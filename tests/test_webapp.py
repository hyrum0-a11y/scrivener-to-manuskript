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
from webapp.app import create_app, pdf_available
from webapp.jobs import Limits
from webapp.safezip import UploadError, extract, find_vault_root

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "vault-template"
needs_pandoc = pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
needs_pdf = pytest.mark.skipif(not pdf_available(), reason="weasyprint/pymupdf not installed")

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
    info.write_text(info.read_text().replace('cover: "cover.jpg"', f'cover: "{cover}"'))
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
        # pandoc 3.1 keeps the name cover.png; newer releases rename it file0.png.
        assert any(n.startswith("EPUB/media/") and zf.read(n) == PNG_1PX for n in names)


def make_partless_vault(tmp_path):
    """The starter vault, which has no Part heading: chapters sit directly
    under the book, as in a novel with no part divisions."""
    return make_vault(tmp_path)


def test_reading_order_without_parts_keeps_chapters(tmp_path):
    from obsidian_book.epub import parse_reading_order
    _, parts, _ = parse_reading_order(make_partless_vault(tmp_path) / "Manuscript Reading Order.md")
    assert len(parts) == 1 and parts[0].implicit
    assert [c.title for c in parts[0].chapters] == ["Chapter 1"]
    assert parts[0].chapters[0].scenes == ["01 - Opening Scene"]


@needs_pandoc
def test_epub_without_parts_has_no_part_page(tmp_path):
    epub = build_epub(make_partless_vault(tmp_path), tmp_path / "out", untrusted=True)
    with zipfile.ZipFile(epub) as zf:
        nav = zf.read("EPUB/nav.xhtml").decode()
        text = b"".join(zf.read(n) for n in zf.namelist() if n.endswith(".xhtml")).decode()
    assert "Chapter 1" in nav
    assert "PART I" not in text and "Opening Scene" not in nav


@needs_pdf
def test_pdf_without_parts_lists_chapters(tmp_path):
    import pymupdf
    from obsidian_book.pdf import build_pdf
    pdf = build_pdf(make_partless_vault(tmp_path), tmp_path / "out", untrusted=True)
    text = "".join(page.get_text() for page in pymupdf.open(pdf))
    assert "Chapter 1—1" in text and "PART I" not in text


def write_order(tmp_path, body: str) -> Path:
    path = tmp_path / "Manuscript Reading Order.md"
    path.write_text("# Book — Reading Order\n\n" + body)
    return path


def test_labelled_lines_under_parts_and_chapters(tmp_path):
    from obsidian_book.epub import parse_reading_order
    _, parts, _ = parse_reading_order(write_order(tmp_path, (
        "# Part I The Start\ncenter: *A poem line*\nleft: Plain\n> old epigraph\n\n"
        "## The Storm\ncenter: **Anna**\nleft: London, 1952\nneeds a rewrite\n%%a note%%\n- [[s1]]\n")))
    part, chapter = parts[0], parts[0].chapters[0]
    assert part.lines == [("center", "*A poem line*"), ("left", "Plain")] and part.ignored == ["> old epigraph"]
    assert chapter.title == "The Storm" and chapter.pov == "Anna"
    assert chapter.lines == [("center", "**Anna**"), ("left", "London, 1952")]
    assert chapter.ignored == ["needs a rewrite"] and chapter.scenes == ["s1"]


def test_old_unlabelled_reading_order_still_reads_the_old_way(tmp_path):
    from obsidian_book.epub import parse_reading_order
    _, parts, _ = parse_reading_order(write_order(tmp_path, (
        "# Part I Momentum\n> After the fire\n\n## One\nGerald\nJanuary 2009\n- [[a]]\n\n## Two\nGerald\n- [[b]]\n")))
    assert parts[0].lines == [("center", "*After the fire*")]
    assert parts[0].chapters[0].lines == [("center", "**Gerald**"), ("left", "January 2009")]


def test_stray_note_in_a_new_book_is_not_printed(tmp_path):
    from obsidian_book.epub import parse_reading_order
    _, parts, _ = parse_reading_order(write_order(tmp_path, "## Chapter 1\nneeds a rewrite\n- [[a]]\n\n## Chapter 2\n- [[b]]\n"))
    assert parts[0].chapters[0].lines == [] and parts[0].chapters[0].ignored == ["needs a rewrite"]


