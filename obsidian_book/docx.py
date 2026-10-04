#!/usr/bin/env python3
"""
docx.py — Compile an Obsidian book vault into a Word document (.docx) laid
out like the print PDF, for authors who want to make final changes in Word,
Google Docs, LibreOffice or Pages and export their own PDF.

Same vault conventions as epub.py (Book Info.md + Manuscript Reading
Order.md). Layout, matching pdf.py:
  - trim size and mirrored margins from Book Info.md's trim_size (default
    5.25in x 8in), EB Garamond body text, Linux Biolinum headings (programs
    without those fonts substitute a similar serif / sans-serif one);
  - a title page: the writer's "Title Page" front-matter note if the
    Reading Order lists one (see epub.parse_title_page()), else one built
    from Book Info.md;
  - front matter, then a Contents page with page numbers (Word fields that
    Word and LibreOffice fill in when the document opens);
  - each Part page and each Part's first chapter on a right-hand page (a
    blank page follows each Part page);
    page numbering starts at 1 on Part I;
  - running headers on chapter pages (page number and author on left
    pages, running header and page number on right pages), none on a
    chapter's opening page or on front/back matter;
  - a raised initial capital opening each chapter, Book Info.md's
    scene_break (default —※—) between scenes.

How: the vault becomes pandoc Markdown in which every paragraph carries a
named Word style (pandoc's custom-style), pandoc writes the .docx using a
reference document holding those styles, then build_docx() edits the
result's XML for what pandoc can't express: section breaks (stand-in marker
paragraphs become sectPr), header parts, Contents page-number fields,
mirrored margins and odd/even headers. No LibreOffice needed, so it can run
on the web tool.

Usage:
    python3 -m obsidian_book.docx <vault> [--output-dir DIR]
"""

import argparse
import io
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from obsidian_book.epub import (
    BLANK_LINE, CENTERED_HIDDEN_HEADING_TITLES, PART_TITLE_RE, TITLE_PAGE, UNTRUSTED_FILTER, BookError,
    check_vault, expand_paragraphs, markdown_literal, scene_break, group_correspondence, index_vault_files, load_vault,
    parse_reading_order, parse_title_page, render_paragraph_groups, resolve_output_dir, safe_filename,
    split_first_paragraph, strip_cuts, strip_frontmatter,
)

DEFAULT_TRIM = "5.25in 8in"
TRIM_SIZE_RE = re.compile(r"^\d+(\.\d+)?in \d+(\.\d+)?in$")
OUTER_MARGIN_RATIO = 0.5 / 5.25    # of page width (same ratios as pdf.py)
INNER_MARGIN_RATIO = 0.75 / 5.25
VERTICAL_MARGIN_RATIO = 0.65 / 8.0
BODY_FONT, HEADING_FONT = "EB Garamond", "Linux Biolinum O"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
HEADER_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"
HEADER_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"


def twips(inches: float) -> int:
    return round(inches * 1440)


# --- styles -----------------------------------------------------------------------

def _style(style_id: str, ppr: str = "", rpr: str = "", based_on: str = "Normal", kind: str = "paragraph",
           custom: bool = True, extra: str = "") -> str:
    based = f'<w:basedOn w:val="{based_on}"/>' if based_on else ""
    custom_attr = ' w:customStyle="1"' if custom else ""
    return (f'<w:style w:type="{kind}" w:styleId="{style_id}"{custom_attr}>'
            f'<w:name w:val="{style_id}"/>{based}{extra}<w:qFormat/>'
            f'{f"<w:pPr>{ppr}</w:pPr>" if ppr else ""}{f"<w:rPr>{rpr}</w:rPr>" if rpr else ""}</w:style>')


def _font(name: str) -> str:
    return f'<w:rFonts w:ascii="{name}" w:hAnsi="{name}" w:cs="{name}" w:eastAsia="{name}"/>'


def _spacing(before: int = 0, after: int = 0, line: int | None = None) -> str:
    line_attr = f' w:line="{line}" w:lineRule="auto"' if line else ""
    return f'<w:spacing w:before="{before}" w:after="{after}"{line_attr}/>'


