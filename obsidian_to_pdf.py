#!/usr/bin/env python3
"""
obsidian_to_pdf.py — Compile an Obsidian vault into a print-style PDF via
WeasyPrint. Originally built to match the layout of SS1's professionally
typeset reference PDF:

    /home/rum/Dropbox/TEXT/SS/SSBK1/SSI_backup/
        Silent Subversion I-SECRETS WANT OUT_Hyrum Jones-6.pdf

That layout (fonts, margin ratios, POV sign-image feature) is now the
default house style, driven per book by Book Info.md fields rather than
hardcoded to SS1 specifically.

Like obsidian_to_epub.py, the compile order/structure comes entirely from
"Manuscript Reading Order.md" at the vault root. This script renders an HTML
document with CSS Paged Media rules (page size, running headers, a
page-numbered Contents page via target-counter()) and converts it to PDF
with WeasyPrint.

Fonts: Linux Biolinum (bold display headings) and EB Garamond (body text +
italics) — both open-source substitutes/matches for the reference PDF's
embedded fonts. The reference also used Palatino Linotype and Sitka Text
(proprietary, unavailable on Linux); EB Garamond Italic substitutes for both,
per user decision 2026-09-01.
    sudo pacman -S ttf-linux-libertine
    paru -S ebgaramond-otf

Each chapter's POV character can optionally get an individual "sign" glyph
(a small circular emblem) inserted between their name and the date line,
using pre-rendered PNGs from a directory declared in the vault's Book Info.md
(pov_signs_dir/pov_signs fields) — this is entirely optional per book; a
vault with no pov_signs_dir set just renders without them.

Requirements:
  - Python 3.10+
  - weasyprint (pip/pacman: python-weasyprint)

Usage:
    python3 obsidian_to_pdf.py <vault> [--output-dir DIR]

Like obsidian_to_epub.py, all book-specific settings come from the vault's
Book Info.md (see obsidian_to_epub.py's docstring for the base fields:
title/author/author_file_as/publisher/isbn/cover/output_dir/series). This
script additionally reads, all optional:
    subtitle: "Secrets Want Out"          # title-page subtitle line
    title_page_lines: "SILENT|SUBVERSION" # title split across 2 lines (pipe-separated);
                                           # defaults to the title alone, uppercased, on one line
    series_position: "1"                  # title-page dot row: this book's position...
    series_length: "3"                    # ...out of this many (blank on either = no dot row)
    trim_size: "5.25in 8in"                # CSS @page size (width height); default "5.25in 8in"
    running_header: "SILENT SUBVERSION 1" # chapter-page running header text; defaults to
                                           # title.upper() if not given
    pov_signs_dir: "/home/rum/Dropbox/TEXT/SS/YourSign"
    pov_signs: "Gerald=Gerald-Sign.png, Taylor=Taylor-Sign.png"  # comma-separated name=file pairs
"""

import argparse
import html
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pymupdf as fitz
import weasyprint

from obsidian_to_epub import parse_book_info

FRONTMATTER_TITLE_RE = re.compile(r'^title:\s*"(.*)"\s*$', re.MULTILINE)
ISBN_EBOOK_RE = re.compile(r'^(ISBN \(eBook\):\s*).*$', re.MULTILINE)
CUT_SPAN_RE = re.compile(r"~~.*?~~")
COMMENT_RE = re.compile(r"%%.*?%%")
DASH_RE = re.compile(r"-{2,}")  # Obsidian's editor won't accept a literal em
                                 # dash, so the vault uses "--" as a stand-in.

FM_BULLET_RE = re.compile(r'^- \[\[([^\]|]+)(?:\|[^\]]+)?\]\]\s+_([^_]+)_\s*$')
SCENE_BULLET_RE = re.compile(r'^- \[\[([^\]|]+)(?:\|[^\]]+)?\]\]\s*$')
PART_TITLE_RE = re.compile(r'^(Part\s+[IVXLCDM]+)\s+(.+)$', re.IGNORECASE)

# A paragraph that's a text/email message: either (a) entirely wrapped in a
# single pair of asterisks, or (b) a "Name: *message*" line — a sender name
# in plain text followed by a colon and the italicized message, the vault's
# convention for multi-person text/chat exchanges. Consecutive runs of
# either form get grouped into a .correspondence div so they read as
# visually distinct from the surrounding narrative, the way the blank line
# around them already sets them apart when reading the raw markdown. A
# little trailing punctuation after the closing "*" (a period, question
# mark, etc. that landed outside the italics by hand) is tolerated.
CORRESPONDENCE_RE = re.compile(
    r"^(?:[A-Za-z][\w .'-]{0,40}:\s+)?\*[^*]+\*[.,!?;:\"')]*\s*$"
)