def test_check_lists_ignored_lines(tmp_path, capsys):
    from obsidian_book.epub import check_vault, load_vault
    vault = make_vault(tmp_path)
    order = vault / "Manuscript Reading Order.md"
    order.write_text(order.read_text().replace("## Chapter 1\n", "## Chapter 1\ncenter: **Anna**\nfix this\n"))
    check_vault(*load_vault(vault))
    out = capsys.readouterr().out
    assert "aren't printed" in out and "under Chapter 1: fix this" in out


# --- app end to end -------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "data", Limits(keep_seconds=60))
    return app.test_client()


def wait_for(client, url, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        page = client.get(url).get_data(as_text=True)
        if "Waiting in line" not in page and "Checking and building" not in page:
            return page
        time.sleep(0.2)
    raise AssertionError("job never finished")


def post(client, formats, **files):
    return client.post("/convert", data={"formats": formats, **files}, content_type="multipart/form-data")


def folder_parts(d: Path, prefix: str = "My Book") -> list:
    """The multipart fields the folder picker's script sends."""
    return [(io.BytesIO(f.read_bytes()), f"{prefix}/{f.relative_to(d).as_posix()}")
            for f in d.rglob("*") if f.is_file()]


def test_home_lists_all_tools_in_groups(client):
    page = client.get("/").get_data(as_text=True)
    for name in ("Starter vault", 'href="/starter-vault.zip"', "Obsidian → EPUB", "Obsidian → PDF", "Obsidian → Word", "Scrivener → Obsidian", "EPUB → Obsidian",
                 'href="/import/scrivener"', 'href="/import/epub"',
                 "Convert your Obsidian book", "Bring your work in", "Pick your vault"):
        assert name in page
    assert page.count("Coming soon") == (0 if pdf_available() else 1)


def test_old_addresses_redirect_and_preselect(client):
    resp = client.get("/epub")
    assert resp.status_code == 301 and resp.headers["Location"].endswith("/convert?fmt=epub")
    page = client.get("/convert?fmt=epub").get_data(as_text=True)
    assert 'value="epub" checked' in page


def test_starter_vault_download(client):
    resp = client.get("/starter-vault.zip")
    names = zipfile.ZipFile(io.BytesIO(resp.data)).namelist()
    assert "My Book/Manuscript Reading Order.md" in names


@needs_pandoc
def test_zip_upload_converts_and_shows_book(client, tmp_path):
    vault = make_vault(tmp_path)
    resp = post(client, ["epub"], vault=(io.BytesIO(zip_of_dir(vault)), "book.zip"))
    assert resp.status_code == 303
    job_url = resp.headers["Location"]
    page = wait_for(client, job_url)
    assert "Download EPUB" in page, page
    assert "My Novel" in page and "Your Name" in page and "1 chapter" in page
    assert "Worth a look" in page and "[Your Name]" in page   # starter text reminder, not a blocker
    epub = client.get(job_url + "/epub")
    assert zipfile.ZipFile(io.BytesIO(epub.data)).read("mimetype") == b"application/epub+zip"
    cover = client.get(job_url + "/cover")
    assert cover.mimetype == "image/png" and cover.data == PNG_1PX
    assert client.get(job_url + "/pdf").status_code == 404


@needs_pandoc
def test_check_lists_broken_links_and_stops(client, tmp_path):
    vault = make_vault(tmp_path)
    order = vault / "Manuscript Reading Order.md"
    order.write_text(order.read_text() + "\n- [[Missing Scene]]\n")
    page = wait_for(client, post(client, ["epub"], files=folder_parts(vault)).headers["Location"])
    assert "1 problem" in page and "Missing Scene" in page and "Must fix" in page
    assert "Download EPUB" not in page


@needs_pandoc
def test_folder_upload_converts(client):
    resp = post(client, ["epub"], files=folder_parts(TEMPLATE))
    assert resp.status_code == 303
    assert "Download EPUB" in wait_for(client, resp.headers["Location"])


@pytest.mark.parametrize("name", ["../evil.md", "/tmp/evil.md", "My Book/../../evil.md"])
def test_folder_upload_rejects_unsafe_paths(client, name):
    resp = post(client, ["epub"], files=[(io.BytesIO(b"x"), name)])
    assert resp.status_code == 400
    assert "unsafe path" in resp.get_data(as_text=True)


@needs_pandoc
@needs_pdf
def test_both_formats_in_one_job(client):
    job_url = post(client, ["epub", "pdf"], files=folder_parts(TEMPLATE)).headers["Location"]
    page = wait_for(client, job_url, timeout=120)
    assert "Download EPUB" in page and "Download PDF" in page, page
    pdf = client.get(job_url + "/pdf")
    assert pdf.mimetype == "application/pdf" and pdf.data.startswith(b"%PDF")


@needs_pdf
def test_pdf_fetcher_only_reads_inside_vault(tmp_path):
    import pymupdf
    import weasyprint
    from obsidian_book.pdf import _vault_only_fetcher
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "in.png").write_bytes(PNG_1PX)
    (tmp_path / "out.png").write_bytes(PNG_1PX)
    html = (f'<img src="{(vault / "in.png").as_uri()}"><img src="{(tmp_path / "out.png").as_uri()}">'
            f'<img src="http://example.com/x.png">')
    pdf = weasyprint.HTML(string=html, base_url=str(vault),
                          url_fetcher=_vault_only_fetcher(vault)).write_pdf()
    assert len(pymupdf.open(stream=pdf, filetype="pdf")[0].get_images()) == 1


