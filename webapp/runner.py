"""One conversion job, run as a child process with CPU and file-size limits.

    python -m webapp.runner <job_dir> <epub,pdf | vault> <max_unzipped_bytes> <max_files>

Converting: reads <job_dir>/upload.zip (or a folder already saved to
<job_dir>/vault), checks the vault, then builds each requested format into
<job_dir>/out/.

Importing ("vault"): <job_dir>/options.json says {"source": "epub" |
"scrivener", "title", "author"}. Turns upload.epub, or the Scrivener project
in upload.zip / vault/, into a vault, checks it the same way, and zips it
into <job_dir>/out/.
The outcome goes to <job_dir>/result.json:

    {"ok": bool, "error": str|None, "book": {...}, "issues": [...],
     "files": {"epub": name}, "errors": {"pdf": message}, "cover": name|None}

Uploads and working folders are deleted before exiting, whatever happens.
"""

import contextlib
import io
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

from obsidian_book import BookError, build_epub
from obsidian_book.epub import (check_vault, index_vault_files, load_vault, parse_reading_order,
                                resolve_cover, strip_frontmatter)
from obsidian_book.importer import safe_name, write_vault
from webapp.safezip import UploadError, extract, find_vault_root, remove

COVER_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}  # not .svg: it can carry script
MAX_COVER_BYTES = 10 * 2**20


def builder(fmt: str):
    if fmt == "pdf":
        from obsidian_book.pdf import build_pdf  # heavy imports, only when needed
        return build_pdf
    if fmt == "docx":
        from obsidian_book.docx import build_docx
        return build_docx
    return build_epub


def parse_issues(text: str) -> list:
    """Turn check_vault()'s printed ERROR:/WARNING: blocks into
    [{"level", "text", "items"}] for the results page."""
    issues = []
    for line in text.splitlines():
        m = re.match(r"(ERROR|WARNING): (.*)", line)
        if m:
            issues.append({"level": m[1].lower(), "text": m[2], "items": []})
        elif issues and line.strip():
            item = line.strip()
            issues[-1]["items"].append(item[2:] if item.startswith("- ") else item)
    return issues


def book_details(vault: Path, book_info: dict) -> dict:
    _, parts, _ = parse_reading_order(vault / "Manuscript Reading Order.md")
    files = index_vault_files(vault)
    scenes = [s for p in parts for c in p.chapters for s in c.scenes]
    words = sum(len(strip_frontmatter(files[s].read_text(encoding="utf-8"))[1].split())
                for s in scenes if s in files)
    return {"title": book_info["title"], "author": book_info["author"],
            "parts": sum(not p.implicit for p in parts), "chapters": sum(len(p.chapters) for p in parts),
            "scenes": len(scenes), "words": words}


def copy_cover(vault: Path, book_info: dict, out_dir: Path):
    """Copy the cover (if it's an ordinary image inside the vault) for the
    results page thumbnail. Returns its file name or None."""
    cover = resolve_cover(vault, book_info)
    if (not cover or not cover.is_relative_to(vault) or not cover.is_file()
            or cover.suffix.lower() not in COVER_TYPES or cover.stat().st_size > MAX_COVER_BYTES):
        return None
    name = "cover" + cover.suffix.lower()
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cover, out_dir / name)
    return name


