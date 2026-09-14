#!/usr/bin/env python3
"""
obsidian_to_odt.py — Compile an Obsidian vault into an ODT (OpenDocument
Text) manuscript via pandoc, for further formatting in a word processor
(LibreOffice Writer, etc.) or handing to an editor/proofreader who wants a
plain document rather than an epub/PDF.

Like obsidian_to_epub.py and obsidian_to_pdf.py, the compile order/structure
comes entirely from "Manuscript Reading Order.md" at the vault root, and
book metadata (title, author, ...) comes from a "Book Info.md" note — see
obsidian_to_epub.py's module docstring for both file formats. This script
reuses obsidian_to_epub.py's document assembly directly (parse_book_info,
parse_reading_order, build_document) instead of duplicating it: the same
markdown chunks that produce the epub are handed to pandoc's ODT writer
instead of its epub writer. Pandoc's ODT output silently drops the
epub-only fenced-div classes (.povname, .chapterdate, .dropcap, etc.) used
there for CSS styling — bold and italic survive, but none of the epub/PDF's
typographic styling (drop caps, centered POV names) does. This is meant as
an editable draft format, not a finished distributable, unlike the other
two scripts.

It does get a running header and numbered pages, though (optional
running_header field in Book Info.md, same convention as
obsidian_to_pdf.py — defaults to title.upper() if not given): front matter
(Information, Acknowledgments, etc.) gets neither, and the manuscript body
starts a fresh page count at 1 from its first Part heading onward, the way
a print book's front matter is conventionally uncounted. This is done via
two ODF master pages ("Standard" for front matter, "Manuscript" for the
body — see patch_master_pages()) plus a one-off automatic style on that
first heading that switches master page and restarts numbering there (see
restart_page_numbering()). Everything after that heading — later Parts,
back matter — keeps using the "Manuscript" master page and just keeps
counting, since ODF master-page assignment persists until the next
explicit change.

Requirements:
  - Python 3.10+
  - pandoc  (https://pandoc.org/installing.html)

Usage:
    python3 obsidian_to_odt.py <vault> [--output-dir DIR]
"""

import argparse
import io
import re
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from obsidian_to_epub import parse_book_info, parse_reading_order, build_document

SCRIPT_DIR = Path(__file__).parent
REFERENCE_ODT = SCRIPT_DIR / "reference.odt"

MANUSCRIPT_HEADING_STYLE = "Heading_20_1_ManuscriptStart"


def patch_styles(xml: str) -> str:
    """Patch Text_20_body style for novel paragraph formatting: justified,
    0.14" first-line indent, no extra spacing between paragraphs."""
    new_props = (
        '<style:paragraph-properties '
        'fo:margin-top="0in" fo:margin-bottom="0in" '
        'fo:text-align="justify" fo:text-indent="0.14in" '
        'style:contextual-spacing="false" />'
    )

    def replace_props(m: re.Match) -> str:
        block = m.group(0)
        return re.sub(r'<style:paragraph-properties[^/]*/>', new_props, block)

    return re.sub(
        r'<style:style style:name="Text_20_body".*?</style:style>',
        replace_props,
        xml,
        flags=re.DOTALL,
    )


def patch_master_pages(xml: str, running_header: str) -> str:
    """Add a second master page, "Manuscript", used for everything from the
    first Part heading onward: a centered running header plus a
    page-number footer. The original "Standard" master page (front matter)
    loses its footer entirely, so front-matter pages carry no page number
    at all — restart_page_numbering() below is what actually switches the
    manuscript body onto this master page and resets the count to 1 there."""
    header_text = escape(running_header)

    # Front matter ("Standard" master page): strip its footer/page-number.
    xml = re.sub(
        r'(<style:master-page style:name="Standard"[^>]*>)\s*'
        r'<style:footer>.*?</style:footer>\s*(</style:master-page>)',
        r'\1\2',
        xml, count=1, flags=re.DOTALL,
    )

    # The shared page-layout's header area ships empty in pandoc's default
    # template — give it a real height/margin so a header actually shows.
    xml = xml.replace(
        '<style:header-style />',
        '<style:header-style>'
        '<style:header-footer-properties fo:min-height="0.4in" '
        'fo:margin-left="0in" fo:margin-right="0in" fo:margin-bottom="0.2in" '
        'style:dynamic-spacing="false" /></style:header-style>',
        1,
    )

    # New paragraph style for the header text itself (centered, small).
    header_style = (
        '<style:style style:name="Header" style:family="paragraph" '
        'style:parent-style-name="Standard" style:class="extra">'
        '<style:paragraph-properties fo:text-align="center" '
        'style:justify-single-word="false" />'
        '<style:text-properties fo:font-size="85%" fo:letter-spacing="0.02in" />'
        '</style:style>'
    )
    xml = xml.replace("</office:styles>", header_style + "</office:styles>", 1)

    # New "Manuscript" master page: same page geometry ("Mpm1") as
    # Standard, but with the running header and a page-number footer
    # ("MP1" — the same centered page-number paragraph style Standard's
    # footer used, still defined in styles.xml, just no longer referenced
    # by Standard after the strip above).
    manuscript_master = (
        '<style:master-page style:name="Manuscript" style:page-layout-name="Mpm1">'
        f'<style:header><text:p text:style-name="Header">{header_text}</text:p></style:header>'
        '<style:footer><text:p text:style-name="MP1">'
        '<text:page-number text:select-page="current">1</text:page-number>'
        '</text:p></style:footer>'
        '</style:master-page>'
    )
    xml = xml.replace("</office:master-styles>", manuscript_master + "</office:master-styles>", 1)

    return xml