@needs_pdf
@pytest.mark.parametrize("field", ['pov_signs_dir: "/etc"', 'pov_signs_dir: "../x"', 'trim_size: "5in; } x {"'])
def test_untrusted_pdf_rejects_bad_settings(tmp_path, field):
    from obsidian_book.pdf import build_pdf
    v = make_vault(tmp_path)
    info = v / "Book Info.md"
    key = field.split(":")[0]
    info.write_text(info.read_text().replace(f'{key}: ""', field))
    with pytest.raises(BookError, match=key):
        build_pdf(v, tmp_path / "out", untrusted=True)


def test_upload_bad_zip_reports_error(client):
    page = wait_for(client, post(client, ["epub"], vault=(io.BytesIO(b"not a zip"), "book.zip")).headers["Location"])
    assert "isn&#39;t a valid .zip" in page


def test_upload_needs_format_and_zip_and_unknown_job_404s(client):
    assert post(client, [], vault=(io.BytesIO(b"x"), "book.zip")).status_code == 400
    assert post(client, ["epub"], vault=(io.BytesIO(b"x"), "book.txt")).status_code == 400
    assert client.get("/jobs/" + "0" * 32).status_code == 404
    assert client.get("/jobs/../../etc/passwd").status_code == 404


@needs_pandoc
def test_status_badge_queue_and_history(client):
    q = client.get("/queue").get_json()
    assert (q["running"], q["waiting"], q["level"], q["short"]) == (0, 0, "free", "Converter: no wait")
    for page in ("/", "/convert", "/import/epub"):
        html_ = client.get(page).get_data(as_text=True)
        assert "Converter: no wait" in html_ and 'href="/status"' in html_ and "status.js" in html_
    assert "No jobs in the last 24 hours" in client.get("/status").get_data(as_text=True)

    resp = post(client, ["epub"], vault=(io.BytesIO(zip_of_dir(TEMPLATE)), "v.zip"))
    wait_for(client, resp.headers["Location"])
    assert client.get("/queue").get_json()["level"] == "free"
    page = client.get("/status").get_data(as_text=True)
    assert "1 job finished" in page and "Jobs finished per hour" in page and "<svg" in page
    assert "1 job in the last hour" in page          # bar tooltip and table row
    assert "<td>EPUB</td><td>1</td><td>0</td>" in page
    assert "My Novel" not in page and "Your Name" not in page   # no titles or names