def zip_vault(vault: Path, dest: Path) -> None:
    """Zip the vault folder (with the folder itself as the zip's top level)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(vault.rglob("*")):
            if f.is_file():
                zf.write(f, Path(vault.name) / f.relative_to(vault))


def run_import(job_dir: Path, max_bytes: int, max_files: int) -> dict:
    upload, src_dir, built, out_dir = (job_dir / "upload.zip", job_dir / "vault", job_dir / "built",
                                       job_dir / "out")
    epub_upload = job_dir / "upload.epub"
    paths = [str(p.resolve()) for p in (src_dir, built, epub_upload)]

    def scrub(text: str) -> str:  # don't show the server's job paths
        for path in paths:
            text = text.replace(path + "/", "").replace(path, "your upload")
        return text

    result = {"ok": False, "error": None, "book": None, "issues": [], "files": {}, "errors": {},
              "cover": None, "log": ""}
    try:
        opts = json.loads((job_dir / "options.json").read_text(encoding="utf-8"))
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            if opts.get("source") == "epub":
                from obsidian_book.from_epub import build_book
                book = build_book(epub_upload, max_bytes)
            else:
                from obsidian_book.from_scrivener import build_book, find_scrivx
                if upload.exists():
                    extract(upload, src_dir, max_bytes=max_bytes, max_files=max_files)
                book = build_book(find_scrivx(src_dir), opts.get("title", ""), opts.get("author", ""))
            vault = write_vault(book, built / (safe_name(book.title, 60) or "My Book"))
        vault, book_info = load_vault(vault)
        report = io.StringIO()
        with contextlib.redirect_stdout(report):
            check_vault(vault, book_info)
        result["issues"] = parse_issues(scrub(report.getvalue()))
        result["book"] = book_details(vault, book_info)
        result["cover"] = copy_cover(vault, book_info, out_dir)
        name = f"{vault.name} (Obsidian vault).zip"
        zip_vault(vault, out_dir / name)
        result["files"]["vault"] = name
        result["log"] = scrub(log.getvalue())
        result["ok"] = True
        return result
    except (UploadError, BookError) as e:
        result["error"] = scrub(str(e))
        return result
    except Exception as e:
        result["error"] = (f"Something in the upload couldn't be read "
                           f"({type(e).__name__}: {scrub(str(e))[:300]}).")
        return result
    finally:
        remove(src_dir)
        remove(built)
        upload.unlink(missing_ok=True)
        epub_upload.unlink(missing_ok=True)


def run(job_dir: Path, formats: list, max_bytes: int, max_files: int) -> dict:
    upload, vault_dir, out_dir = job_dir / "upload.zip", job_dir / "vault", job_dir / "out"
    vault_path = str(vault_dir.resolve())

    def scrub(text: str) -> str:  # don't show the server's job path
        return text.replace(vault_path + "/", "").replace(vault_path, "your vault")

    result = {"ok": False, "error": None, "book": None, "issues": [], "files": {}, "errors": {},
              "cover": None, "log": ""}
    try:
        if upload.exists():
            extract(upload, vault_dir, max_bytes=max_bytes, max_files=max_files)
        vault, book_info = load_vault(find_vault_root(vault_dir))

        report = io.StringIO()
        with contextlib.redirect_stdout(report):
            errors = check_vault(vault, book_info)
        result["issues"] = parse_issues(scrub(report.getvalue()))
        result["book"] = book_details(vault, book_info)
        result["cover"] = copy_cover(vault, book_info, out_dir)
        if errors:
            result["error"] = (f"The book check found {errors} problem{'' if errors == 1 else 's'}, "
                               f"listed below. Fix {'it' if errors == 1 else 'them'} and try again.")
            return result

        log = io.StringIO()
        for fmt in formats:
            try:
                with contextlib.redirect_stdout(log):
                    result["files"][fmt] = builder(fmt)(vault, out_dir, untrusted=True).name
            except BookError as e:
                result["errors"][fmt] = scrub(str(e))
            except Exception as e:  # a vault the renderer chokes on; no traceback
                result["errors"][fmt] = (f"Something in the vault couldn't be converted "
                                         f"({type(e).__name__}: {scrub(str(e))[:300]}).")
        result["log"] = scrub(log.getvalue())
        result["ok"] = bool(result["files"])
        if not result["ok"]:
            result["error"] = "The conversion failed. Details are below."
        return result
    except (UploadError, BookError) as e:
        result["error"] = scrub(str(e))
        return result
    except Exception as e:
        result["error"] = (f"Something in the vault couldn't be read "
                           f"({type(e).__name__}: {scrub(str(e))[:300]}).")
        return result
    finally:
        remove(vault_dir)
        upload.unlink(missing_ok=True)


def main() -> None:
    job_dir = Path(sys.argv[1])
    formats, max_bytes, max_files = sys.argv[2].split(","), int(sys.argv[3]), int(sys.argv[4])
    result = (run_import(job_dir, max_bytes, max_files) if formats == ["vault"]
              else run(job_dir, formats, max_bytes, max_files))
    (job_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
