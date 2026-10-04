"""Tests for the Word (.docx) converter and the writer's Title Page note."""

import io
import re
import shutil
import xml.dom.minidom
import zipfile
from pathlib import Path

import pytest

from obsidian_book import build_epub
from obsidian_book.docx import build_docx
from obsidian_book.epub import parse_title_page
from webapp.jobs import type_label

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "vault-template"
needs_pandoc = pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")


def vault_copy(tmp_path, scene_extra="") -> Path:
    v = tmp_path / "vault"
    shutil.copytree(TEMPLATE, v)
    if scene_extra:
        scene = next((v / "Manuscript").rglob("*.md"))
        scene.write_text(scene.read_text() + scene_extra)
    return v


def docx_parts(path: Path) -> dict:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def test_parse_title_page():
    body = "\n# THE LONG\n# ROAD\n\n## *A Novel*\n\nJane %%note%% Smith\n---\n### Book One\n\n"
    assert parse_title_page(body) == [("big", "THE LONG"), ("big", "ROAD"), ("space", ""), ("medium", "*A Novel*"),
                                      ("space", ""), ("normal", "Jane Smith"), ("rule", ""), ("small", "Book One")]


def test_job_type_labels():
    assert [type_label(k) for k in ("epub", "epub,pdf,docx", "import-scrivener")] == \
        ["EPUB", "EPUB + PDF + Word", "Scrivener import"]


@needs_pandoc
def test_docx_layout_of_starter_vault(tmp_path):
    parts = docx_parts(build_docx(TEMPLATE, tmp_path))
    for name, data in parts.items():
        if name.endswith((".xml", ".rels")):
            xml.dom.minidom.parseString(data)  # every part well-formed
    doc = parts["word/document.xml"].decode()
    assert not re.search(r"AT[0-9a-f]{12}[SP]", doc)          # every stand-in replaced
    # title page, Information, Acknowledgments, Contents, Part, chapter, About the Author
    assert doc.count("<w:sectPr") == 7
    assert doc.count('<w:type w:val="oddPage"/>') == 4      # Acknowledgments, Contents, Part, back matter
    assert '<w:pgNumType w:start="1"/>' in doc              # numbering starts at Part I
    assert "[YOUR TITLE]" in doc and "PAGEREF part-1" in doc
    assert "Anna" in doc and "London, 1952" in doc
    assert 'w:styleId="DropcapLetter"' in parts["word/styles.xml"].decode()
    assert b"YOUR NAME" in parts["word/headerAtEven.xml"] and b"MY NOVEL" in parts["word/headerAtOdd.xml"]
    settings = parts["word/settings.xml"].decode()
    assert settings.index("<w:mirrorMargins/>") < settings.index("<w:defaultTabStop")   # schema order
    assert settings.index("<w:evenAndOddHeaders/>") < settings.index("<w:characterSpacingControl")
    assert "headerAtOdd.xml" in parts["[Content_Types].xml"].decode()
    w, h = re.search(r'<w:pgSz w:w="(\d+)" w:h="(\d+)"', doc).groups()
    assert (int(w), int(h)) == (7560, 11520)                  # 5.25in x 8in


@needs_pandoc
def test_docx_without_title_page_note_builds_one(tmp_path):
    v = vault_copy(tmp_path)
    order = v / "Manuscript Reading Order.md"
    order.write_text(order.read_text().replace("- [[Title Page]]  _Front Matter_\n", ""))
    doc = docx_parts(build_docx(v, tmp_path / "out"))["word/document.xml"].decode()
    big = re.findall(r'<w:pStyle w:val="TPBig"\s*/>.*?<w:t[^>]*>([^<]*)</w:t>', doc)
    assert big == ["MY NOVEL"]


@needs_pandoc
def test_docx_untrusted_drops_server_files_and_raw_xml(tmp_path):
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"\x89PNG\r\n\x1a\n" + b"SECRET-MARKER")
    extra = (f"\n![a]({secret}) ![b](../secret.png) ![c](http://example.com/x.png)\n"
             "```{=openxml}\n<w:p><w:r><w:t>INJECTED-XML</w:t></w:r></w:p>\n```\n"
             '::: {custom-style="ATBreak"}\nATdeadbeef1234S0X\n:::\n')
    out = build_docx(vault_copy(tmp_path, extra), tmp_path / "out", untrusted=True)
    blob = b"".join(docx_parts(out).values())
    assert b"SECRET-MARKER" not in blob and b"INJECTED-XML" not in blob


@needs_pandoc
def test_title_page_note_in_epub(tmp_path):
    with zipfile.ZipFile(build_epub(TEMPLATE, tmp_path)) as zf:
        text = "".join(zf.read(n).decode("utf-8", "replace") for n in zf.namelist() if n.endswith(".xhtml"))
    assert 'class="tp-big"' in text and "[YOUR TITLE]" in text


def test_title_page_note_in_pdf():
    pdf = pytest.importorskip("obsidian_book.pdf")
    from obsidian_book.epub import load_vault
    html, _ = pdf.build_html(*load_vault(TEMPLATE))
    assert 'class="tp-big"' in html and "[YOUR TITLE]" in html and 'class="title-main"' not in html


