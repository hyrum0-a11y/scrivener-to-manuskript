"""Tests for the "bring your work in" tools: EPUB -> vault and Scrivener -> vault.

Sample EPUBs and Scrivener projects are built here, so no real manuscript
is needed.
"""

import html
import io
import shutil
import zipfile
from pathlib import Path

import pytest

from obsidian_book import BookError, build_epub
from obsidian_book.epub import check_vault, load_vault, parse_reading_order
from obsidian_book.from_epub import build_book as epub_book, import_epub
from obsidian_book.from_scrivener import find_scrivx, import_scrivener
from obsidian_book.importer import is_separator, split_scenes, take_header_lines
from webapp.app import create_app
from webapp.jobs import Limits

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "vault-template"
needs_pandoc = pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c4944415408d763f8cfc000000301010018dd8db00000000049454e44ae426082")
PROSE = "She walked into the room and looked around. Nothing was where she had left it."


def structure(vault: Path) -> list:
    """[(part title, [(chapter title, pov, scene count)])] from the reading order."""
    _, parts, _ = parse_reading_order(vault / "Manuscript Reading Order.md")
    return [(p.title, [(c.title, c.pov, len(c.scenes)) for c in p.chapters]) for p in parts]


def assert_compiles(vault: Path) -> None:
    vault, info = load_vault(vault)
    assert check_vault(vault, info) == 0


# --- shared text helpers ------------------------------------------------------------

@pytest.mark.parametrize("par", ["* * *", r"\* \* \*", "#", "⁂", "—※—", "-----", "~", "![](images/ornament.png)"])
def test_scene_break_lines(par):
    assert is_separator(par)


@pytest.mark.parametrize("par", ["...", "…", "“…”", "Yes.", "![A map](images/map.png)", "-- she said"])
def test_not_scene_breaks(par):
    assert not is_separator(par)


def test_split_scenes_uses_vault_paragraph_convention():
    md = "First para *here*.\n\nSecond [link](http://x.com) para.\n\n* * *\n\nNext scene ![pic](a.png)\n\n%%not a comment%%"
    assert split_scenes(md) == ["First para *here*.\nSecond link para.", "Next scene\n\\%\\%not a comment\\%\\%"]


def test_header_lines():
    assert take_header_lines("Gerald\nJ\nJanuary 2009\nThe phone rang.", "CHAPTER 1") == \
        (["Gerald", "January 2009"], "The phone rang.")
    assert take_header_lines("The phone rang.\nMore.", "X") == ([], "The phone rang.\nMore.")


# --- EPUB -----------------------------------------------------------------------------

