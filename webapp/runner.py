"""One conversion, run as a child process with CPU and file-size limits.

    python -m webapp.runner <job_dir> <epub|pdf> <max_unzipped_bytes> <max_files>

Reads <job_dir>/upload.zip (or a folder already saved to <job_dir>/vault),
writes the book to <job_dir>/out/ and the outcome to <job_dir>/result.json.
The vault is deleted before exiting, whatever happens.
"""

import contextlib
import io
import json
import sys
from pathlib import Path

from obsidian_book import BookError, build_epub
from webapp.safezip import UploadError, extract, find_vault_root, remove


def builder(fmt: str):
    if fmt == "pdf":
        from obsidian_book.pdf import build_pdf  # heavy imports, only when needed
        return build_pdf
    return build_epub


def run(job_dir: Path, fmt: str, max_bytes: int, max_files: int) -> dict:
    upload, vault_dir, out_dir = job_dir / "upload.zip", job_dir / "vault", job_dir / "out"
    log = io.StringIO()
    try:
        if upload.exists():
            extract(upload, vault_dir, max_bytes=max_bytes, max_files=max_files)
        vault = find_vault_root(vault_dir)
        with contextlib.redirect_stdout(log):
            book = builder(fmt)(vault, out_dir, untrusted=True)
        return {"ok": True, "file": book.name, "log": log.getvalue()}
    except (UploadError, BookError) as e:
        # Don't show the server's job path in messages.
        msg = str(e).replace(str(vault_dir.resolve()) + "/", "").replace(str(vault_dir.resolve()), "your vault")
        out = log.getvalue().replace(str(vault_dir.resolve()) + "/", "")
        return {"ok": False, "error": msg, "log": out}
    except Exception as e:  # a vault the renderer chokes on; don't show a traceback
        return {"ok": False, "error": f"Something in the vault couldn't be converted "
                                      f"({type(e).__name__}: {str(e)[:300]}).", "log": log.getvalue()}
    finally:
        remove(vault_dir)
        upload.unlink(missing_ok=True)


def main() -> None:
    job_dir = Path(sys.argv[1])
    result = run(job_dir, sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
    (job_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