CENTER = '<w:ind w:firstLine="0"/><w:jc w:val="center"/>'
NO_INDENT = '<w:ind w:firstLine="0"/>'
HEADING = _font(HEADING_FONT) + "<w:b/><w:bCs/>"


def style_xml() -> str:
    """Every style the document uses, replacing pandoc's versions of the same ids."""
    pt = lambda points: f'<w:sz w:val="{round(points * 2)}"/><w:szCs w:val="{round(points * 2)}"/>'
    styles = [
        _style("Normal", _spacing(0, 0, 324) + '<w:jc w:val="both"/>',
               _font(BODY_FONT) + pt(10.5) + '<w:color w:val="111111"/><w:lang w:val="en-US"/>',
               based_on="", custom=False),
        _style("BodyText", _spacing(0, 0, 324) + '<w:ind w:firstLine="231"/>', custom=False),
        _style("FirstParagraph", based_on="BodyText", custom=False),
        _style("Compact", based_on="BodyText", custom=False),
        _style("Heading1", '<w:keepNext/>' + _spacing(twips(1.4), 0) + CENTER + '<w:outlineLvl w:val="0"/>',
               HEADING + pt(22) + '<w:color w:val="000000"/>', custom=False, extra='<w:next w:val="BodyText"/>'),
        _style("Heading2", '<w:keepNext/>' + _spacing(twips(0.3), twips(0.35)) + CENTER + '<w:outlineLvl w:val="1"/>',
               HEADING + pt(17) + '<w:color w:val="000000"/>', custom=False, extra='<w:next w:val="BodyText"/>'),
        _style("Hyperlink", rpr='<w:color w:val="auto"/>', based_on="DefaultParagraphFont", kind="character",
               custom=False),
        _style("Header", '<w:ind w:firstLine="0"/>', HEADING + pt(8.5) + '<w:spacing w:val="20"/><w:color w:val="333333"/>',
               custom=False),
        # Title page: TitleStart is the space above it; TP* are the writer's line kinds.
        _style("TitleStart", _spacing(twips(1.2), 0) + CENTER, pt(1)),
        _style("TPBig", _spacing(0, 0, 276) + CENTER, HEADING + pt(30) + '<w:spacing w:val="90"/>'),
        _style("TPMedium", _spacing(0, 0) + CENTER, "<w:i/><w:iCs/>" + pt(15)),
        _style("TPNormal", _spacing(0, 0) + CENTER, HEADING + pt(13)),
        _style("TPSmall", _spacing(0, 0) + CENTER, _font(HEADING_FONT) + pt(10) + '<w:spacing w:val="20"/>'),
        _style("TPSpace", _spacing(0, 0, 384) + CENTER),
        _style("TPRule", _spacing(144, 144) + CENTER),
        # Front/back matter and contents
        _style("FMTitle", _spacing(0, twips(0.4)) + CENTER, HEADING + pt(15)),
        _style("Centered", _spacing(0, twips(0.15)) + CENTER),
        _style("TocEntry", _spacing(115, 115) + CENTER),
        # Parts and chapters
        _style("PartSubtitle", _spacing(twips(0.6), 0) + CENTER, HEADING + pt(22)),
        _style("Epigraph", _spacing(twips(0.8), 0, 432) + CENTER, "<w:i/><w:iCs/>" + pt(11)),
        _style("POVName", _spacing(0, 173) + CENTER, HEADING + pt(13)),
        _style("ChapterDate", _spacing(216, 432) + NO_INDENT + '<w:jc w:val="left"/>', pt(9.5)),
        _style("Dropcap", NO_INDENT),
        _style("DropcapLetter", rpr=_font(BODY_FONT) + pt(31.5), based_on="DefaultParagraphFont", kind="character"),
        _style("Noindent", NO_INDENT),
        _style("Sep", _spacing(259, 259) + CENTER),
        _style("BlankPage", "<w:pageBreakBefore/>" + NO_INDENT),
        _style("Correspondence", "<w:contextualSpacing/>" + _spacing(216, 216) + NO_INDENT),
        # Stand-ins replaced by build_docx()
        _style("ATBreak", _spacing(0, 0) + NO_INDENT, '<w:vanish/>' + pt(1)),
        _style("ATPageRef", based_on="DefaultParagraphFont", kind="character"),
    ]
    return "".join(styles)


