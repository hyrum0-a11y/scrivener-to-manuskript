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
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from webapp.safezip import remove

JOB_ID_LEN = 32  # hex chars


@dataclass
class Limits:
    max_unzipped_bytes: int = 200 * 2**20
    max_files: int = 5000
    cpu_seconds: int = 300   # a long novel's PDF takes a while in WeasyPrint
    wall_seconds: int = 600
    max_output_bytes: int = 200 * 2**20
    max_queued: int = 20
    keep_seconds: int = 3600


class QueueFull(Exception):
    pass


class JobQueue:
    def __init__(self, data_dir: Path, limits: Limits, workers: int = 1):
        self.jobs_dir = Path(data_dir) / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.limits = limits
        self._queue: queue.Queue = queue.Queue()
        self._running = 0
        self._running_lock = threading.Lock()
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
        with self._running_lock:
            running = self._running
        return {"running": running, "waiting": self._queue.qsize()}

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
            with self._running_lock:
                self._running += 1
            try:
                self._run(job_id)
            except Exception as e:  # never let one job kill the worker
                self._finish(job_id, {"ok": False, "error": f"Internal error: {e}"})
            finally:
                with self._running_lock:
                    self._running -= 1
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