@needs_pandoc
def test_web_word_download(tmp_path):
    from webapp.app import create_app
    from webapp.jobs import Limits
    import time
    client = create_app(tmp_path / "data", Limits(keep_seconds=60)).test_client()
    files = [(io.BytesIO(f.read_bytes()), f"My Book/{f.relative_to(TEMPLATE).as_posix()}")
             for f in TEMPLATE.rglob("*") if f.is_file() and ".obsidian" not in f.parts]
    resp = client.post("/convert", data={"formats": ["docx"], "files": files}, content_type="multipart/form-data")
    url = resp.headers["Location"]
    for _ in range(300):
        page = client.get(url).get_data(as_text=True)
        if "Checking and building" not in page and "Waiting in line" not in page:
            break
        time.sleep(0.2)
    assert "Download Word (.docx)" in page and "EB Garamond" in page, page
    doc = client.get(url + "/docx")
    assert doc.mimetype.endswith("wordprocessingml.document") and doc.data[:2] == b"PK"


@needs_pandoc
def test_scene_break_setting_in_every_format(tmp_path):
    v = vault_copy(tmp_path)
    info = v / "Book Info.md"
    info.write_text(info.read_text().replace('scene_break: "—※—"', 'scene_break: "* * *"'))
    doc = docx_parts(build_docx(v, tmp_path / "out"))["word/document.xml"].decode()
    assert "* * *" in doc and "—※—" not in doc
    with zipfile.ZipFile(build_epub(v, tmp_path / "out")) as zf:
        text = "".join(zf.read(n).decode() for n in zf.namelist() if n.endswith(".xhtml"))
    assert 'class="sep"' in text and "* * *" in text and "<hr" not in text
    pdf = pytest.importorskip("obsidian_book.pdf")
    from obsidian_book.epub import load_vault
    assert '<p class="sep">* * *</p>' in pdf.build_html(*load_vault(v))[0]


@needs_pandoc
def test_blank_scene_break(tmp_path):
    from obsidian_book.epub import scene_break
    assert scene_break({"scene_break": "blank"}) == " " and scene_break({"scene_break": ""}) == "—※—"
    v = vault_copy(tmp_path)
    info = v / "Book Info.md"
    info.write_text(info.read_text().replace('scene_break: "—※—"', 'scene_break: "blank"'))
    doc = docx_parts(build_docx(v, tmp_path / "out"))["word/document.xml"].decode()
    sep = re.search(r'<w:pStyle w:val="SepBlank"\s*/>.*?</w:p>', doc, re.S).group(0)
    assert " " in sep and "—※—" not in doc and "blank" not in sep


def set_info(v: Path, old: str, new: str) -> None:
    info = v / "Book Info.md"
    info.write_text(info.read_text().replace(old, new))


@needs_pandoc
def test_chapter_space_above(tmp_path):
    from obsidian_book.epub import BookError, chapter_space_above
    assert chapter_space_above({"chapter_space_above": ""}) is None and chapter_space_above({"chapter_space_above": "5"}) == 5
    for bad in ("lots", "13"):
        with pytest.raises(BookError, match="number of empty lines"):
            chapter_space_above({"chapter_space_above": bad})
    v = vault_copy(tmp_path)
    set_info(v, 'chapter_space_above: ""', 'chapter_space_above: "6"')
    styles = docx_parts(build_docx(v, tmp_path / "out"))["word/styles.xml"].decode()
    heading2 = re.search(r'w:styleId="Heading2".*?</w:style>', styles, re.S).group(0)
    assert 'w:before="1701"' in heading2                     # 6 lines x 10.5pt x 1.35 = 85pt
    with zipfile.ZipFile(build_epub(v, tmp_path / "out")) as zf:
        css = "".join(zf.read(n).decode() for n in zf.namelist() if n.endswith(".css"))
    assert "margin-top: 8.10rem" in css
    pdf = pytest.importorskip("obsidian_book.pdf")
    import pymupdf
    out = pdf.build_pdf(v, tmp_path / "pdf6")
    doc = pymupdf.open(out)
    page = next(p for p in doc if p.get_text().strip().startswith("Chapter 1"))
    y_spaced = page.search_for("Chapter 1")[0].y0
    set_info(v, 'chapter_space_above: "6"', 'chapter_space_above: ""')
    doc = pymupdf.open(pdf.build_pdf(v, tmp_path / "pdf0"))
    page = next(p for p in doc if p.get_text().strip().startswith("Chapter 1"))
    assert y_spaced - page.search_for("Chapter 1")[0].y0 > 50   # moved down by roughly 85pt - 0.3in


def test_page_headers_defaults_and_fallback():
    from obsidian_book.epub import page_headers
    base = {"author": "Jane Smith", "title": "The Long Road"}
    assert page_headers(base) == ("JANE SMITH", "THE LONG ROAD")
    assert page_headers({**base, "running_header": "LONG ROAD 1"}) == ("JANE SMITH", "LONG ROAD 1")
    assert page_headers({**base, "header_left": "Jane Smith", "header_right": 'The "Long" Road',
                         "running_header": "ignored"}) == ("Jane Smith", 'The "Long" Road')


@needs_pandoc
def test_header_left_and_right_in_word_and_pdf(tmp_path):
    v = vault_copy(tmp_path)
    set_info(v, 'header_left: ""', 'header_left: "Jane Smith"')
    set_info(v, 'header_right: ""', 'header_right: "The "Long" Road"')
    parts = docx_parts(build_docx(v, tmp_path / "out"))
    assert b"Jane Smith" in parts["word/headerAtEven.xml"] and b"YOUR NAME" not in parts["word/headerAtEven.xml"]
    assert b"The &quot;Long&quot; Road" in parts["word/headerAtOdd.xml"] or b'The "Long" Road' in parts["word/headerAtOdd.xml"]
    pdf = pytest.importorskip("obsidian_book.pdf")
    import pymupdf
    text = "".join(p.get_text() for p in pymupdf.open(pdf.build_pdf(v, tmp_path / "pdf")))
    assert "Jane Smith" in text and 'The "Long" Road' in text.replace("“", '"').replace("”", '"')
