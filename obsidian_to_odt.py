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

Three ODF master pages, matching the PDF's own page-type behavior exactly:
"Standard" (title page + front matter — no header), "PartOpener" (each
Part's own opening page — no header), and "ChapterBody" (the manuscript
body proper — every chapter after the first one in its Part, plus every
chapter's continuation pages — with an alternating left/right header:
page number at the outer edge, author centered on left/even pages, book
title centered on right/odd pages; no footer anywhere, the page number
lives in the header). Back matter switches back to "Standard" (no header)
at its own first item. The manuscript body's page count restarts at 1 on
the first Part heading.

Getting the mid-document master-page *switch* working (needed for all of
the above except the header alternation itself, which is a different,
always-static ODF mechanism) took real debugging: earlier versions of this
script put `style:master-page-name` inside `<style:paragraph-properties>`
on a paragraph style, which LibreOffice silently ignores — confirmed via
its own UNO scripting API (not just PDF export output) that a style
written that way doesn't even get *recognized* as a distinct paragraph
style when the document loads, let alone apply a master-page switch. The
correct placement is a **direct attribute of `<style:style>` itself**,
a sibling of `style:family`/`style:parent-style-name`, not a child
element's property. Every "switching doesn't work" conclusion from earlier
work on this script was actually this one placement bug, not a genuine
LibreOffice limitation — see retarget_headings() and patch_master_pages()
below for the corrected version, and [[obsidian_to_odt_pipeline]] (project
memory) for the full debugging trail.

