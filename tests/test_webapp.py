"""Tests for the web tool: upload safety, the untrusted EPUB build, and an
end-to-end upload -> queue -> download through the Flask app.

    pip install flask pytest && pytest
"""

import io
import shutil
import stat
import time
import zipfile
from pathlib import Path

import pytest

from obsidian_book import BookError, build_epub
from webapp.app import create_app
from webapp.jobs import Limits
from webapp.safezip import UploadError, extract, find_vault_root

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "vault-template"
needs_pandoc = pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c4944415408d763f8cfc000000301010018dd8db00000000049454e44ae426082")


def zip_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def zip_of_dir(d: Path, prefix: str = "My Book") -> bytes:
    return zip_bytes({f"{prefix}/{f.relative_to(d).as_posix()}": f.read_bytes()
                      for f in d.rglob("*") if f.is_file()})


def write(tmp_path, data: bytes) -> Path:
    p = tmp_path / "up.zip"
    p.write_bytes(data)
    return p


# --- safezip ------------------------------------------------------------------

@pytest.mark.parametrize("name", ["../evil.md", "/etc/evil.md", "a/../../evil.md", "C:/evil.md", "..\\evil.md"])
def test_extract_rejects_unsafe_paths(tmp_path, name):
    with pytest.raises(UploadError):
        extract(write(tmp_path, zip_bytes({name: "x"})), tmp_path / "out", max_bytes=10**6, max_files=10)
    assert not (tmp_path / "evil.md").exists()


def test_extract_rejects_symlinks(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(UploadError):
        extract(write(tmp_path, buf.getvalue()), tmp_path / "out", max_bytes=10**6, max_files=10)


def test_extract_caps_real_size_and_count(tmp_path):
    with pytest.raises(UploadError, match="larger than"):
        extract(write(tmp_path, zip_bytes({"big.md": "x" * 5000})), tmp_path / "o1", max_bytes=1000, max_files=10)
    with pytest.raises(UploadError, match="more than"):
        extract(write(tmp_path, zip_bytes({f"{i}.md": "x" for i in range(5)})), tmp_path / "o2",
                max_bytes=10**6, max_files=3)


def test_find_vault_root_one_level_down(tmp_path):
    extract(write(tmp_path, zip_of_dir(TEMPLATE)), tmp_path / "out", max_bytes=10**7, max_files=100)
    assert find_vault_root(tmp_path / "out") == tmp_path / "out" / "My Book"
    with pytest.raises(UploadError, match="Reading Order"):
        find_vault_root(tmp_path / "out" / "My Book" / "Manuscript")


# --- untrusted build ------------------------------------------------------------

def make_vault(tmp_path, cover="cover.png", extra=""):
    v = tmp_path / "vault"
    shutil.copytree(TEMPLATE, v)
    (v / "cover.png").write_bytes(PNG_1PX)
    info = v / "Book Info.md"
    info.write_text(info.read_text().replace('cover: ""', f'cover: "{cover}"'))
    scene = next((v / "Manuscript").rglob("*.md"))
    scene.write_text(scene.read_text() + extra)
    return v


@pytest.mark.parametrize("cover", ["/etc/hostname", "../cover.png"])
def test_untrusted_rejects_cover_outside_vault(tmp_path, cover):
    with pytest.raises(BookError, match="cover"):
        build_epub(make_vault(tmp_path, cover=cover), tmp_path / "out", untrusted=True)


@needs_pandoc
def test_untrusted_build_drops_server_files(tmp_path):
    secret = tmp_path / "secret.png"
    secret.write_bytes(PNG_1PX + b"SECRET-MARKER")
    extra = (f"\n\n![a]({secret}) ![b](../secret.png) ![c](http://example.com/x.png) ![ok](cover.png)\n\n"
             f'<img src="{secret}">\n\n---\ncover-image: {secret}\ncss: {secret}\n---\n\nEnd.\n')
    epub = build_epub(make_vault(tmp_path, extra=extra), tmp_path / "out", untrusted=True)
    with zipfile.ZipFile(epub) as zf:
        names = zf.namelist()
        assert not any(b"SECRET-MARKER" in zf.read(n) for n in names)
        assert any(n.endswith(".css") for n in names)
        assert "EPUB/media/cover.png" in names


# --- app end to end -------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "data", Limits(keep_seconds=60))
    return app.test_client()


def wait_for(client, url, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        page = client.get(url).get_data(as_text=True)
        if "Waiting in line" not in page and "Building your EPUB" not in page:
            return page
        time.sleep(0.2)
    raise AssertionError("job never finished")


def test_home_lists_all_tools(client):
    page = client.get("/").get_data(as_text=True)
    for name in ("Obsidian → EPUB", "Obsidian → PDF", "Obsidian → ODT", "Scrivener → Markdown", "EPUB → Obsidian"):
        assert name in page
    assert page.count("Coming soon") == 4


def test_starter_vault_download(client):
    resp = client.get("/starter-vault.zip")
    names = zipfile.ZipFile(io.BytesIO(resp.data)).namelist()
    assert "My Book/Manuscript Reading Order.md" in names


@needs_pandoc
def test_upload_converts_and_downloads(client):
    resp = client.post("/epub", data={"vault": (io.BytesIO(zip_of_dir(TEMPLATE)), "book.zip")},
                       content_type="multipart/form-data")
    assert resp.status_code == 303
    job_url = resp.headers["Location"]
    page = wait_for(client, job_url)
    assert "Your EPUB is ready" in page, page
    epub = client.get(job_url + "/download")
    assert epub.status_code == 200
    assert zipfile.ZipFile(io.BytesIO(epub.data)).read("mimetype") == b"application/epub+zip"


def test_upload_bad_zip_reports_error(client):
    resp = client.post("/epub", data={"vault": (io.BytesIO(b"not a zip"), "book.zip")},
                       content_type="multipart/form-data")
    page = wait_for(client, resp.headers["Location"])
    assert "isn&#39;t a valid .zip" in page


def test_upload_requires_zip_and_unknown_job_404s(client):
    resp = client.post("/epub", data={"vault": (io.BytesIO(b"x"), "book.txt")},
                       content_type="multipart/form-data")
    assert resp.status_code == 400
    assert client.get("/jobs/" + "0" * 32).status_code == 404
    assert client.get("/jobs/../../etc/passwd").status_code == 404