CENTERED_HIDDEN_HEADING_TITLES = {"Information", "Acknowledgments"}
CENTERED_VISIBLE_HEADING_TITLES = {"About the Author", "Continue Reading"}


@dataclass
class Chapter:
    title: str
    pov: str = ""
    subtitle_lines: list = field(default_factory=list)
    scenes: list = field(default_factory=list)


@dataclass
class Part:
    title: str
    epigraph: list = field(default_factory=list)
    chapters: list = field(default_factory=list)


def strip_frontmatter(text: str) -> tuple[str, str]:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            fm = text[:end]
            m = FRONTMATTER_TITLE_RE.search(fm)
            title = m.group(1) if m else ""
            return title, text[end + 5:]
    return "", text


def smart_quotes(text: str) -> str:
    """Convert straight " / ' to proper curly typographic quotes. The vault's
    source .md files use straight quotes throughout (confirmed by inspecting
    scene files directly) — but the reference PDF (checked via PyMuPDF text
    extraction) uses real curly “ ” quotes everywhere. Unlike the epub
    scripts (which get this for free via pandoc), this script has no
    markdown-processing layer, so it needs its own conversion — hand-rolled
    here (a simplified smartypants-style heuristic) rather than depending on
    the python-smartypants package, which emits HTML entities rather than
    plain Unicode characters and would complicate the later dropcap-letter
    character-slicing logic in render_scene()."""
    result = []
    n = len(text)
    for i, ch in enumerate(text):
        prev = text[i - 1] if i > 0 else ""
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == '"':
            # A quote right after a dash is always dialogue cut off by an
            # interruption (confirmed by checking every "—\"" occurrence in
            # both SilentSub1 and SilentSub2 — none are a dash introducing a
            # *new* quote) — so it must close, not open, despite the dash
            # otherwise acting like other opening-bracket characters below.
            if prev != "" and prev in "-—":
                result.append("”")
            elif prev == "" or prev.isspace() or prev in "([{":
                result.append("“")
            else:
                result.append("”")
        elif ch == "'":
            if prev.isalpha() or prev.isdigit():
                result.append("’")  # apostrophe: contraction/possessive
            elif prev != "" and prev in "-—":
                result.append("’")  # closing single quote — see the "'"'" case above
            elif nxt.isalpha() and (prev == "" or prev.isspace() or prev in "([{"):
                result.append("‘")  # opening single quote
            else:
                result.append("’")  # closing single quote (default)
        else:
            result.append(ch)
    return "".join(result)


def strip_cuts(body: str) -> str:
    lines = []
    for line in body.split("\n"):
        line = CUT_SPAN_RE.sub("", line)
        line = COMMENT_RE.sub("", line)
        line = DASH_RE.sub("—", line)
        line = smart_quotes(line)
        if line.strip():
            lines.append(line)
    return "\n".join(lines)


def paragraphs(body: str) -> list:
    """Split the vault's single-newline-per-paragraph body into a list of
    paragraph strings (each still containing internal markdown emphasis)."""
    body = strip_cuts(body).strip()
    return [p for p in body.split("\n") if p.strip()]


def strip_cuts_lines(body: str) -> list:
    """Like strip_cuts, but returns a list of lines with genuine blank lines
    preserved as "" — used for front/back-matter items, which (unlike
    Manuscript scenes) use blank lines deliberately to group tightly-related
    short lines (e.g. title+copyright, or the edition/ISBN block) and set
    them apart from unrelated ones, rather than as a paragraph-per-line
    editing convenience."""
    lines = []
    for raw in body.split("\n"):
        if not raw.strip():
            lines.append("")
            continue
        line = CUT_SPAN_RE.sub("", raw)
        line = COMMENT_RE.sub("", line)
        line = DASH_RE.sub("—", line)
        line = smart_quotes(line)
        if line.strip():
            lines.append(line)
    return lines


def paragraph_groups(body: str) -> list:
    """Group consecutive non-blank lines together (a run with no blank line
    between them) — a blank line in the source marks the boundary between
    groups. Each group renders as one <p> (its lines joined with <br/>) so
    it reads as a tight block, while separate groups get the normal
    paragraph margin between them."""
    groups, current = [], []
    for line in strip_cuts_lines(body):
        if not line:
            if current:
                groups.append(current)
                current = []
        else:
            current.append(line)
    if current:
        groups.append(current)
    return groups