def build_reference_docx(dest: Path) -> None:
    """pandoc's default reference.docx with style_xml()'s styles swapped in."""
    result = subprocess.run(["pandoc", "--print-default-data-file", "reference.docx"], capture_output=True)
    if result.returncode != 0:
        raise BookError(f"pandoc couldn't provide its Word template: {result.stderr.decode(errors='replace')}")
    ours = style_xml()
    ours_ids = set(re.findall(r'w:styleId="([^"]+)"', ours))
    with zipfile.ZipFile(io.BytesIO(result.stdout)) as src, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as out:
        for name in src.namelist():
            data = src.read(name)
            if name == "word/styles.xml":
                xml = data.decode("utf-8")
                xml = re.sub(r'<w:style\b[^>]*w:styleId="([^"]+)".*?</w:style>',
                             lambda m: "" if m.group(1) in ours_ids else m.group(0), xml, flags=re.S)
                xml = re.sub(r"<w:rPrDefault>.*?</w:rPrDefault>",
                             f"<w:rPrDefault><w:rPr>{_font(BODY_FONT)}</w:rPr></w:rPrDefault>", xml, flags=re.S)
                data = xml.replace("</w:styles>", ours + "</w:styles>").encode("utf-8")
            out.writestr(name, data)


# --- the document as Markdown ---------------------------------------------------------------

@dataclass
class Section:
    start: str        # "nextPage" or "oddPage" (right-hand page)
    headers: bool     # running headers (chapter pages) or none
    restart: bool = False  # page numbering starts at 1 here


def _div(style: str, text: str) -> str:
    return f'::: {{custom-style="{style}"}}\n{text}\n:::'


class _Doc:
    def __init__(self, token: str):
        self.token = token
        self.sections: list = []
        self.chunks: list = []

    def section(self, start: str, headers: bool = False, restart: bool = False) -> None:
        """Start a new section (a page break of the given kind)."""
        self.chunks.append(_div("ATBreak", f"{self.token}S{len(self.sections)}X"))
        self.sections.append(Section(start, headers, restart))

    def add(self, *chunks: str) -> None:
        self.chunks.extend(c for c in chunks if c)

    def page_ref(self, anchor: str) -> str:
        return f"[{self.token}P{anchor}X]{{custom-style=\"ATPageRef\"}}"


def _title_page_items(book_info: dict) -> list:
    """The built-in title page (no Title Page note) in parse_title_page()'s terms."""
    lines = book_info.get("title_page_lines", "").split("|")
    if len(lines) < 2:
        lines = [book_info["title"].upper(), ""]
    items = [("big", line) for line in lines if line]
    if book_info.get("subtitle"):
        items += [("space", ""), ("medium", book_info["subtitle"])]
    items += [("space", ""), ("space", ""), ("space", ""), ("normal", book_info["author"])]
    position, length = book_info.get("series_position", ""), book_info.get("series_length", "")
    if position.isdigit() and length.isdigit() and 0 < int(position) <= int(length) <= 20:
        items.append(("small", "".join("●" if i == int(position) else "○" for i in range(1, int(length) + 1))))
    return items


def _raised_cap(first: str) -> str:
    """The paragraph with its first letter (and an opening quote before it)
    in the DropcapLetter style. Left alone if it starts with Markdown syntax."""
    quote = {'"': "“", "'": "‘", "“": "“", "‘": "‘"}.get(first[0], "")
    letter_at = 1 if quote else 0
    if len(first) <= letter_at or not first[letter_at].isalnum():
        return first
    return f'[{quote}{first[letter_at]}]{{custom-style="DropcapLetter"}}{first[letter_at + 1:]}'