def make_epub(tmp_path, entries: list, files: dict, nested: dict | None = None, name="book.epub",
              cover=True) -> Path:
    """A minimal EPUB 3: files {name: body html}, entries [(title, href)] in the
    nav (nested {title: [(title, href)]} for sub-entries), spine = files order."""
    def li(title, href):
        sub = "".join(li(*e) for e in (nested or {}).get(title, []))
        return f'<li><a href="{href}">{title}</a>{f"<ol>{sub}</ol>" if sub else ""}</li>'
    nav = ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>'
           f'<nav epub:type="toc"><ol>{"".join(li(t, h) for t, h in entries)}</ol></nav></body></html>')
    manifest = "".join(f'<item id="f{i}" href="{n}" media-type="application/xhtml+xml"/>' for i, n in enumerate(files))
    spine = "".join(f'<itemref idref="f{i}"/>' for i in range(len(files)))
    opf = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Test Book</dc:title>'
           '<dc:creator>Ann Author</dc:creator><dc:identifier>urn:isbn:9781234567897</dc:identifier></metadata>'
           '<manifest><item id="nav" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/>'
           + (f'<item id="cov" href="cover.png" properties="cover-image" media-type="image/png"/>' if cover else "")
           + f'{manifest}</manifest>'
           f'<spine>{spine}</spine></package>')
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml",
                    '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                    '<rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/nav.xhtml", nav)
        zf.writestr("OEBPS/cover.png", PNG_1PX)
        for n, body in files.items():
            zf.writestr(f"OEBPS/{n}", f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>')
    return path


@needs_pandoc
def test_epub_round_trip_of_starter_vault(tmp_path):
    epub = build_epub(TEMPLATE, tmp_path / "out")
    vault = import_epub(epub, tmp_path / "v")
    assert_compiles(vault)
    assert [c for _, chs in structure(vault) for c in chs][0][0].upper() == "1. EXAMPLE CHAPTER TITLE"
    info = (vault / "Book Info.md").read_text()
    assert 'title: "My Novel"' in info and 'author: "Your Name"' in info


@needs_pandoc
def test_epub_nested_parts_front_back_and_scenes(tmp_path):
    files = {
        "copy.xhtml": "<h1>Copyright</h1><p>Copyright 2026 Ann Author.</p>",
        "p1.xhtml": "<h1>Part One</h1><p><em>A short epigraph</em></p>",
        "c1.xhtml": f"<h2>Chapter 1</h2><p>{PROSE}</p><p>* * *</p><p>{PROSE}</p>",
        "c2.xhtml": f"<h2>Chapter 2</h2><p>{PROSE}</p><hr/><p>{PROSE}</p><p>⁂</p><p>{PROSE}</p>",
        "about.xhtml": "<h1>About the Author</h1><p>Ann lives by the sea.</p>",
    }
    epub = make_epub(tmp_path, [("Copyright", "copy.xhtml"), ("Part One", "p1.xhtml"), ("About the Author", "about.xhtml")],
                     files, nested={"Part One": [("Chapter 1", "c1.xhtml"), ("Chapter 2", "c2.xhtml")]})
    vault = import_epub(epub, tmp_path / "v")
    assert_compiles(vault)
    assert structure(vault) == [("Part One", [("Chapter 1", "", 2), ("Chapter 2", "", 3)])]
    order = (vault / "Manuscript Reading Order.md").read_text()
    assert "- [[Copyright]]  _Front Matter_" in order and "- [[About the Author]]  _Back Matter_" in order
    assert "> A short epigraph" in order
    info = (vault / "Book Info.md").read_text()
    assert 'isbn: "9781234567897"' in info and 'cover: "cover.png"' in info and 'author_file_as: "Author, Ann"' in info
    assert (vault / "cover.png").read_bytes() == PNG_1PX


@needs_pandoc
def test_epub_flat_contents_with_chapters_found_in_headings(tmp_path):
    # Contents list only the parts; chapters (with a POV line) are headings in the text,
    # and Part II is missing from the contents entirely.
    files = {
        "p1.xhtml": "<h1>PART I</h1><h1>THE START</h1><h2><em>Line one of a poem</em></h2>",
        "c1.xhtml": f"<h2>ONE</h2><h2>Taylor</h2><p>{PROSE}</p>",
        "c2.xhtml": f"<h2>TWO</h2><h2>Gerald</h2><p>{PROSE}</p><p>—※—</p><p>{PROSE}</p>",
        "p2.xhtml": "<h1>PART II</h1><h1>THE END</h1>",
        "c3.xhtml": f"<h2>THREE</h2><h2>Sadi</h2><p>{PROSE}</p>",
        "c4.xhtml": f"<h2>FOUR</h2><h2>Sadi</h2><p>{PROSE}</p>",
    }
    epub = make_epub(tmp_path, [("PART I", "p1.xhtml")], files)
    vault = import_epub(epub, tmp_path / "v")
    assert_compiles(vault)
    assert structure(vault) == [
        ("PART I THE START", [("ONE", "Taylor", 1), ("TWO", "Gerald", 2)]),
        ("PART II THE END", [("THREE", "Sadi", 1), ("FOUR", "Sadi", 1)]),
    ]
    assert "> Line one of a poem" in (vault / "Manuscript Reading Order.md").read_text()


@needs_pandoc
def test_epub_without_parts_or_contents(tmp_path):
    files = {f"c{i}.xhtml": f"<h1>Chapter {i}</h1><p>{PROSE}</p>" for i in range(1, 4)}
    epub = make_epub(tmp_path, [], files)
    vault = import_epub(epub, tmp_path / "v")
    assert_compiles(vault)
    assert structure(vault) == [("", [(f"Chapter {i}", "", 1) for i in range(1, 4)])]


@needs_pandoc
def test_epub_ignores_contents_links_outside_the_book(tmp_path):
    files = {"c1.xhtml": f"<h1>Chapter 1</h1><p>{PROSE}</p>", "c2.xhtml": f"<h1>Chapter 2</h1><p>{PROSE}</p>"}
    epub = make_epub(tmp_path, [("Secret", "../../../etc/passwd"), ("Chapter 1", "c1.xhtml"),
                                ("Chapter 2", "c2.xhtml")], files)
    vault = import_epub(epub, tmp_path / "v")
    assert [c[0] for _, chs in structure(vault) for c in chs] == ["Chapter 1", "Chapter 2"]


def test_epub_unpacked_size_is_capped(tmp_path):
    files = {"c1.xhtml": "<p>" + "x" * 50_000 + "</p>"}
    with pytest.raises(BookError, match="too large"):
        epub_book(make_epub(tmp_path, [("C", "c1.xhtml")], files), max_bytes=10_000)


def test_not_an_epub(tmp_path):
    bad = tmp_path / "x.epub"
    bad.write_text("hello")
    with pytest.raises(BookError, match="isn't a valid EPUB"):
        epub_book(bad)


# --- Scrivener ------------------------------------------------------------------------

def rtf(*paras: str) -> str:
    return "{\\rtf1\\ansi " + "".join(p + "\\par\n" for p in paras) + "}"


def make_scriv(tmp_path, name="My Story") -> Path:
    """A minimal Scrivener 3 project: Part > Chapter > scenes, a loose chapter,
    an excluded scene, ebook front matter with a cover image, research to skip."""
    scriv = tmp_path / f"{name}.scriv"
    texts = {
        "A1": rtf("<$Scr_Ps::0>Taylor", "May 2009", PROSE),
        "A2": rtf(PROSE, "#", PROSE),
        "A3": rtf("Excluded scene."),
        "B1": rtf(PROSE),
        "P1": rtf("PART ONE", "An epigraph line"),
        "F1": rtf("Copyright 2026."),
        "F2": rtf("Title page text."),
        "R1": rtf("Research notes."),
    }
    def item(uid, typ, title, children="", include=True):
        meta = f"<MetaData><IncludeInCompile>{'Yes' if include else 'No'}</IncludeInCompile></MetaData>"
        kids = f"<Children>{children}</Children>" if children else ""
        return f'<BinderItem UUID="{uid}" Type="{typ}"><Title>{title}</Title>{meta}{kids}</BinderItem>'
    chapter = item("C1", "Folder", "Chapter 1", item("A1", "Text", "Arrival") + item("A2", "Text", "Later")
                   + item("A3", "Text", "Cut", include=False))
    binder = (item("D", "DraftFolder", "Manuscript", item("P1", "Folder", "Part One", chapter)
                   + item("B1", "Text", "Epilogue"))
              + item("R", "ResearchFolder", "Research", item("R1", "Text", "Notes"))
              + item("FM", "Folder", "Front Matter",
                     item("FP", "Folder", "Paperback", item("F2", "Text", "Title Page"))
                     + item("FE", "Folder", "Ebook", item("IMG", "Image", "cover art") + item("F1", "Text", "Copyright")
                            + item("F2", "Text", "Title Page"))))
    scriv.mkdir()
    (scriv / f"{name}.scrivx").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><ScrivenerProject Version="2.0"><Binder>{binder}</Binder></ScrivenerProject>')
    for uid, text in texts.items():
        (scriv / "Files" / "Data" / uid).mkdir(parents=True)
        (scriv / "Files" / "Data" / uid / "content.rtf").write_text(text)
    (scriv / "Files" / "Data" / "IMG").mkdir()
    (scriv / "Files" / "Data" / "IMG" / "content.png").write_bytes(PNG_1PX)
    (scriv / "Settings").mkdir()
    (scriv / "Settings" / "compile.xml").write_text(
        "<Compile><MetaData><Author>Sam Writer</Author><Surname>Writer</Surname><Forename>Sam</Forename></MetaData></Compile>")
    return scriv


@needs_pandoc
def test_scrivener_project_to_vault(tmp_path):
    vault = import_scrivener(make_scriv(tmp_path), tmp_path / "v")
    assert_compiles(vault)
    # The loose text after the part stays in that part, like the EPUB importer.
    assert structure(vault) == [("Part One", [("Chapter 1", "Taylor", 3), ("Epilogue", "", 1)])]
    order = (vault / "Manuscript Reading Order.md").read_text()
    assert "> An epigraph line" in order and "May 2009" in order
    assert "- [[Copyright]]  _Front Matter_" in order and "Title Page" not in order
    assert "Excluded" not in "".join(p.read_text() for p in vault.rglob("*.md"))
    assert "Research notes" not in "".join(p.read_text() for p in vault.rglob("*.md"))
    assert "Scr_" not in "".join(p.read_text() for p in vault.rglob("*.md"))
    info = (vault / "Book Info.md").read_text()
    assert 'title: "My Story"' in info and 'author: "Sam Writer"' in info and 'author_file_as: "Writer, Sam"' in info
    assert (vault / "cover.png").read_bytes() == PNG_1PX


@needs_pandoc
def test_scrivener_title_and_author_override(tmp_path):
    vault = import_scrivener(make_scriv(tmp_path), tmp_path / "v", title="Better Title", author="Pen Name")
    info = (vault / "Book Info.md").read_text()
    assert 'title: "Better Title"' in info and 'author: "Pen Name"' in info and 'author_file_as: "Name, Pen"' in info


def test_scrivener_project_found_one_level_down_and_errors(tmp_path):
    (tmp_path / "upload").mkdir()
    make_scriv(tmp_path / "upload")
    assert find_scrivx(tmp_path / "upload").name == "My Story.scrivx"
    (tmp_path / "empty").mkdir()
    with pytest.raises(BookError, match="Couldn't find a Scrivener project"):
        find_scrivx(tmp_path / "empty")


# --- web ------------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    return create_app(tmp_path / "data", Limits(keep_seconds=60)).test_client()


def wait_for(client, url, timeout=60):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        page = html.unescape(client.get(url).get_data(as_text=True))
        if "Waiting in line" not in page and "into a vault…" not in page:
            return page
        time.sleep(0.2)
    raise AssertionError("job never finished")


def vault_names(client, job_url) -> list:
    resp = client.get(job_url + "/vault")
    assert resp.mimetype == "application/zip"
    return zipfile.ZipFile(io.BytesIO(resp.data)).namelist()


@needs_pandoc
def test_web_epub_import(client, tmp_path):
    epub = build_epub(TEMPLATE, tmp_path / "out")
    resp = client.post("/import/epub", data={"book": (io.BytesIO(epub.read_bytes()), "my.epub")},
                       content_type="multipart/form-data")
    assert resp.status_code == 303
    page = wait_for(client, resp.headers["Location"])
    assert "Download vault (.zip)" in page and "Book check passed" in page and "Next steps" in page, page
    names = vault_names(client, resp.headers["Location"])
    assert "My Novel/Book Info.md" in names and "My Novel/Manuscript Reading Order.md" in names


@needs_pandoc
def test_web_scrivener_folder_and_zip_import(client, tmp_path):
    scriv = make_scriv(tmp_path)
    parts = [(io.BytesIO(f.read_bytes()), f"My Story.scriv/{f.relative_to(scriv).as_posix()}")
             for f in scriv.rglob("*") if f.is_file()]
    resp = client.post("/import/scrivener", data={"files": parts, "author": "Pen Name"},
                       content_type="multipart/form-data")
    page = wait_for(client, resp.headers["Location"])
    assert "Download vault (.zip)" in page and "Pen Name" in page, page
    assert "My Story/Book Info.md" in vault_names(client, resp.headers["Location"])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in scriv.rglob("*"):
            zf.write(f, Path("My Story.scriv") / f.relative_to(scriv))
    resp = client.post("/import/scrivener", data={"book": (io.BytesIO(buf.getvalue()), "s.zip")},
                       content_type="multipart/form-data")
    assert "Download vault (.zip)" in wait_for(client, resp.headers["Location"])


def test_web_import_rejects_wrong_files(client):
    page = client.post("/import/epub", data={"book": (io.BytesIO(b"x"), "book.pdf")},
                       content_type="multipart/form-data")
    assert page.status_code == 400 and "isn't an .epub" in html.unescape(page.get_data(as_text=True))
    page = client.post("/import/scrivener", data={}, content_type="multipart/form-data")
    assert page.status_code == 400 and "Pick your .scriv folder" in page.get_data(as_text=True)


@needs_pandoc
def test_web_bad_epub_reports_error(client):
    resp = client.post("/import/epub", data={"book": (io.BytesIO(b"not a zip"), "bad.epub")},
                       content_type="multipart/form-data")
    page = wait_for(client, resp.headers["Location"])
    assert "isn't a valid EPUB" in page and "Try another file" in page


@needs_pandoc
def test_import_without_cover_gets_placeholder_and_guide(tmp_path):
    files = {f"c{i}.xhtml": f"<h1>Chapter {i}</h1><p>{PROSE}</p>" for i in (1, 2)}
    vault = import_epub(make_epub(tmp_path, [], files, cover=False), tmp_path / "v")
    assert_compiles(vault)
    assert (vault / "cover.jpg").read_bytes() == (TEMPLATE / "cover.jpg").read_bytes()
    assert "The cover is a placeholder" in (vault / "Book Info.md").read_text()
    assert (vault / "Start Here.md").is_file()