def md_inline_to_html(text: str) -> str:
    """Minimal markdown inline handling: escape HTML, then restore *italic*,
    **bold**, and [text](url) spans — the only forms used in this vault's
    scene/front/back-matter text."""
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', escaped)
    return escaped


def parse_reading_order(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    front_matter: list[str] = []
    back_matter: list[str] = []
    parts: list[Part] = []

    current_part = None
    current_chapter = None
    past_title = False

    i, n = 0, len(lines)
    while i < n:
        stripped = lines[i].strip()

        if not stripped:
            i += 1
            continue

        fm_m = FM_BULLET_RE.match(stripped)
        if fm_m:
            fname, section = fm_m.group(1), fm_m.group(2)
            if section == "Front Matter":
                front_matter.append(fname)
            elif section == "Back Matter":
                back_matter.append(fname)
            i += 1
            continue

        if stripped.startswith("## "):
            current_chapter = Chapter(title=stripped[3:].strip())
            if current_part is not None:
                current_part.chapters.append(current_chapter)
            i += 1
            header_lines = []
            while i < n:
                s2 = lines[i].strip()
                if not s2:
                    i += 1
                    continue
                if s2.startswith("#") or SCENE_BULLET_RE.match(s2) or FM_BULLET_RE.match(s2):
                    break
                header_lines.append(s2)
                i += 1
            if header_lines:
                current_chapter.pov = header_lines[0]
            current_chapter.subtitle_lines = header_lines[1:]
            continue

        if stripped.startswith("# "):
            if not past_title:
                past_title = True
                i += 1
                continue
            current_part = Part(title=stripped[2:].strip())
            parts.append(current_part)
            current_chapter = None
            i += 1
            epigraph_lines = []
            while i < n:
                st2 = lines[i].strip()
                if not st2:
                    i += 1
                    continue
                if st2.startswith(">"):
                    epigraph_lines.append(st2[1:].strip())
                    i += 1
                    continue
                break
            current_part.epigraph = epigraph_lines
            continue

        scene_m = SCENE_BULLET_RE.match(stripped)
        if scene_m:
            if current_chapter is not None:
                current_chapter.scenes.append(scene_m.group(1))
            i += 1
            continue

        i += 1

    return front_matter, parts, back_matter


def index_vault_files(vault: Path) -> dict:
    index = {}
    for section in ("Front Matter", "Manuscript", "Back Matter"):
        d = vault / section
        if d.is_dir():
            for p in d.rglob("*.md"):
                if not p.name.startswith("_"):
                    index[p.stem] = p
    return index


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "x"


def parse_pov_signs(book_info: dict) -> tuple:
    """Parse the optional pov_signs_dir/pov_signs Book Info.md fields into
    (signs_dir, {name: filename}). Returns (None, {}) if the book has no
    sign images configured — sign_images_for() then always returns []."""
    signs_dir_str = book_info.get("pov_signs_dir", "")
    if not signs_dir_str:
        return None, {}
    signs_dir = Path(signs_dir_str)
    pov_sign = {}
    for pair in book_info.get("pov_signs", "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        name, _, fname = pair.partition("=")
        pov_sign[name.strip()] = fname.strip()
    return signs_dir, pov_sign


def sign_images_for(pov: str, signs_dir, pov_sign: dict) -> list:
    """Resolve one or more '-Sign.png' images for a (possibly multi-name,
    '-'-joined) POV credit line. Skips silently if a name isn't mapped, or
    if the book has no sign images configured at all (signs_dir is None)."""
    if signs_dir is None:
        return []
    images = []
    for name in [n.strip() for n in pov.split(" - ")]:
        fname = pov_sign.get(name)
        if fname:
            p = signs_dir / fname
            if p.is_file():
                images.append(p)
    return images


def apply_information_overrides(body: str, book_info: dict) -> str:
    """For the "Information" front-matter item only: if Book Info.md declares
    a 'series' field, that's treated as opting in to keeping Information.md's
    opening two lines — the book title, then the series/book-number line —
    and its "ISBN (eBook):" line in sync with Book Info.md automatically,
    instead of hand-edited independently. Vaults with no 'series' field
    (e.g. SS1/SS2) are returned unchanged — their Information.md pages
    predate this convention and have their own hand-tuned formatting (e.g.
    SS1's ISBN is written with dashes, which differs from Book Info.md's
    plain-digit isbn field, so blindly syncing it there would corrupt it).
    Same behavior as obsidian_to_epub.py's function of the same name — kept
    in sync with it by hand since these are separate scripts."""
    series = book_info.get("series", "")
    if not series:
        return body

    lines = body.split("\n")
    replacements = [book_info["title"].upper(), series]
    idx = 0
    for i, line in enumerate(lines):
        if line.strip():
            lines[i] = replacements[idx]
            idx += 1
            if idx == len(replacements):
                break
    body = "\n".join(lines)

    isbn = book_info.get("isbn", "")
    if isbn:
        body = ISBN_EBOOK_RE.sub(lambda m: m.group(1) + isbn, body)
    return body


def render_front_back_item(title: str, body: str) -> str:
    # Every front/back-matter item forces a right-hand-page start EXCEPT
    # Information, which just flows onto the next page after the title page
    # — matches the reference PDF's pagination exactly.
    recto = "" if title == "Information" else " recto"
    groups = paragraph_groups(body)
    render_group = lambda g: "<p>" + "<br/>".join(md_inline_to_html(l) for l in g) + "</p>"
    if title in CENTERED_HIDDEN_HEADING_TITLES:
        inner = "\n".join(render_group(g) for g in groups)
        return (
            f'<section class="fbm-page noheader{recto}" id="{slugify(title)}">'
            f'<h1 class="hidden-title">{html.escape(title)}</h1>'
            f'<div class="centered">{inner}</div></section>'
        )
    if title in CENTERED_VISIBLE_HEADING_TITLES:
        inner = "\n".join(render_group(g) for g in groups)
        return (
            f'<section class="fbm-page noheader{recto}" id="{slugify(title)}">'
            f'<h1 class="fbm-title">{html.escape(title)}</h1>'
            f'<div class="centered">{inner}</div></section>'
        )
    inner = "\n".join(render_group(g) for g in groups)
    return (
        f'<section class="fbm-page noheader{recto}" id="{slugify(title)}">'
        f'<h1 class="fbm-title">{html.escape(title)}</h1>'
        f'<div class="prose">{inner}</div></section>'
    )


def render_scene(fname: str, resolve, is_first: bool) -> str:
    _, body = strip_frontmatter(resolve(fname).read_text(encoding="utf-8"))
    paras = paragraphs(body)
    if not paras:
        return ""
    first, rest = paras[0], paras[1:]
    if is_first and first:
        # A real <span> wrapping the literal first character, not a
        # ::first-letter pseudo-element — WeasyPrint 69's ::first-letter
        # float has a confirmed vertical/horizontal layout bug (the glyph
        # either sits a full line too low, or overlaps the following
        # characters, depending on its computed line-height; see
        # compile_book_ss1_pdf.md memory for the isolated repro). A plain
        # floated span sidesteps the pseudo-element layout path entirely and
        # renders correctly.
        # If the paragraph opens with dialogue (a curly opening quote), the
        # reference PDF enlarges the quote mark AND the letter after it
        # together as one unit (confirmed via PyMuPDF per-glyph font-size
        # inspection: e.g. "“P" both render at the dropcap size) — not just
        # the quote mark alone, which would leave the actual first letter of
        # the sentence at normal size.
        span_len = 2 if first[0] in "“‘" else 1
        letter = html.escape(first[:span_len])
        remainder_html = md_inline_to_html(first[span_len:])
        first_para_html = f'<span class="dropcap-letter">{letter}</span>{remainder_html}'
        out = [f'<p class="dropcap">{first_para_html}</p>']
    else:
        out = [f'<p class="noindent">{md_inline_to_html(first)}</p>']
    i, n = 0, len(rest)
    while i < n:
        if CORRESPONDENCE_RE.match(rest[i].strip()):
            run = []
            while i < n and CORRESPONDENCE_RE.match(rest[i].strip()):
                run.append(rest[i])
                i += 1
            inner = "\n".join(f"<p>{md_inline_to_html(p)}</p>" for p in run)
            out.append(f'<div class="correspondence">{inner}</div>')
        else:
            out.append(f"<p>{md_inline_to_html(rest[i])}</p>")
            i += 1
    return "\n".join(out)


def build_html(vault: Path, book_info: dict) -> tuple:
    reading_order_path = vault / "Manuscript Reading Order.md"
    if not reading_order_path.is_file():
        sys.exit(f"ERROR: {reading_order_path} not found — it defines the compile order.")

    # Named book_title/book_subtitle, not title/subtitle — this function
    # reuses those shorter names as per-item loop variables below (front/back
    # matter titles, Part subtitles), which would otherwise shadow these.
    book_title = book_info["title"]
    author = book_info["author"]
    book_subtitle = book_info.get("subtitle", "")
    title_lines = book_info.get("title_page_lines", "").split("|")
    if len(title_lines) < 2:
        title_lines = [book_title.upper(), ""]
    series_position = book_info.get("series_position", "")
    series_length = book_info.get("series_length", "")
    signs_dir, pov_sign = parse_pov_signs(book_info)

    front_matter, parts, back_matter = parse_reading_order(reading_order_path)
    file_index = index_vault_files(vault)

    def resolve(fname: str) -> Path:
        p = file_index.get(fname)
        if p is None:
            sys.exit(f"ERROR: '{fname}' is referenced in Manuscript Reading Order.md "
                      f"but no matching file was found in the vault.")
        return p

    sections = []
    chapter_titles = []  # ordered, for locating each chapter's opening page afterward

    # Title page — the dot row is a series-progress indicator (filled = this
    # book, hollow = the others), matching the original design. Omitted
    # entirely if the book has no series_position/series_length set.
    dots_html = ""
    if series_position and series_length:
        dots = "".join(
            "●" if i == int(series_position) else "○"
            for i in range(1, int(series_length) + 1)
        )
        dots_html = f'<p class="title-dots">{dots}</p>'
    sections.append(
        '<section class="title-page noheader">'
        f'<h1 class="title-main">{title_lines[0]}</h1>'
        f'<h1 class="title-last">{title_lines[1]}</h1>'
        f'<p class="title-subtitle">{html.escape(book_subtitle)}</p>'
        f'<p class="title-author">{html.escape(author)}</p>'
        f"{dots_html}"
        "</section>"
    )

    for fname in front_matter:
        title, body = strip_frontmatter(resolve(fname).read_text(encoding="utf-8"))
        if title == "Information":
            body = apply_information_overrides(body, book_info)
        sections.append(render_front_back_item(title, body))

    # Contents page — lists Parts only, with page numbers via target-counter()
    toc_items = []
    for idx, part in enumerate(parts, start=1):
        m = PART_TITLE_RE.match(part.title)
        roman = m.group(1).split()[-1] if m else str(idx)
        subtitle = m.group(2) if m else part.title
        anchor = f"part-{idx}"
        toc_items.append(
            f'<p class="toc-entry"><a href="#{anchor}">{html.escape(roman)}: '
            f'{html.escape(subtitle.upper())}</a></p>'
        )
    sections.append(
        '<section class="fbm-page noheader recto" id="contents">'
        '<h1 class="fbm-title">Contents</h1>'
        f'<div class="toc">{"".join(toc_items)}</div></section>'
    )

    # NOTE: chapter-opening pages show the running header just like
    # continuation pages (a deliberate simplification). WeasyPrint 69's
    # ":first" page pseudo-class only means "the literal first page of the
    # whole document" (confirmed via isolated testing) — it does NOT support
    # "first page of this named page type", which is what would be needed to
    # suppress the header on each of the 81 chapters' own opening page. A
    # reliable fix would need a two-pass render (determine each chapter's
    # actual page number, then post-process the PDF) — left as a possible
    # follow-up rather than blocking on it now.

    for idx, part in enumerate(parts, start=1):
        m = PART_TITLE_RE.match(part.title)
        heading, subtitle = (m.group(1).upper(), m.group(2)) if m else (part.title, "")
        anchor = f"part-{idx}"
        epigraph_html = ""
        if part.epigraph:
            lines = "<br/>".join(md_inline_to_html(l) for l in part.epigraph)
            epigraph_html = f'<p class="epigraph">{lines}</p>'
        # Part I resets the visible page counter to 1 — see the CSS ".partone"
        # comment for why it needs a page name distinct from other Parts.
        part_page_class = "partone" if idx == 1 else "noheader"
        sections.append(
            f'<section class="part-page {part_page_class} recto" id="{anchor}">'
            f'<h1 class="part-heading">{html.escape(heading)}</h1>'
            f'<p class="part-subtitle">{html.escape(subtitle)}</p>'
            f"{epigraph_html}</section>"
        )

        for chapter_idx, chapter in enumerate(part.chapters):
            # The first chapter of a part also gets forced onto a fresh
            # right-hand page (like the part-opener itself) — matches the
            # reference's blank page between the Part page and Chapter One.
            recto_class = " recto" if chapter_idx == 0 else ""
            chapter_titles.append(chapter.title.upper())

            sign_html = ""
            for img in sign_images_for(chapter.pov, signs_dir, pov_sign):
                sign_html += f'<img class="pov-sign" src="{img.as_uri()}" alt=""/>'
            pov_html = f'<p class="povname">{html.escape(chapter.pov)}</p>' if chapter.pov else ""
            date_html = ""
            if chapter.subtitle_lines:
                date_lines = "<br/>".join(html.escape(line) for line in chapter.subtitle_lines)
                date_html = f'<p class="chapterdate">{date_lines}</p>'

            scenes_html = []
            for i, fname in enumerate(chapter.scenes):
                scenes_html.append(render_scene(fname, resolve, is_first=(i == 0)))
                if i < len(chapter.scenes) - 1:
                    scenes_html.append('<p class="sep">—※—</p>')

            sections.append(
                f'<section class="chapter-page{recto_class}">'
                f'<h2 class="chapter-heading">{html.escape(chapter.title.upper())}</h2>'
                f"{pov_html}{sign_html}{date_html}"
                f'<div class="prose">{"".join(scenes_html)}</div></section>'
            )

    for fname in back_matter:
        title, body = strip_frontmatter(resolve(fname).read_text(encoding="utf-8"))
        sections.append(render_front_back_item(title, body))

    doc_html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>{html.escape(book_title)}</title></head>
<body>
{"".join(sections)}
</body>
</html>"""
    return doc_html, chapter_titles


CSS_TEMPLATE = """
@page {
  size: __TRIM_SIZE__;
  /* top right bottom left — fallback only; :left/:right below define the
     real (mirrored) margins every actual page gets. */
  margin: __MARGIN_TOP__ __MARGIN_OUTER__ __MARGIN_TOP__ __MARGIN_INNER__;
}
/* Mirrored margins, print-book style: the inner (spine-side) margin is
   wider than the outer margin, both scaled from trim size (see
   margins_for_trim_size() — ratios originally measured off SS1's reference
   PDF's text-block positions at 5.25in x 8in: outer ~0.5in, inner ~0.75in,
   top/bottom ~0.65in). On a left/verso page the spine is on the right, so
   the inner margin is on the right; on a right/recto page the spine is on
   the left. */
@page :left {
  margin-left: __MARGIN_OUTER__;
  margin-right: __MARGIN_INNER__;
}
@page :right {
  margin-left: __MARGIN_INNER__;
  margin-right: __MARGIN_OUTER__;
}

/* The default (unnamed) page has NO running header. This matters beyond
   front/back matter and Part pages: WeasyPrint's auto-inserted blank pages
   (from break-before:right forcing a right-hand start) fall back to this
   default page type regardless of what "page" name the surrounding content
   uses — confirmed via isolated testing — so making the default headerless
   is what keeps those blank pages clean, not the .noheader rule below. */

/* Only chapter pages opt into a running header — page number at the outer
   edge (left on verso pages, right on recto), book/author name centered. */
@page chapterbody:left {
  @top-left { content: counter(page); font-family: 'Linux Biolinum O'; font-weight: bold; font-size: 8.5pt; letter-spacing: 1px; color: #333; }
  @top-center { content: "__AUTHOR__"; font-family: 'Linux Biolinum O'; font-weight: bold; font-size: 8.5pt; letter-spacing: 1px; color: #333; }
}
@page chapterbody:right {
  @top-center { content: "__RUNNING_HEADER__"; font-family: 'Linux Biolinum O'; font-weight: bold; font-size: 8.5pt; letter-spacing: 1px; color: #333; }
  @top-right { content: counter(page); font-family: 'Linux Biolinum O'; font-weight: bold; font-size: 8.5pt; letter-spacing: 1px; color: #333; }
}
@page noheader {
  margin-top: 1in;
}

/* Part I's page also resets the visible page counter to 1 — matches the
   reference, where front matter is unnumbered and body pagination starts
   fresh at the first Part. MUST have its own unique page name distinct from
   other Parts: combining counter-reset with a page name shared by multiple
   elements corrupts every later target-counter() lookup (confirmed via
   isolated testing) — that's why Contents' page numbers broke the first
   time this was attempted. */
@page partone {
  margin-top: 1in;
  counter-reset: page 1;
}

body {
  font-family: 'EB Garamond';
  font-size: 10.5pt;
  line-height: 1.35;
  text-align: justify;
  color: #111;
}

section {
  break-before: page;
}

.noheader {
  page: noheader;
}

.chapter-page {
  page: chapterbody;
}

.partone {
  page: partone;
}

/* Most front matter (Acknowledgments/Contents) and every Part opening
   always start on a right-hand page, print-book style — matches the blank
   pages in the reference PDF. The Information/copyright page is the one
   exception (it just flows onto the next page after the title page,
   matching the reference), and chapters aren't forced either (too many to
   justify the paper cost, and the reference doesn't force them). */
.title-page, .recto {
  break-before: right;
}

p {
  margin: 0;
  text-indent: 1.1em;
}

h1, h2 {
  text-align: center;
  font-family: 'Linux Biolinum O';
  font-weight: bold;
}

/* Title page */
.title-page { text-align: center; margin-top: 1.2in; }
/* The generic "p { text-indent: 1.1em; }" rule below otherwise leaks into
   these three (all <p> tags) — for a single centered line, that inherited
   indent shifts the visual center rightward by roughly half the indent
   (measured ~7pt off-center on a 5.25in page; confirmed as the cause by an
   isolated repro without this reset, which reproduced the exact offset). */
.title-page p { text-indent: 0; }
.title-main, .title-last {
  font-size: 30pt;
  letter-spacing: 6px;
  margin: 0;
}
.title-subtitle {
  font-family: 'EB Garamond';
  font-style: italic;
  font-size: 15pt;
  margin-top: 0.3in;
}
.title-author {
  font-family: 'Linux Biolinum O';
  font-weight: bold;
  font-size: 13pt;
  margin-top: 0.9in;
}
.title-dots {
  font-size: 13pt;
  letter-spacing: 6px;
  margin-top: 0.15in;
}

/* Front/back matter */
.fbm-title { font-size: 15pt; margin-bottom: 0.4in; }
.hidden-title { display: none; }
.centered p, .prose.centered p { text-align: center; text-indent: 0; margin-bottom: 0.15in; }
.fbm-page .prose p { text-indent: 0; margin-bottom: 0.1in; text-align: center; }

/* Contents */
#contents .toc-entry { text-align: center; text-indent: 0; margin: 0.08in 0; }
#contents .toc-entry a { color: inherit; text-decoration: none; }
#contents .toc-entry a::after { content: "\\2014" target-counter(attr(href), page); }

/* Part title pages */
.part-page { text-align: center; margin-top: 1.4in; }
.part-heading { font-size: 22pt; margin: 0; }
.part-subtitle {
  font-family: 'Linux Biolinum O';
  font-weight: bold;
  font-size: 22pt;
  text-align: center;
  text-indent: 0;
  margin-top: 0.6in;
}
.epigraph {
  font-family: 'EB Garamond';
  font-style: italic;
  font-size: 11pt;
  text-align: center;
  text-indent: 0;
  line-height: 1.8;
  margin-top: 0.8in;
}

/* Chapter pages */
.chapter-heading { font-size: 17pt; margin-top: 0.3in; margin-bottom: 0.35in; }
.povname {
  font-family: 'Linux Biolinum O';
  font-weight: bold;
  font-size: 13pt;
  text-align: center;
  text-indent: 0;
  margin-bottom: 0.12in;
}
.pov-sign {
  display: block;
  width: 0.55in;
  margin: 0.1in auto;
}
.chapterdate {
  font-size: 9.5pt;
  text-indent: 0;
  margin-top: 0.15in;
  margin-bottom: 0.3in;
}

.dropcap-letter {
  /* Raised-cap style, not a traditional floated drop cap: the letter sits
     inline on the paragraph's own first line (no wraparound into line 2/3)
     and extends upward above it, since a large inline span simply grows
     its line box upward from the shared baseline. No float, no explicit
     height needed — those were only for the old multi-line-wrap version. */
  font-family: 'EB Garamond';
  font-size: 3em;
  line-height: 1;
  vertical-align: baseline;
  margin-right: 0.08em;
}
.noindent, .dropcap { text-indent: 0; }

.sep {
  text-align: center;
  text-indent: 0;
  margin: 0.18in 0;
}

.correspondence {
  margin: 0.15in 0;
}
.correspondence p {
  text-indent: 0;
}

em { font-style: italic; }
a { color: inherit; text-decoration: none; }
strong { font-weight: bold; }
"""


# Mirrored-margin ratios, measured directly off SS1's reference PDF at its
# 5.25in x 8in trim size (outer ~0.5in, inner ~0.75in, top/bottom ~0.65in —
# see compile_book_ss1_pdf.md). Applied proportionally to whatever trim_size
# a book declares, so a different page size gets sensibly scaled margins
# instead of SS1's exact fixed inch values carrying over unchanged.
OUTER_MARGIN_RATIO = 0.5 / 5.25    # of page width
INNER_MARGIN_RATIO = 0.75 / 5.25   # of page width (wider: spine-side gutter)
VERTICAL_MARGIN_RATIO = 0.65 / 8.0  # of page height, both top and bottom


def margins_for_trim_size(trim_size: str) -> dict:
    """trim_size is "<width>in <height>in" (e.g. "5.25in 8in"). Returns CSS
    length strings for the outer, inner (spine-side), and top/bottom margins,
    scaled proportionally from the ratios measured off SS1's own layout."""
    width_in, height_in = (float(p.replace("in", "")) for p in trim_size.split())
    return {
        "outer": f"{width_in * OUTER_MARGIN_RATIO:.3f}in",
        "inner": f"{width_in * INNER_MARGIN_RATIO:.3f}in",
        "top": f"{height_in * VERTICAL_MARGIN_RATIO:.3f}in",
    }


def build_css(trim_size: str, author: str, running_header: str) -> str:
    margins = margins_for_trim_size(trim_size)
    return (
        CSS_TEMPLATE
        .replace("__TRIM_SIZE__", trim_size)
        .replace("__AUTHOR__", author.upper())
        .replace("__RUNNING_HEADER__", running_header)
        .replace("__MARGIN_OUTER__", margins["outer"])
        .replace("__MARGIN_INNER__", margins["inner"])
        .replace("__MARGIN_TOP__", margins["top"])
    )


def blank_chapter_opening_headers(pdf_path: Path, chapter_titles: list) -> None:
    """WeasyPrint's ':first' page pseudo-class only means 'the literal first
    page of the whole document' (confirmed via isolated testing), not 'first
    page of this named page type' — so there's no reliable pure-CSS way to
    suppress the running header on each of the 81 chapters' own opening page
    while keeping it on continuation pages. Post-process instead: find each
    chapter's opening page by its heading text (unique across the book, and
    only ever appearing near the top of a page at a chapter's own opening —
    continuation pages always start mid-sentence; checking the first several
    extracted lines rather than just the first handles PyMuPDF's text order
    putting the floated dropcap letter ahead of the heading), then white out
    the header band there. chapter_titles must be in the same order they appear in the
    book, so a title reappearing anywhere in body prose can't cause a
    false-positive match out of sequence.
    """
    doc = fitz.open(str(pdf_path))
    header_band = fitz.Rect(0, 0, 378, 61.2)  # full top margin, 5.25in x 8in page
    next_idx = 0
    for page in doc:
        if next_idx >= len(chapter_titles):
            break
        lines = [l.strip() for l in page.get_text("text").split("\n") if l.strip()]
        if chapter_titles[next_idx] in lines[:5]:
            page.draw_rect(header_band, color=None, fill=(1, 1, 1), overlay=True)
            next_idx += 1
    if next_idx < len(chapter_titles):
        print(f"  WARNING: only matched {next_idx}/{len(chapter_titles)} chapter "
              f"openings — some headers may not have been removed.")
    doc.saveIncr()
    doc.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compile an Obsidian vault into a print-style PDF (WeasyPrint). "
                    "Book metadata is read from <vault>/Book Info.md — see this file's module docstring.")
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

    trim_size = book_info.get("trim_size") or "5.25in 8in"
    running_header = book_info.get("running_header") or book_info["title"].upper()

    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    output = (output_dir / f"{book_info['title']}_{timestamp}.pdf").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    print("Assembling manuscript...")
    doc_html, chapter_titles = build_html(vault, book_info)

    print("Rendering PDF (weasyprint)...")
    weasyprint.HTML(string=doc_html, base_url=str(vault)).write_pdf(
        str(output), stylesheets=[weasyprint.CSS(string=build_css(trim_size, book_info["author"], running_header))]
    )

    print("Removing running header from each chapter's opening page...")
    blank_chapter_opening_headers(output, chapter_titles)

    print(f"Done. Written: {output}")


if __name__ == "__main__":
    main()