def build_markdown(vault: Path, book_info: dict, token: str) -> tuple:
    """(markdown, [Section]) for the whole book."""
    front_matter, parts, back_matter = parse_reading_order(vault / "Manuscript Reading Order.md")
    files = index_vault_files(vault)
    read = lambda fname: strip_frontmatter(files[fname].read_text(encoding="utf-8"))
    doc = _Doc(token)

    front = [read(f) for f in front_matter]
    own = next((body for title, body in front if title == TITLE_PAGE), None)
    items = parse_title_page(own) if own is not None else _title_page_items(book_info)
    doc.section("nextPage")
    doc.add(_div("TitleStart", BLANK_LINE))
    styles = {"big": "TPBig", "medium": "TPMedium", "normal": "TPNormal", "small": "TPSmall"}
    for kind, text in items:
        if kind == "space":
            doc.add(_div("TPSpace", BLANK_LINE))
        elif kind == "rule":
            doc.add(_div("TPRule", "⁂"))
        else:
            doc.add(_div(styles[kind], text))

    def front_back(title: str, body: str, start: str) -> None:
        doc.section(start)
        if title not in CENTERED_HIDDEN_HEADING_TITLES and title:
            doc.add(_div("FMTitle", title))
        prose = render_paragraph_groups(body)
        if prose:
            doc.add(_div("Centered", prose))

    for title, body in front:
        if title != TITLE_PAGE:
            front_back(title, body, "nextPage" if title == "Information" else "oddPage")

    # Contents: Parts (or chapters, for a book without parts) with page numbers.
    doc.section("oddPage")
    doc.add(_div("FMTitle", "Contents"))
    chapter_no = 0
    for part_no, part in enumerate(parts, start=1):
        if part.implicit:
            for chapter in part.chapters:
                chapter_no += 1
                doc.add(_div("TocEntry", f"{chapter.title.upper()}—{doc.page_ref(f'chapter-{chapter_no}')}"))
            continue
        chapter_no += len(part.chapters)
        m = PART_TITLE_RE.match(part.title)
        label = f"{m.group(1).split()[-1]}: {m.group(2).upper()}" if m else part.title.upper()
        doc.add(_div("TocEntry", f"{label}—{doc.page_ref(f'part-{part_no}')}"))

    chapter_no = 0
    for part_no, part in enumerate(parts, start=1):
        if not part.implicit:
            m = PART_TITLE_RE.match(part.title)
            heading, subtitle = (m.group(1).upper(), m.group(2)) if m else (part.title, "")
            doc.section("oddPage", restart=part_no == 1)
            doc.add(f"# {heading} {{#part-{part_no}}}")
            if subtitle:
                doc.add(_div("PartSubtitle", subtitle))
            if part.epigraph:
                doc.add(_div("Epigraph", "  \n".join(part.epigraph)))
            # The Part page is one (right-hand) page, so one blank page puts its first
            # chapter on a right-hand page too. Done by hand rather than with an
            # odd-page section break: LibreOffice counts the page such a break inserts
            # as the chapter's first page and puts the running header on the real one.
            doc.add(_div("BlankPage", BLANK_LINE))
        for i, chapter in enumerate(part.chapters):
            chapter_no += 1
            start = "oddPage" if part.implicit and part_no == 1 and i == 0 else "nextPage"
            doc.section(start, headers=True, restart=part.implicit and part_no == 1 and i == 0)
            doc.add(f"## {chapter.title.upper()} {{#chapter-{chapter_no}}}")
            if chapter.pov:
                doc.add(_div("POVName", chapter.pov))
            if chapter.subtitle_lines:
                doc.add(_div("ChapterDate", "  \n".join(chapter.subtitle_lines)))
            for n, fname in enumerate(chapter.scenes):
                body = expand_paragraphs(strip_cuts(read(fname)[1]))
                first, rest = split_first_paragraph(body)
                if n == 0 and first:
                    doc.add(_div("Dropcap", _raised_cap(first)))
                elif first:
                    doc.add(_div("Noindent", first))
                if rest:
                    doc.add(group_correspondence(rest).replace("::: {.correspondence}",
                                                               '::: {custom-style="Correspondence"}'))
                if n < len(chapter.scenes) - 1:
                    doc.add(_div("Sep", markdown_literal(scene_break(book_info))))

    for title, body in (read(f) for f in back_matter):
        front_back(title, body, "oddPage")

    return "\n\n".join(doc.chunks), doc.sections


