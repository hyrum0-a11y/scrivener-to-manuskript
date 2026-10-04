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
    scene = next((v / "Manuscript").rglob("*.md"))
    shutil.copy(scene, scene.with_name("02 - Second Scene.md"))
    order = v / "Manuscript Reading Order.md"
    order.write_text(order.read_text().replace("- [[01 - Opening Scene]]", "- [[01 - Opening Scene]]\n- [[02 - Second Scene]]"))
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