Since `custom-style` doesn't apply to ATX headings (confirmed via direct
testing — see above), the specific headings that need to trigger a switch
(the first Part, every other Part, each Part's first chapter, back
matter's first item) are tagged with an explicit pandoc heading id
(`{#some-id}`) in build_document_odt() — pandoc preserves this verbatim as
the ODT bookmark name rather than auto-slugifying the heading text, so
retarget_headings() can find each one reliably by name in the compiled
output's content.xml, rather than by fragile position-counting.

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
                        'fo:margin-bottom="0.25in" fo:break-before="page" />',
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
<!-- fo:line-height as a fixed point value (not a %, which scales with the
     tallest glyph on the line) stops the 300%-sized DropcapLetter span from
     inflating the gap to the paragraph's own next wrapped line — confirmed
     via isolated testing that a percentage/unset line-height visibly
     double-spaces that one transition. 14pt matches Text_20_body's own
     (LibreOffice-default, ~12pt) single-line spacing. The now-tight line
     box is too short to contain the oversized dropcap glyph without
     colliding with whatever precedes this paragraph (the chapter heading)
     though, so margin-top makes room for it to extend upward instead. -->
<style:style style:name="Dropcap" style:family="paragraph" style:parent-style-name="Text_20_body">
  <style:paragraph-properties fo:text-indent="0in" fo:margin-top="0.3in" fo:line-height="14pt" />
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
<!-- Same invisible-marker trick as PageBreak, but also switches back to
     "Standard" (no header) — used once, for back matter's first item, if
     that item has no heading of its own to hang the switch on (the hidden
     bucket — see render_front_back_item_odt()). Safe to declare
     style:master-page-name here directly since this is a brand-new style
     name, not one of pandoc's well-known ones that patch_builtin_styles()
     has to fight with. -->
<style:style style:name="PageBreakToStandard" style:family="paragraph" style:parent-style-name="Standard" style:master-page-name="Standard">
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


def patch_master_pages(xml: str, author: str, running_header: str) -> str:
    """Define the three master pages: "Standard" (title page + front
    matter — pandoc's default footer stripped, no header added), a new
    empty "PartOpener" (each Part's own opening page — no header, same
    page geometry), and a new "ChapterBody" (the manuscript body — an
    alternating left/right header, matching the PDF: page number at the
    outer edge, author centered on left/even pages, book title centered on
    right/odd pages; no footer). Which master page is actually active on a
    given page is controlled entirely by retarget_headings() (content.xml,
    post-pandoc) — this function only *defines* the three; none of them
    are wired to specific headings here.

    The left/right alternation uses ODF's native mirrored-page-style
    mechanism (style:page-usage="mirrored" on the page-layout, plus a
    <style:header-left> alongside the normal <style:header>) — confirmed
    via isolated testing to work reliably and unconditionally, independent
    of the mid-document master-page *switching* retarget_headings() does
    (a different, previously-broken-by-a-placement-bug mechanism — see
    this module's docstring)."""
    author_text = escape(author.upper())
    title_text = escape(running_header)

    # One shared paragraph style: a "center" tab stop for the centered
    # author/title, a "right" tab stop for the page number on right pages.
    header_style = (
        '<style:style style:name="Header" style:family="paragraph" '
        'style:parent-style-name="Standard" style:class="extra">'
        '<style:paragraph-properties fo:text-align="left">'
        '<style:tab-stops>'
        '<style:tab-stop style:position="3.25in" style:type="center" />'
        '<style:tab-stop style:position="6.5in" style:type="right" />'
        '</style:tab-stops>'
        '</style:paragraph-properties>'
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

    # Without page-usage="mirrored" on the page-layout, LibreOffice ignores
    # <style:header-left> entirely and every page just uses <style:header>.
    xml = xml.replace(
        '<style:page-layout-properties',
        '<style:page-layout-properties style:page-usage="mirrored"',
        1,
    )

    # "Standard" ships with a footer (pandoc's default page-number one) —
    # drop it; no header is added, since title page/front matter get none.
    xml = re.sub(
        r'(<style:master-page style:name="Standard"[^>]*>)\s*<style:footer>.*?</style:footer>\s*(</style:master-page>)',
        r'\1\2',
        xml, count=1, flags=re.DOTALL,
    )

    # Right (odd) pages: title centered, page number at the right edge.
    header_right = (
        '<style:header><text:p text:style-name="Header">'
        f'<text:tab/>{title_text}<text:tab/>'
        '<text:page-number text:select-page="current">1</text:page-number>'
        '</text:p></style:header>'
    )
    # Left (even) pages: page number at the left edge, author centered.
    header_left = (
        '<style:header-left><text:p text:style-name="Header">'
        '<text:page-number text:select-page="current">1</text:page-number>'
        f'<text:tab/>{author_text}'
        '</text:p></style:header-left>'
    )

    part_opener_master = '<style:master-page style:name="PartOpener" style:page-layout-name="Mpm1" />'
    chapter_body_master = (
        '<style:master-page style:name="ChapterBody" style:page-layout-name="Mpm1">'
        f'{header_right}{header_left}'
        '</style:master-page>'
    )
    xml = xml.replace(
        "</office:master-styles>",
        part_opener_master + chapter_body_master + "</office:master-styles>",
        1,
    )

    return xml


def _heading_switch_style(tag: str) -> tuple[str, str] | None:
    """Map one of build_document_odt()'s heading-id tags to (new
    text:style-name, that style's automatic-style XML) — or None if `tag`
    isn't one of ours (e.g. pandoc's own auto-slugified bookmark name for
    an untagged heading, which retarget_headings() must leave alone).
    style:master-page-name is a direct attribute of <style:style> in every
    case — see this module's docstring for why that placement (not nested
    inside <style:paragraph-properties>) is the part that actually matters."""
    if tag == "part-open-first":
        name = "PartOpenFirst"
        xml = (
            f'<style:style style:name="{name}" style:family="paragraph" '
            'style:parent-style-name="Heading_20_1" style:master-page-name="PartOpener">'
            '<style:paragraph-properties fo:break-before="page" style:page-number="1" />'
            '</style:style>'
        )
        return name, xml
    if tag.startswith("part-open-"):
        name = "PartOpen"
        xml = (
            f'<style:style style:name="{name}" style:family="paragraph" '
            'style:parent-style-name="Heading_20_1" style:master-page-name="PartOpener">'
            '<style:paragraph-properties fo:break-before="page" />'
            '</style:style>'
        )
        return name, xml
    if tag.startswith("chapter-open-"):
        name = "ChapterOpen"
        xml = (
            f'<style:style style:name="{name}" style:family="paragraph" '
            'style:parent-style-name="Heading_20_2" style:master-page-name="ChapterBody">'
            '<style:paragraph-properties fo:break-before="page" />'
            '</style:style>'
        )
        return name, xml
    if tag == "backmatter-open":
        name = "BackMatterOpen"
        xml = (
            f'<style:style style:name="{name}" style:family="paragraph" '
            'style:parent-style-name="Heading_20_2" style:master-page-name="Standard">'
            '<style:paragraph-properties fo:break-before="page" />'
            '</style:style>'
        )
        return name, xml
    return None


def retarget_headings(content_xml: str) -> str:
    """Find every heading build_document_odt() tagged with an explicit id
    (`# Title {#part-open-first}` etc. — preserved verbatim by pandoc as
    the ODT bookmark name, since custom-style doesn't apply to headings —
    see this module's docstring) and retarget it onto the one-off style
    _heading_switch_style() maps that tag to, switching master page (and,
    for "part-open-first" specifically, restarting the page count) there.
    Untagged headings — most chapters, most front/back-matter items — are
    left completely alone by the regex simply not matching them.

    A single re.sub() pass handles every match in one go, which matters:
    an earlier version of this kind of post-processing computed all
    target offsets up front via a separate finditer() pass and then
    mutated the string in a loop, which silently corrupted later matches
    once an earlier replacement changed the string's length. re.sub()'s
    callback form has no such issue — it never needs the caller to reason
    about shifting offsets itself."""
    styles_needed: dict[str, str] = {}

    def repl(m: re.Match) -> str:
        rest_of_tag, bookmark_tag = m.group(1), m.group(2)
        result = _heading_switch_style(bookmark_tag)
        if result is None:
            return m.group(0)
        style_name, style_xml = result
        styles_needed[style_name] = style_xml
        return f'<text:h text:style-name="{style_name}"{rest_of_tag}><text:bookmark-start text:name="{bookmark_tag}"'

    pattern = re.compile(
        r'<text:h text:style-name="[^"]*"([^>]*)>\s*'
        r'<text:bookmark-start text:name="([a-zA-Z0-9-]+)"'
    )
    content_xml = pattern.sub(repl, content_xml)

    if styles_needed:
        automatic_styles = "".join(styles_needed.values())
        content_xml = content_xml.replace(
            "</office:automatic-styles>", automatic_styles + "</office:automatic-styles>", 1
        )
    return content_xml


def build_reference_odt(author: str, running_header: str) -> None:
    """(Re)generate reference.odt next to this script, with all the patches
    above applied to pandoc's default ODT template. Rebuilt on every run so
    it always matches the current patch functions and the current book's
    author/running header — not checked into git (see .gitignore), same as
    the old compile_book.py convention."""
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
                xml = patch_master_pages(xml, author, running_header)
                data = xml.encode()
            dst.writestr(name, data)
    REFERENCE_ODT.write_bytes(dst_buf.getvalue())


def patch_output_odt(output: Path) -> None:
    """Rewrite the just-written output.odt in place: styles.xml via
    patch_builtin_styles() (has to happen after pandoc runs, since pandoc's
    own ODT writer re-injects its default text-properties for those
    well-known style names regardless of what reference.odt already
    customized — see that function's docstring), and content.xml via
    retarget_headings() (the master-page switching that suppresses the
    header on title/front-matter/Part-opener pages and restarts the page
    count — see that function's docstring)."""
    src = zipfile.ZipFile(output)
    dst_buf = io.BytesIO()
    with zipfile.ZipFile(dst_buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "styles.xml":
                data = patch_builtin_styles(data.decode()).encode()
            elif name == "content.xml":
                data = retarget_headings(data.decode()).encode()
            dst.writestr(name, data)
    src.close()
    output.write_bytes(dst_buf.getvalue())


def render_front_back_item_odt(title: str, body: str, heading_id: str = "") -> list:
    """ODT equivalent of obsidian_to_epub.py's render_front_back_item():
    same three buckets (CENTERED_HIDDEN_HEADING_TITLES /
    CENTERED_VISIBLE_HEADING_TITLES / everything else), but a hidden title
    is simply omitted (custom-style doesn't apply to headings, so there's
    no ODT equivalent of CSS's display:none for one) and a visible title
    uses H2 — the same level as chapters, so it gets a matching size for
    free without needing per-heading styling. `heading_id`, if given, is
    build_document_odt()'s way of tagging back matter's first (visible-
    bucket) item so retarget_headings() can find and switch it back to the
    no-header "Standard" master page — see that function's docstring."""
    prose = render_paragraph_groups(body)
    id_attr = f" {{#{heading_id}}}" if heading_id else ""
    if title in CENTERED_HIDDEN_HEADING_TITLES:
        return [f'::: {{custom-style="Centered"}}\n{prose}\n:::']
    if title in CENTERED_VISIBLE_HEADING_TITLES:
        heading = f"## {title}{id_attr}" if title else ""
        return [heading, f'::: {{custom-style="Centered"}}\n{prose}\n:::']
    heading = f"## {title}{id_attr}" if title else ""
    return [heading, prose]


def inject_dropcap(first_para: str) -> str:
    """Wrap a scene's opening character in a DropcapLetter character-style
    span (pandoc bracket-span syntax) and the whole paragraph in the
    Dropcap paragraph style (text-indent:0 plus the fixed line-height/
    margin-top fix — see that style's comment in patch_named_styles()) —
    the ODT equivalent of the PDF's raised initial cap."""
    first_para = first_para.strip()
    if not first_para:
        return first_para
    letter, rest = first_para[0], first_para[1:]
    return f'::: {{custom-style="Dropcap"}}\n[{letter}]{{custom-style="DropcapLetter"}}{rest}\n:::'


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
    # No marker needed here to break out of the title page: whatever comes
    # next always forces its own page break already — a hidden-bucket
    # front-matter item via the per-item PageBreak marker below, a
    # visible-bucket one or a chapter via Heading_20_2's break-before, or
    # (front matter empty) a Part via Heading_20_1's break-before.

    for fname in front_matter:
        fm_title, body = strip_frontmatter(resolve(fname).read_text(encoding="utf-8"))
        if fm_title == "Information":
            body = apply_information_overrides(body, book_info)
        if fm_title in CENTERED_HIDDEN_HEADING_TITLES:
            # No heading of its own to hang fo:break-before on — see
            # render_front_back_item_odt() — so force the page break with
            # an invisible marker paragraph instead.
            chunks.append(f'::: {{custom-style="PageBreak"}}\n{BLANK_LINE}\n:::')
        chunks.extend(render_front_back_item_odt(fm_title, body))

    # Every Part heading is tagged so retarget_headings() can switch it to
    # the no-header "PartOpener" master page — the first one additionally
    # restarts the page count there. Each Part's *first* chapter is tagged
    # so retarget_headings() can switch it to "ChapterBody" (header on);
    # later chapters in the same Part are left untagged — nothing switches
    # master page away from "ChapterBody" for them, so they just continue
    # using it, which is exactly what's wanted.
    for part_idx, part in enumerate(parts):
        m = PART_TITLE_RE.match(part.title)
        heading, subtitle_part = (m.group(1).upper(), m.group(2)) if m else (part.title, "")
        part_tag = "part-open-first" if part_idx == 0 else f"part-open-{part_idx}"
        chunks.append(f"# {heading} {{#{part_tag}}}")
        if subtitle_part:
            chunks.append(f'::: {{custom-style="PartSubtitle"}}\n{subtitle_part}\n:::')
        if part.epigraph:
            poem = "  \n".join(part.epigraph)
            chunks.append(f'::: {{custom-style="Epigraph"}}\n{poem}\n:::')

        for chapter_idx, chapter in enumerate(part.chapters):
            chapter_id_attr = f" {{#chapter-open-{part_idx}}}" if chapter_idx == 0 else ""
            chunks.append(f"## {chapter.title.upper()}{chapter_id_attr}")
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

    # Back matter switches back to the no-header "Standard" master page at
    # its own first item — a hidden-bucket item (no heading — see
    # render_front_back_item_odt()) uses the invisible PageBreakToStandard
    # marker instead of a heading-id tag, since custom-style *does* work on
    # divs. Every later back-matter item is left alone: nothing switches
    # master page away from "Standard" for them, so they just stay there.
    for bm_idx, fname in enumerate(back_matter):
        bm_title, body = strip_frontmatter(resolve(fname).read_text(encoding="utf-8"))
        is_first = bm_idx == 0
        if bm_title in CENTERED_HIDDEN_HEADING_TITLES:
            marker_style = "PageBreakToStandard" if is_first else "PageBreak"
            chunks.append(f'::: {{custom-style="{marker_style}"}}\n{BLANK_LINE}\n:::')
            chunks.extend(render_front_back_item_odt(bm_title, body))
        else:
            heading_id = "backmatter-open" if is_first else ""
            chunks.extend(render_front_back_item_odt(bm_title, body, heading_id))

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
    build_reference_odt(book_info["author"], running_header)

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