# --- finishing the .docx --------------------------------------------------------------------

def _insert_before_first(xml: str, new: str, tags: list) -> str:
    """Insert new before the first of these elements (schema order matters to Word), else at the end."""
    positions = [m.start() for tag in tags for m in [re.search(rf"<w:{tag}\b", xml)] if m]
    if positions:
        i = min(positions)
        return xml[:i] + new + xml[i:]
    return xml.replace("</w:settings>", new + "</w:settings>")


def _header_xml(paragraph: str) -> bytes:
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:hdr xmlns:w="{W_NS}" xmlns:r="{R_NS}">{paragraph}</w:hdr>').encode("utf-8")


PAGE_FIELD = ('<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
              '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r>'
              '<w:r><w:fldChar w:fldCharType="end"/></w:r>')


def _headers(author: str, running_header: str, text_width: int) -> dict:
    tabs = (f'<w:tabs><w:tab w:val="center" w:pos="{text_width // 2}"/>'
            f'<w:tab w:val="right" w:pos="{text_width}"/></w:tabs>')
    ppr = f'<w:pPr><w:pStyle w:val="Header"/>{tabs}</w:pPr>'
    tab = "<w:r><w:tab/></w:r>"
    text = lambda t: f'<w:r><w:t xml:space="preserve">{xml_escape(t)}</w:t></w:r>'
    return {
        "headerAtEmpty.xml": _header_xml('<w:p><w:pPr><w:pStyle w:val="Header"/></w:pPr></w:p>'),
        "headerAtEven.xml": _header_xml(f"<w:p>{ppr}{PAGE_FIELD}{tab}{text(author.upper())}</w:p>"),
        "headerAtOdd.xml": _header_xml(f"<w:p>{ppr}{tab}{text(running_header)}{tab}{PAGE_FIELD}</w:p>"),
    }


RESTART = '<w:pgNumType w:start="1"/>'


def _sect_pr(section: Section, page: dict) -> str:
    if section.headers:
        refs = (f'<w:headerReference w:type="even" r:id="rIdAtEven"/>'
                f'<w:headerReference w:type="default" r:id="rIdAtOdd"/>'
                f'<w:headerReference w:type="first" r:id="rIdAtEmpty"/>')
    else:
        refs = "".join(f'<w:headerReference w:type="{t}" r:id="rIdAtEmpty"/>' for t in ("even", "default", "first"))
    return (f'<w:sectPr>{refs}<w:type w:val="{section.start}"/>'
            f'<w:pgSz w:w="{page["width"]}" w:h="{page["height"]}"/>'
            f'<w:pgMar w:top="{page["top"]}" w:right="{page["outer"]}" w:bottom="{page["top"]}" '
            f'w:left="{page["inner"]}" w:header="{page["header"]}" w:footer="{page["header"]}" w:gutter="0"/>'
            f'{RESTART if section.restart else ""}<w:cols w:space="720"/>'
            f'{"<w:titlePg/>" if section.headers else ""}</w:sectPr>')


