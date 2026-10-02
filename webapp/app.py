"""authortools.hyrumjones.com: a home page listing every tool, plus the
Obsidian -> EPUB and Obsidian -> PDF converters.

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


# The converters a vault can be uploaded to. "what" finishes "Make my ...".
CONVERTERS = {
    "epub": {"name": "Obsidian → EPUB", "what": "EPUB", "mimetype": "application/epub+zip",
             "blurb": "Upload your book vault and get back an EPUB e-book."},
    "pdf": {"name": "Obsidian → PDF", "what": "PDF", "mimetype": "application/pdf",
            "blurb": "Upload your book vault and get back a print-style PDF, "
                     "with part pages, drop caps and running headers."},
}


def tools() -> list:
    return [
        {"slug": "epub", "name": "Obsidian → EPUB", "live": True,
         "desc": "Turn your Obsidian book vault into an e-book for Kindle, Apple Books, Kobo and other readers."},
        {"slug": "pdf", "name": "Obsidian → PDF", "live": pdf_available(),
         "desc": "A print-ready PDF with part pages, drop caps and running headers."},
        {"slug": "odt", "name": "Obsidian → ODT", "live": False,
         "desc": "An editable manuscript you can open in LibreOffice or Word."},
        {"slug": "scrivener", "name": "Scrivener → Markdown", "live": False,
         "desc": "Move a Scrivener 3 project into plain Markdown files (and a Manuskript project)."},
        {"slug": "epub-import", "name": "EPUB → Obsidian", "live": False,
         "desc": "Pull a published e-book back into a vault you can keep writing in."},
    ]


def create_app(data_dir=None, limits: Limits | None = None, workers=None) -> Flask:
    app = Flask(__name__)
    max_mb = int(os.environ.get("AUTHORTOOLS_MAX_UPLOAD_MB", "50"))
    app.config["MAX_CONTENT_LENGTH"] = max_mb * 2**20
    app.config["MAX_FORM_PARTS"] = MAX_FOLDER_FILES + 10  # a picked folder sends one part per file
    data_dir = Path(data_dir or os.environ.get("AUTHORTOOLS_DATA_DIR", "authortools-data"))
    jobs = JobQueue(data_dir, limits or Limits(),
                    workers=workers or int(os.environ.get("AUTHORTOOLS_WORKERS", "1")))
    app.extensions["jobs"] = jobs
    starter_zip = {}

    def converter(fmt):
        if fmt not in CONVERTERS or (fmt == "pdf" and not pdf_available()):
            abort(404)
        return CONVERTERS[fmt]

    def form(fmt, error=None, status=200):
        return render_template("convert.html", fmt=fmt, tool=converter(fmt), max_mb=max_mb,
                               error=error), status

    @app.get("/")
    def home():
        return render_template("home.html", tools=tools())

    @app.get("/<any(epub, pdf):fmt>")
    def convert_form(fmt):
        return form(fmt)

    @app.post("/<any(epub, pdf):fmt>")
    def convert_upload(fmt):
        converter(fmt)
        folder = [f for f in request.files.getlist("files") if f.filename]
        upload = request.files.get("vault")
        if folder:
            def save(job_dir):
                save_files(folder, job_dir / "vault", max_bytes=jobs.limits.max_unzipped_bytes,
                           max_files=MAX_FOLDER_FILES)
        elif upload and upload.filename:
            if not upload.filename.lower().endswith(".zip"):
                return form(fmt, "Upload your vault as a .zip file, or pick the vault folder.", 400)
            def save(job_dir):
                upload.save(job_dir / "upload.zip")
        else:
            return form(fmt, "Pick your vault folder (or a .zip of it) first.", 400)
        try:
            job_id = jobs.submit(fmt, save)
        except UploadError as e:
            return form(fmt, str(e), 400)
        except QueueFull:
            return form(fmt, "The converter is busy right now. Try again in a few minutes.", 503)
        return redirect(url_for("job_page", job_id=job_id), code=303)

    @app.get("/jobs/<job_id>")
    def job_page(job_id):
        st = jobs.status(job_id)
        if st is None:
            abort(404)
        fmt = st.get("format", "epub")
        return render_template("job.html", job_id=job_id, st=st, fmt=fmt, tool=CONVERTERS[fmt],
                               position=jobs.position(job_id),
                               keep_minutes=jobs.limits.keep_seconds // 60)

    @app.get("/jobs/<job_id>/download")
    def job_download(job_id):
        path = jobs.output_path(job_id)
        if path is None:
            abort(404)
        fmt = jobs.status(job_id).get("format", "epub")
        return send_file(path, mimetype=CONVERTERS[fmt]["mimetype"], as_attachment=True,
                         download_name=path.name)

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

    @app.get("/healthz")
    def healthz():
        return "ok"

    @app.errorhandler(413)
    def too_large(_):
        fmt = request.path.strip("/") if request.path.strip("/") in CONVERTERS else "epub"
        return form(fmt, f"That upload is over the {max_mb} MB limit (or has too many files).", 413)

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