def build_reference_odt(running_header: str) -> None:
    """(Re)generate reference.odt next to this script, with novel paragraph
    styles and the front-matter/manuscript master pages patched into
    pandoc's default ODT template. Rebuilt on every run so it always
    matches the current patch_styles()/patch_master_pages() logic and the
    current book's running header — not checked into git (see .gitignore),
    same as the old compile_book.py convention."""
    result = subprocess.run(
        ["pandoc", "--print-default-data-file", "reference.odt"],
        capture_output=True,
    )
    src = zipfile.ZipFile(io.BytesIO(result.stdout))
    dst_buf = io.BytesIO()
    with zipfile.ZipFile(dst_buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "styles.xml":
                xml = patch_styles(data.decode())
                xml = patch_master_pages(xml, running_header)
                data = xml.encode()
            dst.writestr(name, data)
    REFERENCE_ODT.write_bytes(dst_buf.getvalue())


def restart_page_numbering(content_xml: str, front_matter_count: int) -> str:
    """Retarget the first Part heading — the (front_matter_count + 1)-th
    top-level Heading 1 in the document, since every front-matter item and
    every Part title renders as exactly one H1 — onto a one-off automatic
    style that forces a page break onto the "Manuscript" master page
    (running header + page-number footer) and restarts the page count at 1
    there. Everything before it (front matter) stays on "Standard" (no
    header, no page number); everything after just keeps counting, since
    ODF master-page assignment persists until the next explicit change."""
    matches = list(re.finditer(r'<text:h text:style-name="Heading_20_1"', content_xml))
    if front_matter_count >= len(matches):
        return content_xml  # no Part heading found; leave numbering alone

    # Retarget the heading FIRST, using offsets measured against the
    # as-yet-unmodified string — inserting the automatic style below would
    # shift every later offset, so doing that first and reusing this span
    # afterward would slice the wrong (shifted) position.
    start, end = matches[front_matter_count].span()
    content_xml = (
        content_xml[:start]
        + f'<text:h text:style-name="{MANUSCRIPT_HEADING_STYLE}"'
        + content_xml[end:]
    )

    automatic_style = (
        f'<style:style style:name="{MANUSCRIPT_HEADING_STYLE}" '
        'style:family="paragraph" style:parent-style-name="Heading_20_1">'
        '<style:paragraph-properties fo:break-before="page" '
        'style:master-page-name="Manuscript" style:page-number="1" />'
        '</style:style>'
    )
    return content_xml.replace(
        "</office:automatic-styles>", automatic_style + "</office:automatic-styles>", 1
    )


def patch_output_odt(output: Path, front_matter_count: int) -> None:
    """Rewrite the just-written output.odt's content.xml in place via
    restart_page_numbering() — this has to happen after pandoc runs
    (content.xml is generated fresh from the manuscript text each time),
    unlike patch_master_pages()/patch_styles() which only need to touch
    the reusable reference.odt template."""
    src = zipfile.ZipFile(output)
    dst_buf = io.BytesIO()
    with zipfile.ZipFile(dst_buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "content.xml":
                data = restart_page_numbering(data.decode(), front_matter_count).encode()
            dst.writestr(name, data)
    src.close()
    output.write_bytes(dst_buf.getvalue())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compile an Obsidian vault into an ODT manuscript (pandoc). "
                    "Book metadata is read from <vault>/Book Info.md — see "
                    "obsidian_to_epub.py's module docstring.")
    parser.add_argument("vault", help="path to the Obsidian vault to compile")
    parser.add_argument("--output-dir", help="override the vault's Book Info.md output_dir for this run")
    args = parser.parse_args()

    vault = Path(args.vault).expanduser().resolve()
    if not (vault / "Manuscript").is_dir():
        sys.exit(f"ERROR: {vault} does not look like a vault (no Manuscript/ folder)")

    reading_order_path = vault / "Manuscript Reading Order.md"
    if not reading_order_path.is_file():
        sys.exit(f"ERROR: {reading_order_path} not found — it defines the compile order.")
    front_matter, _parts, _back_matter = parse_reading_order(reading_order_path)

    book_info = parse_book_info(vault)
    output_dir_str = args.output_dir or book_info["output_dir"]
    if not output_dir_str:
        sys.exit("ERROR: no output directory given — set output_dir in Book Info.md or pass --output-dir.")
    output_dir = Path(output_dir_str).expanduser().resolve()

    running_header = book_info.get("running_header") or book_info["title"].upper()

    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    output = (output_dir / f"{book_info['title']}_{timestamp}.odt").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    print("Building reference styles...")
    build_reference_odt(running_header)

    print("Assembling manuscript...")
    content = build_document(vault, book_info)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", encoding="utf-8", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    # No --metadata title=/author= here: pandoc's default template turns
    # those into a visible title block before the content, duplicating the
    # vault's own Information page — same gotcha obsidian_to_epub.py works
    # around via --epub-metadata instead of --metadata for that reason.
    print("Running pandoc...")
    result = subprocess.run(
        ["pandoc", tmp_path,
         "--reference-doc", str(REFERENCE_ODT),
         "-o", str(output)],
        capture_output=True, text=True,
    )
    Path(tmp_path).unlink()

    if result.returncode != 0:
        sys.exit(f"pandoc error:\n{result.stderr}")

    print("Adding running header and restarting page numbers at the manuscript body...")
    patch_output_odt(output, len(front_matter))

    print(f"Done. Written: {output}")


if __name__ == "__main__":
    main()