def finish_docx(path: Path, sections: list, token: str, page: dict, book_info: dict, running_header: str) -> None:
    """Edit the pandoc-written .docx in place: sections, headers, contents
    page numbers, mirrored margins, document properties."""
    with zipfile.ZipFile(path) as zf:
        parts = {name: zf.read(name) for name in zf.namelist()}

    doc = parts["word/document.xml"].decode("utf-8")
    marker = re.compile(r'<w:p>(?:(?!</w:p>).)*?<w:pStyle w:val="ATBreak"\s*/>(?:(?!</w:p>).)*?'
                        + re.escape(token) + r"S(\d+)X(?:(?!</w:p>).)*?</w:p>", re.S)

    def section_break(m: re.Match) -> str:
        n = int(m.group(1))
        if n == 0 or n > len(sections):
            return ""
        return ('<w:p><w:pPr><w:spacing w:before="0" w:after="0" w:line="20" w:lineRule="exact"/>'
                f'<w:rPr><w:sz w:val="2"/></w:rPr>{_sect_pr(sections[n - 1], page)}</w:pPr></w:p>')

    doc = marker.sub(section_break, doc)
    doc = re.sub(r"<w:sectPr\b(?:(?!<w:sectPr).)*?</w:sectPr>\s*</w:body>|<w:sectPr\s*/>\s*</w:body>",
                 _sect_pr(sections[-1], page) + "</w:body>", doc, flags=re.S)
    page_ref = re.compile(r'<w:r>(?:(?!</w:r>).)*?<w:rStyle w:val="ATPageRef"\s*/>(?:(?!</w:r>).)*?'
                          + re.escape(token) + r"P([A-Za-z0-9-]+)X(?:(?!</w:r>).)*?</w:r>", re.S)
    doc = page_ref.sub(lambda m: (
        '<w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/></w:r>'
        f'<w:r><w:instrText xml:space="preserve"> PAGEREF {m.group(1)} \\h </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t></w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'), doc)
    parts["word/document.xml"] = doc.encode("utf-8")

    for name, data in _headers(book_info["author"], running_header, page["text_width"]).items():
        parts[f"word/{name}"] = data
    rels = parts["word/_rels/document.xml.rels"].decode("utf-8")
    rels = rels.replace("</Relationships>", "".join(
        f'<Relationship Id="rIdAt{kind}" Type="{HEADER_REL}" Target="headerAt{kind}.xml"/>'
        for kind in ("Empty", "Even", "Odd")) + "</Relationships>")
    parts["word/_rels/document.xml.rels"] = rels.encode("utf-8")
    types = parts["[Content_Types].xml"].decode("utf-8")
    types = types.replace("</Types>", "".join(
        f'<Override PartName="/word/headerAt{kind}.xml" ContentType="{HEADER_TYPE}"/>'
        for kind in ("Empty", "Even", "Odd")) + "</Types>")
    parts["[Content_Types].xml"] = types.encode("utf-8")

    settings = parts["word/settings.xml"].decode("utf-8")
    settings = _insert_before_first(settings, "<w:mirrorMargins/>", [
        "alignBordersAndEdges", "bordersDoNotSurroundHeader", "bordersDoNotSurroundFooter", "gutterAtTop",
        "hideSpellingErrors", "hideGrammaticalErrors", "activeWritingStyle", "proofState", "formsDesign",
        "attachedTemplate", "linkStyles", "stylePaneFormatFilter", "stylePaneSortMethod", "documentType",
        "mailMerge", "revisionView", "trackRevisions", "doNotTrackMoves", "doNotTrackFormatting",
        "documentProtection", "autoFormatOverride", "styleLockTheme", "styleLockQFSet", "defaultTabStop"])
    settings = _insert_before_first(settings, "<w:evenAndOddHeaders/>", [
        "bookFoldRevPrinting", "bookFoldPrinting", "bookFoldPrintingSheets", "drawingGridHorizontalSpacing",
        "drawingGridVerticalSpacing", "displayHorizontalDrawingGridEvery", "displayVerticalDrawingGridEvery",
        "doNotUseMarginsForDrawingGridOrigin", "drawingGridHorizontalOrigin", "drawingGridVerticalOrigin",
        "doNotShadeFormData", "noPunctuationKerning", "characterSpacingControl"])
    settings = _insert_before_first(settings, '<w:updateFields w:val="true"/>', [
        "hdrShapeDefaults", "footnotePr", "endnotePr", "compat", "docVars", "rsids", "mathPr",
        "attachedSchema", "themeFontLang", "clrSchemeMapping"])
    parts["word/settings.xml"] = settings.encode("utf-8")

    fonts = parts["word/fontTable.xml"].decode("utf-8")
    hints = "".join(f'<w:font w:name="{name}"><w:family w:val="{family}"/><w:pitch w:val="variable"/></w:font>'
                    for name, family in ((BODY_FONT, "roman"), (HEADING_FONT, "swiss")) if f'w:name="{name}"' not in fonts)
    parts["word/fontTable.xml"] = fonts.replace("</w:fonts>", hints + "</w:fonts>").encode("utf-8")

    core = parts.get("docProps/core.xml", b"").decode("utf-8")
    if core:
        for tag, value in (("dc:title", book_info["title"]), ("dc:creator", book_info["author"])):
            if f"<{tag}" in core:
                core = re.sub(rf"<{tag}>.*?</{tag}>|<{tag}\s*/>", f"<{tag}>{xml_escape(value)}</{tag}>", core, flags=re.S)
            else:
                core = core.replace("</cp:coreProperties>", f"<{tag}>{xml_escape(value)}</{tag}></cp:coreProperties>")
        parts["docProps/core.xml"] = core.encode("utf-8")

    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for name in (["[Content_Types].xml"] + [n for n in parts if n != "[Content_Types].xml"]):
            out.writestr(name, parts[name])
    tmp.replace(path)


