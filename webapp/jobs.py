"""In-process job queue: uploads wait here and run one at a time (by default)
in a child process, never inside the web request.

Job state lives on disk in <data_dir>/jobs/<id>/status.json, so a status
page survives a worker thread finishing. The queue itself is in memory, so
the app must run as a single process (gunicorn --workers 1 --threads N).
"""

import json
import os
import queue
import resource
import secrets
import signal
import subprocess
import sys
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from webapp.safezip import remove

JOB_ID_LEN = 32  # hex chars
HISTORY_SECONDS = 24 * 3600
# Seconds a job type takes before there's history to go on (measured on the
# 1-CPU Linode: an EPUB in seconds, a Scrivener import ~10 s, a novel PDF 1-2 min).
FORMAT_SECONDS = {"epub": 10, "pdf": 90, "docx": 10}
DEFAULT_SECONDS = {"import-epub": 15, "import-scrivener": 15}
IMPORT_LABELS = {"import-epub": "EPUB import", "import-scrivener": "Scrivener import"}


def type_label(kind: str) -> str:
    """'epub,pdf' -> 'EPUB + PDF', 'docx' -> 'Word', 'import-epub' -> 'EPUB import'."""
    return IMPORT_LABELS.get(kind) or " + ".join("Word" if f == "docx" else f.upper() for f in kind.split(","))


def default_seconds(kind: str) -> float:
    return DEFAULT_SECONDS.get(kind) or sum(FORMAT_SECONDS.get(f, 60) for f in kind.split(","))


def job_type(st: dict) -> str:
    """'epub', 'pdf', 'epub,pdf', 'import-epub' or 'import-scrivener'."""
    if st.get("source"):
        return f"import-{st['source']}"
    return ",".join(st.get("formats") or ["epub"])


def wait_label(seconds: float) -> str:
    if seconds < 60:
        return "under a minute"
    minutes = round(seconds / 60)
    return f"about {minutes} minute{'' if minutes == 1 else 's'}"


@dataclass
class Limits:
    max_unzipped_bytes: int = 200 * 2**20
    max_files: int = 5000
    cpu_seconds: int = 300   # a long novel's PDF takes a while in WeasyPrint
    wall_seconds: int = 600
    max_output_bytes: int = 200 * 2**20
    max_queued: int = 20
    keep_seconds: int = 3600


class _Typical(dict):
    """Measured median seconds per job type, falling back to estimates."""

    def get(self, kind, default=None):
        return super().get(kind) or default_seconds(kind)


class QueueFull(Exception):
    pass


