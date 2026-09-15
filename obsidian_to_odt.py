#!/usr/bin/env python3
"""
obsidian_to_odt.py — Compile an Obsidian vault into an ODT (OpenDocument
Text) manuscript via pandoc, styled to match obsidian_to_pdf.py's print
layout as closely as ODT reasonably allows (title page, centered/hidden
front matter, POV names, chapter dates, epigraphs, drop caps, and a
running header) — for further formatting in a word processor or handing
to an editor.

Like obsidian_to_epub.py and obsidian_to_pdf.py, the compile order/structure
comes entirely from "Manuscript Reading Order.md" at the vault root, and
book metadata comes from a "Book Info.md" note — see obsidian_to_epub.py's
module docstring for both file formats, and obsidian_to_pdf.py's for the
additional optional fields (subtitle/title_page_lines/series_position/
series_length/running_header) this script also reads, for the same title
page/running-header purpose.

This does NOT reuse obsidian_to_epub.py's build_document() — that function
wraps styled elements in `::: {.classname}` markdown divs, a CSS mechanism
meant for epub's stylesheet that pandoc's ODT writer silently drops (bold/
italic survive, nothing else does — confirmed by direct testing). ODT
styling instead uses pandoc's `custom-style="Name"` attribute, which maps a
div's or span's content onto a real named paragraph/character style
already defined in the reference document (confirmed working for divs and
spans; confirmed NOT working on headings, which is why front/back-matter
titles use heading *level* — H2, same as chapters — for sizing instead).
Only parsing (parse_reading_order, parse_book_info, index_vault_files,
etc.) is shared with obsidian_to_epub.py; both epub and PDF already have
their own independent renderers, and ODT now does too.

The running header appears on every page, including the title page and
front matter — unlike the PDF, which hides it there. An earlier version
tried to match the PDF exactly (front matter uncounted, the manuscript
body starting a fresh page-1 count) via a mid-document master-page switch,
the spec-correct ODF technique for this. Confirmed via isolated, hand-built
test files (bypassing this script and even pandoc entirely) that
LibreOffice's headless PDF export does not honor that switch — the page
break happens, but the new page style's header never renders, even in the
simplest possible reproduction — while a header on the page style active
from the very start renders correctly every time. See
patch_master_pages()'s docstring.

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

from obsidian_to_epub import (
    BLANK_LINE,
    CENTERED_HIDDEN_HEADING_TITLES,
    CENTERED_VISIBLE_HEADING_TITLES,
    PART_TITLE_RE,
    apply_information_overrides,
    expand_paragraphs,
    find_orphaned_files,
    group_correspondence,
    index_vault_files,
    parse_book_info,
    parse_reading_order,
    render_paragraph_groups,
    split_first_paragraph,
    strip_cuts,
    strip_frontmatter,
)

SCRIPT_DIR = Path(__file__).parent
REFERENCE_ODT = SCRIPT_DIR / "reference.odt"

# Fonts shared with obsidian_to_pdf.py's build_css(), for a visual match.
HEADING_FONT = "Linux Biolinum O"
BODY_FONT = "EB Garamond"


def patch_builtin_styles(xml: str) -> str:
    """Override pandoc's built-in Text_20_body/First_20_paragraph/
    Heading_20_1/Heading_20_2 styles for novel formatting (justified body
    text with a first-line indent, no indent on a paragraph right after a
    heading, and centered/bold/Biolinum headings — Heading_20_1 for Parts,
    Heading_20_2 for chapters and visible front/back-matter titles).

    This has to run on the FINAL COMPILED DOCUMENT's styles.xml, not
    reference.odt — confirmed by inspecting real output: pandoc's ODT
    writer injects its OWN default text-properties for these well-known
    style names even when a reference-doc already customizes them,
    producing a duplicate sibling `<style:text-properties>` element where
    pandoc's own (later) one silently wins. patch_output_odt() is what
    actually calls this, after pandoc has already run. Also
    attribute-order-agnostic (pandoc's own serialization doesn't
    necessarily put style:name first, unlike reference.odt's), unlike a
    naive `<style:style style:name="..."` regex."""
    def replace_style(xml: str, style_name: str, paragraph_props: str = "", text_props: str = "") -> str:
        block_re = re.compile(
            rf'<style:style\b[^>]*style:name="{re.escape(style_name)}"[^>]*>.*?</style:style>',
            re.DOTALL,
        )
        m = block_re.search(xml)
        if not m:
            return xml
        block = m.group(0)
        open_end = block.index(">") + 1
        open_tag, inner = block[:open_end], block[open_end:-len("</style:style>")]
        # Strip ALL existing paragraph-properties/text-properties children
        # (there may be more than one — see docstring above) before adding
        # our own single clean set.
        inner = re.sub(r'<style:paragraph-properties.*?(?:/>|</style:paragraph-properties>)', '', inner, flags=re.DOTALL)
        inner = re.sub(r'<style:text-properties.*?(?:/>|</style:text-properties>)', '', inner, flags=re.DOTALL)
        new_block = open_tag + paragraph_props + text_props + inner + "</style:style>"
        return xml[:m.start()] + new_block + xml[m.end():]

    xml = replace_style(
        xml, "Text_20_body",
        paragraph_props='<style:paragraph-properties fo:margin-top="0in" fo:margin-bottom="0in" '
                        'fo:text-align="justify" fo:text-indent="0.14in" style:contextual-spacing="false" />',
    )
    xml = replace_style(
        xml, "First_20_paragraph",
        paragraph_props='<style:paragraph-properties fo:margin-top="0in" fo:margin-bottom="0in" '
                        'fo:text-align="justify" fo:text-indent="0in" style:contextual-spacing="false" />',
    )
    xml = replace_style(
        xml, "Heading_20_1",
        paragraph_props='<style:paragraph-properties fo:text-align="center" fo:margin-top="0in" '
                        'fo:margin-bottom="0.2in" fo:break-before="page" />',
        text_props=f'<style:text-properties fo:font-family="{HEADING_FONT}" fo:font-weight="bold" fo:font-size="22pt" />',
    )
    xml = replace_style(
        xml, "Heading_20_2",
        paragraph_props='<style:paragraph-properties fo:text-align="center" fo:margin-top="0.2in" '
                        'fo:margin-bottom="0.25in" />',
        text_props=f'<style:text-properties fo:font-family="{HEADING_FONT}" fo:font-weight="bold" '
                   'fo:font-size="16pt" fo:font-style="normal" />',
    )
    return xml


def patch_named_styles(xml: str) -> str:
    """Add every custom-style paragraph/character style build_document_odt()
    references, matching (not pixel-identical, but the same intent as)
    obsidian_to_pdf.py's build_css() values."""
    paragraph_styles = f'''
<style:style style:name="Centered" style:family="paragraph" style:parent-style-name="Standard">
  <style:paragraph-properties fo:text-align="center" fo:text-indent="0in" fo:margin-bottom="0.15in" />
</style:style>
<style:style style:name="POVName" style:family="paragraph" style:parent-style-name="Standard">
  <style:paragraph-properties fo:text-align="center" fo:text-indent="0in" fo:margin-bottom="0.12in" />
  <style:text-properties fo:font-family="{HEADING_FONT}" fo:font-weight="bold" fo:font-size="13pt" />
</style:style>
<style:style style:name="ChapterDate" style:family="paragraph" style:parent-style-name="Standard">
  <style:paragraph-properties fo:text-align="left" fo:text-indent="0in" fo:margin-top="0.1in" fo:margin-bottom="0.3in" />
  <style:text-properties fo:font-size="9.5pt" />
</style:style>
<style:style style:name="PartSubtitle" style:family="paragraph" style:parent-style-name="Standard">
  <style:paragraph-properties fo:text-align="center" fo:text-indent="0in" fo:margin-top="0.5in" />
  <style:text-properties fo:font-family="{HEADING_FONT}" fo:font-weight="bold" fo:font-size="18pt" />
</style:style>
<style:style style:name="Epigraph" style:family="paragraph" style:parent-style-name="Standard">
  <style:paragraph-properties fo:text-align="center" fo:text-indent="0in" fo:margin-top="0.4in" fo:line-height="180%" />
  <style:text-properties fo:font-family="{BODY_FONT}" fo:font-style="italic" fo:font-size="11pt" />
</style:style>
<style:style style:name="Noindent" style:family="paragraph" style:parent-style-name="Text_20_body">
  <style:paragraph-properties fo:text-indent="0in" />
</style:style>
<style:style style:name="Sep" style:family="paragraph" style:parent-style-name="Standard">
  <style:paragraph-properties fo:text-align="center" fo:text-indent="0in" fo:margin-top="0.18in" fo:margin-bottom="0.18in" />
</style:style>
<style:style style:name="TitleMain" style:family="paragraph" style:parent-style-name="Standard">
  <style:paragraph-properties fo:text-align="center" fo:text-indent="0in" fo:margin="0in" />
  <style:text-properties fo:font-family="{HEADING_FONT}" fo:font-weight="bold" fo:font-size="30pt" fo:letter-spacing="0.06in" />
</style:style>
<style:style style:name="TitleSubtitle" style:family="paragraph" style:parent-style-name="Standard">
  <style:paragraph-properties fo:text-align="center" fo:text-indent="0in" fo:margin-top="0.3in" />
  <style:text-properties fo:font-family="{BODY_FONT}" fo:font-style="italic" fo:font-size="15pt" />
</style:style>
<style:style style:name="TitleAuthor" style:family="paragraph" style:parent-style-name="Standard">
  <style:paragraph-properties fo:text-align="center" fo:text-indent="0in" fo:margin-top="0.9in" />
  <style:text-properties fo:font-family="{HEADING_FONT}" fo:font-weight="bold" fo:font-size="13pt" />
</style:style>
<style:style style:name="TitleDots" style:family="paragraph" style:parent-style-name="Standard">
  <style:paragraph-properties fo:text-align="center" fo:text-indent="0in" fo:margin-top="0.15in" />
  <style:text-properties fo:font-size="13pt" fo:letter-spacing="0.06in" />
</style:style>
<style:style style:name="PageBreak" style:family="paragraph" style:parent-style-name="Standard">
  <style:paragraph-properties fo:break-before="page" fo:margin="0in" />
  <style:text-properties fo:font-size="1pt" />
</style:style>
'''
    character_styles = f'''
<style:style style:name="DropcapLetter" style:family="text">
  <style:text-properties fo:font-family="{BODY_FONT}" fo:font-size="300%" />
</style:style>
'''
    xml = xml.replace("</office:styles>", paragraph_styles + character_styles + "</office:styles>", 1)
    return xml


def patch_master_pages(xml: str, running_header: str) -> str:
    """Add a running header to the "Standard" master page — the only one
    this document uses, so the header (and pandoc's already-present
    page-number footer) appear on every page, title page and front matter
    included.

    An earlier version tried the more refined "front matter uncounted,
    manuscript body starts fresh at page 1" — a second master page plus a
    one-off style switching to it partway through the document, the
    spec-correct ODF technique for a mid-document page-style change. Confirmed
    via isolated, hand-built test files that LibreOffice's headless PDF
    export does not honor that switch: the page break happens, but the new
    master page's header never renders, even in the simplest possible
    reproduction — while a header on the master page active from the very
    start (exactly what this function does) renders correctly. Simpler
    and, unlike the other approach, actually verified working."""
    header_text = escape(running_header)

    header_style = (
        '<style:style style:name="Header" style:family="paragraph" '
        'style:parent-style-name="Standard" style:class="extra">'
        '<style:paragraph-properties fo:text-align="center" '
        'style:justify-single-word="false" />'
        f'<style:text-properties fo:font-family="{HEADING_FONT}" fo:font-weight="bold" '
        'fo:font-size="8.5pt" fo:letter-spacing="0.02in" />'
        '</style:style>'
    )
    xml = xml.replace("</office:styles>", header_style + "</office:styles>", 1)

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

    xml = re.sub(
        r'(<style:master-page style:name="Standard"[^>]*>)',
        rf'\1<style:header><text:p text:style-name="Header">{header_text}</text:p></style:header>',
        xml, count=1,
    )
    return xml


def build_reference_odt(running_header: str) -> None:
    """(Re)generate reference.odt next to this script, with all the patches
    above applied to pandoc's default ODT template. Rebuilt on every run so
    it always matches the current patch functions and the current book's
    running header — not checked into git (see .gitignore), same as the
    old compile_book.py convention."""
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
                xml = patch_named_styles(data.decode())
                xml = patch_master_pages(xml, running_header)
                data = xml.encode()
            dst.writestr(name, data)
    REFERENCE_ODT.write_bytes(dst_buf.getvalue())


def patch_output_odt(output: Path) -> None:
    """Rewrite the just-written output.odt's styles.xml in place via
    patch_builtin_styles() — this has to happen after pandoc runs, since
    pandoc's own ODT writer re-injects its default text-properties for
    those well-known style names regardless of what reference.odt already
    customized (see patch_builtin_styles()'s docstring)."""
    src = zipfile.ZipFile(output)
    dst_buf = io.BytesIO()
    with zipfile.ZipFile(dst_buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "styles.xml":
                data = patch_builtin_styles(data.decode()).encode()
            dst.writestr(name, data)
    src.close()
    output.write_bytes(dst_buf.getvalue())


def render_front_back_item_odt(title: str, body: str) -> list:
    """ODT equivalent of obsidian_to_epub.py's render_front_back_item():
    same three buckets (CENTERED_HIDDEN_HEADING_TITLES /
    CENTERED_VISIBLE_HEADING_TITLES / everything else), but a hidden title
    is simply omitted (custom-style doesn't apply to headings, so there's
    no ODT equivalent of CSS's display:none for one) and a visible title
    uses H2 — the same level as chapters, so it gets a matching size for
    free without needing per-heading styling."""
    prose = render_paragraph_groups(body)
    if title in CENTERED_HIDDEN_HEADING_TITLES:
        return [f'::: {{custom-style="Centered"}}\n{prose}\n:::']
    if title in CENTERED_VISIBLE_HEADING_TITLES:
        heading = f"## {title}" if title else ""
        return [heading, f'::: {{custom-style="Centered"}}\n{prose}\n:::']
    heading = f"## {title}" if title else ""
    return [heading, prose]


def inject_dropcap(first_para: str) -> str:
    """Wrap a scene's opening character in a DropcapLetter character-style
    span (pandoc bracket-span syntax) and the whole paragraph in a
    text-indent:0 Noindent style — the ODT equivalent of the PDF's raised
    initial cap."""
    first_para = first_para.strip()
    if not first_para:
        return first_para
    letter, rest = first_para[0], first_para[1:]
    return f'::: {{custom-style="Noindent"}}\n[{letter}]{{custom-style="DropcapLetter"}}{rest}\n:::'


def build_document_odt(vault: Path, book_info: dict) -> str:
    reading_order_path = vault / "Manuscript Reading Order.md"
    if not reading_order_path.is_file():
        sys.exit(f"ERROR: {reading_order_path} not found — it defines the compile order.")

    front_matter, parts, back_matter = parse_reading_order(reading_order_path)
    file_index = index_vault_files(vault)

    referenced = set(front_matter) | set(back_matter)
    for part in parts:
        for chapter in part.chapters:
            referenced.update(chapter.scenes)

    orphans = find_orphaned_files(file_index, referenced)
    if orphans:
        print("WARNING: these vault files have content but aren't referenced in "
              "Manuscript Reading Order.md, so they will NOT be in the compiled book:")
        for p in sorted(orphans, key=lambda p: str(p.relative_to(vault))):
            print(f"  - {p.relative_to(vault)}")

    def resolve(fname: str) -> Path:
        p = file_index.get(fname)
        if p is None:
            sys.exit(f"ERROR: '{fname}' is referenced in Manuscript Reading Order.md "
                      f"but no matching file was found in the vault.")
        return p

    chunks = []

    # Title page.
    title = book_info["title"]
    author = book_info["author"]
    subtitle = book_info.get("subtitle", "")
    title_lines = book_info.get("title_page_lines", "").split("|")
    if len(title_lines) < 2:
        title_lines = [title.upper(), ""]
    series_position = book_info.get("series_position", "")
    series_length = book_info.get("series_length", "")

    chunks.append(f'::: {{custom-style="TitleMain"}}\n{title_lines[0]}\n:::')
    if title_lines[1]:
        chunks.append(f'::: {{custom-style="TitleMain"}}\n{title_lines[1]}\n:::')
    if subtitle:
        chunks.append(f'::: {{custom-style="TitleSubtitle"}}\n{subtitle}\n:::')
    chunks.append(f'::: {{custom-style="TitleAuthor"}}\n{author}\n:::')
    if series_position and series_length:
        dots = "".join(
            "●" if i == int(series_position) else "○"
            for i in range(1, int(series_length) + 1)
        )
        chunks.append(f'::: {{custom-style="TitleDots"}}\n{dots}\n:::')
    # Invisible marker paragraph: forces the page break from the title page
    # into front matter without touching master-page/page-numbering (both
    # stay on "Standard" — no header, no footer — until the manuscript body).
    chunks.append(f'::: {{custom-style="PageBreak"}}\n{BLANK_LINE}\n:::')

    for fname in front_matter:
        fm_title, body = strip_frontmatter(resolve(fname).read_text(encoding="utf-8"))
        if fm_title == "Information":
            body = apply_information_overrides(body, book_info)
        chunks.extend(render_front_back_item_odt(fm_title, body))

    for part in parts:
        m = PART_TITLE_RE.match(part.title)
        heading, subtitle_part = (m.group(1).upper(), m.group(2)) if m else (part.title, "")
        chunks.append(f"# {heading}")
        if subtitle_part:
            chunks.append(f'::: {{custom-style="PartSubtitle"}}\n{subtitle_part}\n:::')
        if part.epigraph:
            poem = "  \n".join(part.epigraph)
            chunks.append(f'::: {{custom-style="Epigraph"}}\n{poem}\n:::')

        for chapter in part.chapters:
            chunks.append(f"## {chapter.title.upper()}")
            if chapter.pov:
                chunks.append(f'::: {{custom-style="POVName"}}\n{chapter.pov}\n:::')
            if chapter.subtitle_lines:
                subtitle_md = "  \n".join(chapter.subtitle_lines)
                chunks.append(f'::: {{custom-style="ChapterDate"}}\n{subtitle_md}\n:::')

            scenes = chapter.scenes
            for i, fname in enumerate(scenes):
                _, body = strip_frontmatter(resolve(fname).read_text(encoding="utf-8"))
                body = expand_paragraphs(strip_cuts(body))
                first_para, rest = split_first_paragraph(body)
                if i == 0:
                    chunks.append(inject_dropcap(first_para))
                else:
                    chunks.append(f'::: {{custom-style="Noindent"}}\n{first_para}\n:::')
                if rest:
                    chunks.append(group_correspondence(rest))
                if i < len(scenes) - 1:
                    chunks.append('::: {custom-style="Sep"}\n—※—\n:::')

    for fname in back_matter:
        bm_title, body = strip_frontmatter(resolve(fname).read_text(encoding="utf-8"))
        chunks.extend(render_front_back_item_odt(bm_title, body))

    return "\n\n".join(c for c in chunks if c)


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
    content = build_document_odt(vault, book_info)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", encoding="utf-8", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    # No --metadata title=/author= here: pandoc's default template turns
    # those into a visible title block before the content, duplicating the
    # title page this script now builds itself.
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

    print("Fixing heading/body styles pandoc doesn't preserve from reference.odt...")
    patch_output_odt(output)

    print(f"Done. Written: {output}")


if __name__ == "__main__":
    main()
