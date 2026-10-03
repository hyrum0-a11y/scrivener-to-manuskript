"""authortools.hyrumjones.com: a home page listing every tool, plus one
upload page that converts an Obsidian vault to EPUB and/or PDF.

Run locally:   flask --app 'webapp.app:create_app()' run
In production: gunicorn 'webapp.app:create_app()', one process; see deploy/DEPLOY.md.

Settings come from environment variables (defaults in brackets):
  AUTHORTOOLS_DATA_DIR   where job folders live [./authortools-data]
  AUTHORTOOLS_MAX_UPLOAD_MB  largest upload accepted [50]
  AUTHORTOOLS_WORKERS    conversions run at once [1]
"""

import importlib.util
import io
import os
import zipfile
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, send_file, url_for

from webapp.jobs import JobQueue, Limits, QueueFull
from webapp.safezip import UploadError, save_files

REPO_ROOT = Path(__file__).resolve().parent.parent
VAULT_TEMPLATE = REPO_ROOT / "vault-template"
MAX_FOLDER_FILES = 5000


def pdf_available() -> bool:
    return all(importlib.util.find_spec(m) for m in ("weasyprint", "pymupdf"))


# Output formats a vault can be converted to, in the order they're offered.
FORMATS = {
    "epub": {"label": "EPUB e-book", "hint": "for Kindle, Apple Books, Kobo and other readers",
             "mimetype": "application/epub+zip"},
    "pdf": {"label": "Print PDF", "hint": "with part pages, drop caps and running headers",
            "mimetype": "application/pdf"},
}
COVER_MIMETYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                   ".gif": "image/gif", ".webp": "image/webp"}


def available_formats() -> list:
    return [f for f in FORMATS if f != "pdf" or pdf_available()]


def tool_groups() -> list:
    convert = lambda fmt: url_for("convert_form", fmt=fmt)
    return [
        {"title": "Start here", "tools": [
            {"name": "Starter vault", "href": url_for("starter_vault"), "badge": "Download",
             "desc": "A ready-made Obsidian vault set up for these tools. Unzip it, open it in "
                     "Obsidian and start writing."},
        ]},
        {"title": "Convert your Obsidian book", "tools": [
            {"name": "Obsidian → EPUB", "href": convert("epub"),
             "desc": "Turn your Obsidian book vault into an e-book for Kindle, Apple Books, Kobo and other readers."},
            {"name": "Obsidian → PDF", "href": convert("pdf") if pdf_available() else None,
             "desc": "A print-ready PDF with part pages, drop caps and running headers."},
            {"name": "Obsidian → ODT", "href": None,
             "desc": "An editable manuscript you can open in LibreOffice or Word."},
        ]},
        {"title": "Bring your work in", "tools": [
            {"name": "Scrivener → Markdown", "href": None,
             "desc": "Move a Scrivener 3 project into plain Markdown files (and a Manuskript project)."},
            {"name": "EPUB → Obsidian", "href": None,
             "desc": "Pull a published e-book back into a vault you can keep writing in."},
        ]},
    ]


def create_app(data_dir=None, limits: Limits | None = None, workers=None) -> Flask:
    app = Flask(__name__)
    max_mb = int(os.environ.get("AUTHORTOOLS_MAX_UPLOAD_MB", "50"))
    app.config["MAX_CONTENT_LENGTH"] = max_mb * 2**20
    app.config["MAX_FORM_PARTS"] = MAX_FOLDER_FILES + 20  # a picked folder sends one part per file
    data_dir = Path(data_dir or os.environ.get("AUTHORTOOLS_DATA_DIR", "authortools-data"))
    jobs = JobQueue(data_dir, limits or Limits(),
                    workers=workers or int(os.environ.get("AUTHORTOOLS_WORKERS", "1")))
    app.extensions["jobs"] = jobs
    starter_zip = {}

    def form(selected=None, error=None, status=200):
        offered = available_formats()
        selected = [f for f in (selected or ["epub"]) if f in offered] or ["epub"]
        return render_template("convert.html", formats={f: FORMATS[f] for f in offered},
                               selected=selected, max_mb=max_mb, error=error,
                               queue=jobs.counts()), status

    @app.get("/")
    def home():
        return render_template("home.html", groups=tool_groups())

    @app.get("/convert")
    def convert_form():
        return form(request.args.getlist("fmt"))

    # Old single-format addresses keep working.
    @app.get("/<any(epub, pdf):fmt>")
    def old_form(fmt):
        return redirect(url_for("convert_form", fmt=fmt), code=301)

    @app.post("/convert")
    def convert_upload():
        formats = [f for f in available_formats() if f in request.form.getlist("formats")]
        if not formats:
            return form(error="Tick at least one format to make.", status=400)
        folder = [f for f in request.files.getlist("files") if f.filename]
        upload = request.files.get("vault")
        if folder:
            def save(job_dir):
                save_files(folder, job_dir / "vault", max_bytes=jobs.limits.max_unzipped_bytes,
                           max_files=MAX_FOLDER_FILES)
        elif upload and upload.filename:
            if not upload.filename.lower().endswith(".zip"):
                return form(formats, "Upload your vault as a .zip file, or pick the vault folder.", 400)
            def save(job_dir):
                upload.save(job_dir / "upload.zip")
        else:
            return form(formats, "Pick your vault folder (or a .zip of it) first.", 400)
        try:
            job_id = jobs.submit(formats, save)
        except UploadError as e:
            return form(formats, str(e), 400)
        except QueueFull:
            return form(formats, "The converter is busy right now. Try again in a few minutes.", 503)
        return redirect(url_for("job_page", job_id=job_id), code=303)

    @app.get("/jobs/<job_id>")
    def job_page(job_id):
        st = jobs.status(job_id)
        if st is None:
            abort(404)
        return render_template("job.html", job_id=job_id, st=st, formats=FORMATS,
                               position=jobs.position(job_id), queue=jobs.counts(),
                               keep_minutes=jobs.limits.keep_seconds // 60)

    @app.get("/jobs/<job_id>/<any(epub, pdf):fmt>")
    def job_download(job_id, fmt):
        path = jobs.output_path(job_id, fmt)
        if path is None:
            abort(404)
        return send_file(path, mimetype=FORMATS[fmt]["mimetype"], as_attachment=True,
                         download_name=path.name)

    @app.get("/jobs/<job_id>/cover")
    def job_cover(job_id):
        path = jobs.output_path(job_id, "cover")
        if path is None or path.suffix not in COVER_MIMETYPES:
            abort(404)
        return send_file(path, mimetype=COVER_MIMETYPES[path.suffix])

    @app.get("/starter-vault.zip")
    def starter_vault():
        if "data" not in starter_zip:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in sorted(VAULT_TEMPLATE.rglob("*")):
                    if f.is_file():
                        zf.write(f, Path("My Book") / f.relative_to(VAULT_TEMPLATE))
            starter_zip["data"] = buf.getvalue()
        return send_file(io.BytesIO(starter_zip["data"]), mimetype="application/zip",
                         as_attachment=True, download_name="starter-vault.zip")

    @app.get("/queue")
    def queue_counts():
        return jobs.counts()

    @app.get("/healthz")
    def healthz():
        return "ok"

    @app.errorhandler(413)
    def too_large(_):
        return form(error=f"That upload is over the {max_mb} MB limit (or has too many files).", status=413)

    @app.errorhandler(404)
    def not_found(_):
        return render_template("message.html", title="Not found",
                               text="That page doesn't exist, or the file has already been deleted."), 404

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        return resp

    return app