class JobQueue:
    def __init__(self, data_dir: Path, limits: Limits, workers: int = 1):
        self.jobs_dir = Path(data_dir) / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.limits = limits
        self._queue: queue.Queue = queue.Queue()
        self._running: dict = {}   # job id -> (type, start time)
        self._history: deque = deque()  # (finished time, type, seconds, ok), last 24 h
        self._lock = threading.Lock()
        self.workers = workers
        self._clear_leftovers()
        for _ in range(workers):
            threading.Thread(target=self._work, daemon=True).start()
        threading.Thread(target=self._janitor, daemon=True).start()

    # --- public -----------------------------------------------------------

    def submit(self, formats: list, save_upload, extra: dict | None = None) -> str:
        """save_upload(job_dir) writes the upload into job_dir (upload.zip,
        upload.epub or a vault/ folder). If it raises, the job is discarded.
        extra is stored in the job's status (e.g. {"source": "epub"})."""
        if self._queue.qsize() >= self.limits.max_queued:
            raise QueueFull()
        job_id = secrets.token_hex(JOB_ID_LEN // 2)
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir()
        try:
            save_upload(job_dir)
        except BaseException:
            remove(job_dir)
            raise
        self._write_status(job_id, {**(extra or {}), "state": "queued", "formats": formats,
                                    "created": time.time()})
        self._queue.put(job_id)
        return job_id

    def status(self, job_id: str):
        if not self._valid_id(job_id):
            return None
        try:
            return json.loads((self.jobs_dir / job_id / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def output_path(self, job_id: str, key: str):
        """The built file for a format ("epub", "pdf"), or "cover" for the
        cover thumbnail; None if there isn't one."""
        st = self.status(job_id)
        if not st or st.get("state") not in ("done", "failed"):
            return None
        name = st.get("cover") if key == "cover" else (st.get("files") or {}).get(key)
        if not name:
            return None
        out_dir = (self.jobs_dir / job_id / "out").resolve()
        path = (out_dir / name).resolve()
        return path if path.parent == out_dir and path.is_file() else None

    def position(self, job_id: str) -> int:
        """1-based place in line, or 0 if not waiting."""
        with self._queue.mutex:
            waiting = list(self._queue.queue)
        return waiting.index(job_id) + 1 if job_id in waiting else 0

    def counts(self) -> dict:
        """How many conversions are running now and how many are waiting."""
        with self._lock:
            running = len(self._running)
        return {"running": running, "waiting": self._queue.qsize()}

    def typical_seconds(self) -> dict:
        """Median time per job type over the last 24 hours (defaults until
        there's history)."""
        with self._lock:
            history = list(self._history)
        typical = {kind: statistics.median(h[2] for h in history if h[1] == kind) for kind in {h[1] for h in history}}
        return _Typical(typical)

    def snapshot(self) -> dict:
        """What the status badge and /queue show: counts, an estimated wait
        for a new upload, and a one-line label. No titles or file names."""
        now = time.time()
        typical = self.typical_seconds()
        with self._lock:
            running = [(kind, now - start) for kind, start in self._running.values()]
        with self._queue.mutex:
            waiting_ids = list(self._queue.queue)
        waiting = [job_type(self.status(j) or {}) for j in waiting_ids]
        work = sum(max(typical.get(k, 60) - age, 5) for k, age in running)
        work += sum(typical.get(k, 60) for k in waiting)
        wait = work / max(self.workers, 1)
        if len(waiting) >= self.limits.max_queued:
            level, short = "full", "Full · try again soon"
            label = "Full right now. Try again in a few minutes"
        elif running or waiting:
            level, short = "busy", f"Busy · ~{max(round(wait / 60), 1)} min wait"
            label = (f"Busy: {len(running)} converting, {len(waiting)} waiting. "
                     f"New uploads start in {wait_label(wait)}")
        else:
            level, short, label = "free", "Free right now", "Free right now. Your upload starts right away"
        return {"running": len(running), "waiting": len(waiting), "wait_seconds": round(wait),
                "level": level, "label": label, "short": short,
                "running_jobs": [{"type": type_label(k), "seconds": round(age)} for k, age in running],
                "waiting_jobs": [type_label(k) for k in waiting]}

    def wait_before(self, job_id: str) -> int:
        """Estimated seconds until a waiting job starts."""
        now = time.time()
        typical = self.typical_seconds()
        with self._lock:
            running = [(kind, now - start) for kind, start in self._running.values()]
        with self._queue.mutex:
            waiting_ids = list(self._queue.queue)
        if job_id not in waiting_ids:
            return 0
        ahead = [job_type(self.status(j) or {}) for j in waiting_ids[:waiting_ids.index(job_id)]]
        work = sum(max(typical.get(k, 60) - age, 5) for k, age in running) + sum(typical.get(k, 60) for k in ahead)
        return round(work / max(self.workers, 1))

    def stats(self) -> dict:
        """The last 24 hours: jobs finished per hour (oldest first), and
        per-type counts, failures and typical time."""
        now = time.time()
        with self._lock:
            history = list(self._history)
        hours = []
        for i in range(24, 0, -1):
            start = now - i * 3600
            in_hour = [h for h in history if start <= h[0] < start + 3600]
            hours.append({"start": start, "ok": sum(h[3] for h in in_hour),
                          "failed": sum(not h[3] for h in in_hour)})
        by_type = []
        for kind in sorted({h[1] for h in history}):
            done = [h for h in history if h[1] == kind]
            by_type.append({"type": type_label(kind), "count": len(done), "failed": sum(not h[3] for h in done),
                            "typical": round(statistics.median(h[2] for h in done))})
        return {"hours": hours, "by_type": by_type, "total": len(history),
                "failed": sum(not h[3] for h in history)}

    # --- internals --------------------------------------------------------

    @staticmethod
    def _valid_id(job_id: str) -> bool:
        return len(job_id) == JOB_ID_LEN and all(c in "0123456789abcdef" for c in job_id)

    def _write_status(self, job_id: str, data: dict) -> None:
        path = self.jobs_dir / job_id / "status.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)

    def _limit_child(self) -> None:
        lim = self.limits
        resource.setrlimit(resource.RLIMIT_CPU, (lim.cpu_seconds, lim.cpu_seconds))
        resource.setrlimit(resource.RLIMIT_FSIZE, (lim.max_output_bytes, lim.max_output_bytes))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    def _work(self) -> None:
        while True:
            job_id = self._queue.get()
            kind = job_type(self.status(job_id) or {})
            with self._lock:
                self._running[job_id] = (kind, time.time())
            try:
                self._run(job_id)
            except Exception as e:  # never let one job kill the worker
                self._finish(job_id, {"ok": False, "error": f"Internal error: {e}"})
            finally:
                ok = (self.status(job_id) or {}).get("state") == "done"
                with self._lock:
                    _, start = self._running.pop(job_id)
                    now = time.time()
                    self._history.append((now, kind, now - start, ok))
                    while self._history and self._history[0][0] < now - HISTORY_SECONDS:
                        self._history.popleft()
                self._queue.task_done()

    def _run(self, job_id: str) -> None:
        job_dir = self.jobs_dir / job_id
        st = self.status(job_id) or {}
        self._write_status(job_id, {**st, "state": "running", "started": time.time()})
        lim = self.limits
        cmd = [sys.executable, "-m", "webapp.runner", str(job_dir), ",".join(st.get("formats", ["epub"])),
               str(lim.max_unzipped_bytes), str(lim.max_files)]
        proc = subprocess.Popen(cmd, cwd=job_dir, preexec_fn=self._limit_child,
                                start_new_session=True, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE,
                                env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)})
        try:
            _, stderr = proc.communicate(timeout=lim.wall_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)  # pandoc runs as a grandchild
            proc.communicate()
            self._finish(job_id, {"ok": False, "error": f"The conversion took longer than "
                                                       f"{lim.wall_seconds} seconds and was stopped."})
            return
        try:
            result = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if proc.returncode == -signal.SIGXCPU or proc.returncode == -signal.SIGKILL:
                error = "The conversion used too much CPU time and was stopped."
            elif proc.returncode == -signal.SIGXFSZ:
                error = "The book came out too large and the conversion was stopped."
            else:
                error = "The conversion crashed. " + stderr.decode(errors="replace")[-500:]
            result = {"ok": False, "error": error}
        self._finish(job_id, result)

    def _finish(self, job_id: str, result: dict) -> None:
        job_dir = self.jobs_dir / job_id
        remove(job_dir / "vault")
        remove(job_dir / "built")
        for name in ("upload.zip", "upload.epub", "options.json"):
            (job_dir / name).unlink(missing_ok=True)
        st = self.status(job_id) or {}
        st.update(state="done" if result.get("ok") else "failed", finished=time.time(),
                  error=result.get("error"), log=result.get("log", ""), book=result.get("book"),
                  issues=result.get("issues", []), files=result.get("files", {}),
                  errors=result.get("errors", {}), cover=result.get("cover"))
        self._write_status(job_id, st)

    def _clear_leftovers(self) -> None:
        """Jobs from before a restart can't resume; drop them."""
        for d in self.jobs_dir.iterdir():
            remove(d)

    def _janitor(self) -> None:
        while True:
            time.sleep(60)
            cutoff = time.time() - self.limits.keep_seconds
            for d in self.jobs_dir.iterdir():
                st = self.status(d.name)
                if st is None:
                    stale = d.stat().st_mtime < cutoff  # half-written upload
                else:
                    stale = st["state"] in ("done", "failed") and st.get("finished", 0) < cutoff
                if stale:
                    remove(d)