def test_wait_estimate_and_labels(tmp_path):
    from webapp.jobs import JobQueue, wait_label
    q = JobQueue(tmp_path / "data", Limits(max_queued=2), workers=0)  # no workers: jobs stay queued
    ids = [q.submit(f, lambda d: None) for f in (["pdf"], ["epub"])]
    snap = q.snapshot()
    assert (snap["waiting"], snap["level"]) == (2, "full")
    assert snap["waiting_jobs"] == ["PDF", "EPUB"] and snap["wait_seconds"] == 100
    assert q.wait_before(ids[0]) == 0 and q.wait_before(ids[1]) == 90
    assert [wait_label(s) for s in (20, 90, 300)] == ["under a minute", "about 2 minutes", "about 5 minutes"]


def test_hour_chart_geometry():
    from webapp.app import hour_chart
    hours = [{"ok": 0, "failed": 0}] * 22 + [{"ok": 3, "failed": 1}, {"ok": 2, "failed": 0}]
    chart = hour_chart(hours)
    assert chart["top"] == 4 and len(chart["bars"]) == 24
    assert chart["bars"][0]["path"] == "" and chart["bars"][-1]["tip"] == "2 jobs in the last hour"
    assert chart["bars"][-2]["tip"] == "4 jobs 1 to 2 hours ago (1 failed)"


# --- starter vault and Book Info rules ------------------------------------------

def test_starter_vault_reads_both_book_info_blocks():
    from obsidian_book.epub import parse_book_info
    info = parse_book_info(TEMPLATE)
    assert info["cover"] == "cover.jpg" and info["output_dir"] == ""   # blank: Downloads, for the CLI
    assert "trim_size" in info and "isbn" in info          # from the Optional block
    assert (TEMPLATE / "cover.jpg").is_file() and (TEMPLATE / "Start Here.md").is_file()


def test_cover_is_required(tmp_path):
    from obsidian_book.epub import check_vault, load_vault
    vault = make_vault(tmp_path, cover="")
    with pytest.raises(BookError, match="Every book needs a cover"):
        load_vault(vault)
    shutil.rmtree(vault)
    vault, info = load_vault(make_vault(tmp_path, cover="missing.jpg"))
    assert check_vault(vault, info) == 1


def test_blank_output_dir_means_downloads():
    from obsidian_book.epub import resolve_output_dir
    assert resolve_output_dir({"output_dir": ""}) == (Path.home() / "Downloads").resolve()
    assert resolve_output_dir({"output_dir": "~/Books"}, "/tmp/x") == Path("/tmp/x").resolve()


def test_starter_vault_download_has_cover_and_guide(client):
    names = zipfile.ZipFile(io.BytesIO(client.get("/starter-vault.zip").data)).namelist()
    assert "My Book/cover.jpg" in names and "My Book/Start Here.md" in names
    assert "My Book/README.md" not in names


def test_information_page_printed_as_written(tmp_path):
    from obsidian_book.epub import build_document, load_vault
    vault = make_vault(tmp_path)
    info = vault / "Book Info.md"
    info.write_text(info.read_text().replace('isbn: ""', 'isbn: "9781234567897"') + '\n```book-info\nseries: "X, Book 2"\n```\n')
    doc = build_document(*load_vault(vault))
    assert "[YOUR TITLE]" in doc and "ISBN (eBook): [ISBN]" in doc   # nothing filled in from Book Info
    assert "X, Book 2" not in doc and "9781234567897" not in doc


def test_right_label_and_multiline_comment_block(tmp_path):
    from obsidian_book.epub import parse_reading_order
    _, parts, _ = parse_reading_order(write_order(tmp_path, (
        "## One\nright: *Dear diary*\n- [[a]]\n\n%%\nHelp text\ncenter: example\n## Not a chapter\n%%\n")))
    assert [c.title for c in parts[0].chapters] == ["One"]
    assert parts[0].chapters[0].lines == [("right", "*Dear diary*")]


def test_starter_reading_order_help_is_not_printed():
    from obsidian_book.epub import build_document, load_vault
    doc = build_document(*load_vault(TEMPLATE))
    assert "HOW THIS NOTE WORKS" not in doc and "centred" not in doc