def page_layout(trim_size: str) -> dict:
    width_in, height_in = (float(p.replace("in", "")) for p in trim_size.split())
    outer, inner, top = width_in * OUTER_MARGIN_RATIO, width_in * INNER_MARGIN_RATIO, height_in * VERTICAL_MARGIN_RATIO
    return {"width": twips(width_in), "height": twips(height_in), "outer": twips(outer), "inner": twips(inner),
            "top": twips(top), "header": twips(top / 2), "text_width": twips(width_in - outer - inner)}


def build_docx(vault, output_dir=None, *, untrusted: bool = False) -> Path:
    """Compile a vault to a .docx and return its path. Raises BookError on
    problems the author needs to fix. untrusted=True (the web tool): YAML
    blocks in notes are ignored, untrusted.lua drops images and raw markup
    pointing outside the vault (and any raw Word XML), trim_size is checked."""
    vault, book_info = load_vault(vault)
    errors = check_vault(vault, book_info)
    if errors:
        raise BookError(f"Not building: fix the {errors} problem(s) above first.")
    if shutil.which("pandoc") is None:
        raise BookError("ERROR: pandoc is not installed (or not on your PATH). "
                        "Install it from https://pandoc.org/installing.html")
    trim_size = book_info.get("trim_size") or DEFAULT_TRIM
    if not TRIM_SIZE_RE.match(trim_size):
        if untrusted:
            raise BookError(f"ERROR: trim_size '{trim_size}' in Book Info.md should look like \"5.25in 8in\".")
        trim_size = DEFAULT_TRIM
    running_header = book_info.get("running_header") or book_info["title"].upper()

    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    output = (resolve_output_dir(book_info, output_dir)
              / f"{safe_filename(book_info['title'])}_{timestamp}.docx").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    print("Assembling manuscript...")
    token = "AT" + secrets.token_hex(6)
    markdown, sections = build_markdown(vault, book_info, token)
    with tempfile.TemporaryDirectory() as tmp:
        source, reference = Path(tmp) / "book.md", Path(tmp) / "reference.docx"
        source.write_text(markdown, encoding="utf-8")
        build_reference_docx(reference)
        cmd = ["pandoc", str(source), "-o", str(output), "--reference-doc", str(reference),
               "--resource-path", str(vault)]
        if untrusted:
            cmd += ["--from", "markdown-yaml_metadata_block", "--lua-filter", str(UNTRUSTED_FILTER)]
        print("Running pandoc...")
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=vault)
    if result.returncode != 0:
        raise BookError(f"pandoc error:\n{result.stderr}")

    print("Adding page layout (sections, headers, contents page numbers)...")
    finish_docx(output, sections, token, page_layout(trim_size), book_info, running_header)
    print(f"Done. Written: {output}")
    return output


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Compile an Obsidian book vault into a Word document (.docx).")
    parser.add_argument("vault", help="path to the Obsidian vault to compile")
    parser.add_argument("--output-dir", help="override the vault's Book Info.md output_dir for this run")
    args = parser.parse_args(argv)
    try:
        build_docx(args.vault, args.output_dir)
    except BookError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
