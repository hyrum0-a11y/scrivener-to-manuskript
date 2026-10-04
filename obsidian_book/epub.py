#!/usr/bin/env python3
"""
obsidian_to_epub.py — Compile a vault produced by epub_to_obsidian.py back
into a distributable epub.

The compile order and structure come entirely from "Manuscript Reading
Order.md" at the vault root — NOT from folder names. That note is the
backbone:

    - [[file|Title]]  _Front Matter_      (before the first Part heading)
    # Part <roman> <NAME>                 (H1 = Part)
    > epigraph line                       (blockquote right after a Part
    > epigraph line                        heading, becomes the centered
    > epigraph line                        double-spaced tercet)
    ## <Chapter name>                     (H2 = Chapter)
    <POV name>                            (plain line = POV name, bold/centered)
    <optional secondary line>             (plain line = e.g. a date, left-aligned)
    - [[scene-file]]                      (scene bullets, in reading order)
    - [[scene-file]]
    - [[file|Title]]  _Back Matter_       (after the last chapter)

The Part heading is optional: a book that isn't divided into parts just
leaves it out, and chapters listed before any Part heading compile straight
under the book with no Part page.

Each scene file is located by filename anywhere under the vault (Front
Matter/, Manuscript/, Back Matter/) — its folder path is irrelevant to
ordering, only the note's bullet order matters.

The vault stores prose with a single newline between paragraphs (no blank
line) for easy Obsidian editing. Pandoc's markdown reader treats runs of
non-blank lines as ONE paragraph, so before compiling we re-insert a blank
line between every paragraph — the exact inverse of the vault's on-disk
convention (same technique used by compile_book.py for Sky's the Limit).

Requirements:
  - Python 3.10+
  - pandoc  (https://pandoc.org/installing.html)

Usage:
    python3 obsidian_to_epub.py <vault> [--output-dir DIR]
    python3 obsidian_to_epub.py <vault> --check

--check is a dry run: it validates Book Info.md and Manuscript Reading
Order.md, reports reading-order links that point at missing files, unlinked
vault files, and a missing cover image, then exits without running pandoc or
writing anything (exit status 1 if any errors were found).

Book-specific settings (title, author, ISBN, publisher, cover, default
output location) are NOT in this script — they live in a "Book Info.md"
note at the root of each vault, inside a ```book-info code block (a plain
frontmatter --- block at the top of the file would make Obsidian treat it
as a Properties panel, so a code fence is used instead — it's inert to
Obsidian anywhere in the note):

    ```book-info
    title: "Silent Subversion I"
    author: "Hyrum Jones"
    author_file_as: "Jones, Hyrum"
    publisher: "Anxiety Publishing"
    isbn: "9780997210712"
    cover: "SS1-final-ebook-small.jpg"
    output_dir: "/home/user/Books/MyNovel/exports"
    ```

title/author/author_file_as/publisher/cover are required; isbn may be left
blank (""). cover is resolved relative to the vault root unless given as an
absolute path. output_dir is the default destination (blank means
~/Downloads); pass --output-dir to override it for a single run. Several
```book-info blocks in the note are read as one (the starter vault splits
required and optional fields). Front and back matter, including the
Information (copyright) page, are printed exactly as written: nothing in
them is filled in from Book Info.md. This keeps the
same script usable for every book — one vault, one Book Info.md, no
per-book copy of this file.
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

CSS = Path(__file__).parent / "epub_style.css"  # first-line indent, no paragraph spacing
UNTRUSTED_FILTER = Path(__file__).parent / "untrusted.lua"  # see build_epub(untrusted=True)


class BookError(Exception):
    """A problem with the vault or the build that the author needs to fix.
    The message is meant to be shown as-is (CLI prints it; a web tool can
    return it to the uploader)."""

BOOK_INFO_REQUIRED_KEYS = ("title", "author", "author_file_as", "publisher", "cover")
BOOK_INFO_OPTIONAL_KEYS = {"isbn": "", "output_dir": "", "scene_break": "", "chapter_space_above": ""}
LINE_SPACING = 1.35  # body line height as a multiple of the type size, in every format
DEFAULT_SCENE_BREAK = "—※—"
DEFAULT_OUTPUT_DIR = "~/Downloads"  # when Book Info.md's output_dir is blank

BOOK_INFO_BLOCK_RE = re.compile(r"```book[- ]info\s*\n(.*?)```", re.DOTALL)
BOOK_INFO_FIELD_RE = re.compile(r'^([a-z_]+):\s*"(.*)"\s*$', re.MULTILINE)
FRONTMATTER_TITLE_RE = re.compile(r'^title:\s*"(.*)"\s*$', re.MULTILINE)
CUT_SPAN_RE = re.compile(r"~~.*?~~")
COMMENT_RE = re.compile(r"%%.*?%%")
DASH_RE = re.compile(r"-{2,}")  # Obsidian's editor won't accept a literal em
                                 # dash, so the vault uses "--" as a stand-in.

FM_BULLET_RE = re.compile(r'^- \[\[([^\]|]+)(?:\|[^\]]+)?\]\]\s+_([^_]+)_\s*$')
SCENE_BULLET_RE = re.compile(r'^- \[\[([^\]|]+)(?:\|[^\]]+)?\]\]\s*$')

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

# A lone non-breaking space renders as an empty-but-present paragraph — this
# is literally how the published SS1/SS2 epubs create the blank-line gaps
# around the chapter number and POV name (their <p class="br"><br/></p>),
# rather than via a bigger CSS margin.
BLANK_LINE = " "

# Splits a Part title like "Part I MOMENTUM" into ("Part I", "MOMENTUM") —
# the published epubs render these as two separate elements (roman-numeral
# heading, blank space, subtitle), not one combined heading line.
PART_TITLE_RE = re.compile(r'^(Part\s+[IVXLCDM]+)\s+(.+)$', re.IGNORECASE)

# Front/back-matter titles that get no visible on-page heading, just centered
# text (matches the "Information"/"Acknowledgments" copyright-and-legal page
# convention in the published SS1/SS2 epubs).
CENTERED_HIDDEN_HEADING_TITLES = {"Information", "Acknowledgments"}
# Titles that get a visible heading plus centered text (matches "About the
# Author" in both published epubs).
CENTERED_VISIBLE_HEADING_TITLES = {"About the Author", "Continue Reading"}
# Anything else (e.g. "Summary of Book 1") falls back to a visible heading
# plus regular justified/indented prose, like a chapter.


@dataclass
class Chapter:
    title: str
    lines: list = field(default_factory=list)    # [("center" | "left", text)] printed under the title
    scenes: list = field(default_factory=list)
    ignored: list = field(default_factory=list)  # unlabelled lines left out (see parse_reading_order)

    @property
    def pov(self) -> str:
        """The first centred line without its formatting (e.g. for POV sign images)."""
        return next((re.sub(r"[*_]", "", t).strip() for align, t in self.lines if align == "center"), "")


@dataclass
class Part:
    title: str
    lines: list = field(default_factory=list)    # [("center" | "left", text)] printed under the title
    chapters: list = field(default_factory=list)
    # True for the stand-in Part holding chapters listed before any Part
    # heading (a book with no part divisions): it gets no Part page.
    implicit: bool = False
    ignored: list = field(default_factory=list)


def line_groups(lines: list) -> list:
    """[(align, [text, ...])]: runs of consecutive lines with the same alignment."""
    groups = []
    for align, text in lines:
        if groups and groups[-1][0] == align:
            groups[-1][1].append(text)
        else:
            groups.append((align, [text]))
    return groups


def strip_frontmatter(text: str) -> tuple[str, str]:
    """Return (title, body) — title from YAML frontmatter, body is everything after it."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            fm = text[:end]
            m = FRONTMATTER_TITLE_RE.search(fm)
            title = m.group(1) if m else ""
            return title, text[end + 5:]
    return "", text


def scene_break(book_info: dict) -> str:
    """The symbol printed between scenes: Book Info.md's scene_break, else —※—.
    "blank" means just an empty line (a non-breaking space, so it isn't dropped)."""
    value = book_info.get("scene_break", "").strip()
    if value.lower() in ("blank", "blank line", "empty", "space"):
        return BLANK_LINE
    return value or DEFAULT_SCENE_BREAK


def page_headers(book_info: dict) -> tuple:
    """(left-page header, right-page header) for the PDF, Word and ODT:
    Book Info.md's header_left / header_right exactly as typed; blank means
    the author's name / the title in capitals. An older vault's
    running_header still sets the right-hand one."""
    left = book_info.get("header_left", "").strip() or book_info["author"].upper()
    right = (book_info.get("header_right", "").strip() or book_info.get("running_header", "").strip()
             or book_info["title"].upper())
    return left, right


def chapter_space_above(book_info: dict):
    """Book Info.md's chapter_space_above: how many empty lines go above each
    chapter title, as an int, or None to keep each format's usual spacing."""
    value = book_info.get("chapter_space_above", "").strip()
    if not value:
        return None
    if not value.isdigit() or int(value) > 12:
        raise BookError(f"ERROR: chapter_space_above in Book Info.md should be a number of empty lines "
                        f"from 0 to 12, like \"5\" (it's \"{value}\").")
    return int(value)


def markdown_literal(text: str) -> str:
    """Text for pandoc Markdown with every punctuation mark backslash-escaped,
    so a scene break like "* * *" or "#" prints as typed (not as a rule or heading)."""
    return "".join("\\" + c if not c.isalnum() and not c.isspace() and ord(c) < 128 else c for c in text)


def parse_book_info(vault: Path) -> dict:
    """Read <vault>/Book Info.md and return its frontmatter fields as a dict.
    Raises BookError if the file is missing or a required field is absent."""
    path = vault / "Book Info.md"
    if not path.is_file():
        raise BookError(f"ERROR: {path} not found — it holds this book's title/author/"
                        f"ISBN/cover/output_dir. See the module docstring for the format.")
    text = path.read_text(encoding="utf-8")
    blocks = BOOK_INFO_BLOCK_RE.findall(text)  # several blocks are read as one (e.g. required + optional)
    if not blocks:
        raise BookError(f"ERROR: {path} has no ```book-info code block. "
                        f"See the module docstring for the format.")
    info = {k: v for block in blocks for k, v in BOOK_INFO_FIELD_RE.findall(block)}

    missing = [k for k in BOOK_INFO_REQUIRED_KEYS if not info.get(k)]
    if missing == ["cover"]:
        raise BookError(f"ERROR: cover is blank in {path}. Every book needs a cover: put your cover image "
                        f'in the vault\'s top folder (e.g. cover.jpg) and set cover: "cover.jpg".')
    if missing:
        raise BookError(f"ERROR: {path} is missing required field(s): {', '.join(missing)}")

    for key, default in BOOK_INFO_OPTIONAL_KEYS.items():
        info.setdefault(key, default)
    return info


def strip_cuts(body: str) -> str:
    """Remove ~~struck~~ text and %%comments%% (editorial markup, never
    published) before paragraphs are reassembled. Must run before
    expand_paragraphs() — a fully-struck line would otherwise leave a
    stray empty paragraph, so any line that becomes blank after stripping
    is dropped entirely rather than kept as an empty paragraph."""
    lines = []
    for line in body.split("\n"):
        line = CUT_SPAN_RE.sub("", line)
        line = COMMENT_RE.sub("", line)
        line = DASH_RE.sub("—", line)
        if line.strip():
            lines.append(line)
    return "\n".join(lines)


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
        if line.strip():
            lines.append(line)
    return lines


def paragraph_groups(body: str) -> list:
    """Group consecutive non-blank lines together (a run with no blank line
    between them) — a blank line in the source marks the boundary between
    groups. Each group becomes one markdown paragraph with hard line breaks
    between its lines (so it reads as a tight block), while separate groups
    get a real blank line between them (a normal paragraph break, with the
    usual margin)."""
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


def render_paragraph_groups(body: str) -> str:
    """Render front/back-matter body text as markdown: each group's lines
    joined with a hard break ("  \\n"), groups separated by a blank line."""
    return "\n\n".join("  \n".join(g) for g in paragraph_groups(body))


def expand_paragraphs(body: str) -> str:
    """Re-insert blank lines between paragraphs (inverse of the vault's
    single-newline convention) so pandoc treats each line as its own paragraph."""
    body = body.strip()
    return re.sub(r"\n(?!\n)", "\n\n", body)


def split_first_paragraph(body: str) -> tuple[str, str]:
    """Split off the first paragraph so it can be wrapped in its own div
    (for no-indent / drop-cap styling) separately from the rest of the scene."""
    first, _, rest = body.partition("\n\n")
    return first, rest


def group_correspondence(body: str) -> str:
    """Given an already-expand_paragraphs'd (blank-line-separated) block,
    wrap consecutive correspondence paragraphs in a .correspondence div so
    they render as a visually distinct block instead of ordinary indented
    narrative paragraphs."""
    paras = body.split("\n\n")
    chunks = []
    run: list[str] = []

    def flush_run():
        if run:
            chunks.append("::: {.correspondence}\n" + "\n\n".join(run) + "\n:::")
            run.clear()

    for p in paras:
        if CORRESPONDENCE_RE.match(p.strip()):
            run.append(p)
        else:
            flush_run()
            chunks.append(p)
    flush_run()
    return "\n\n".join(chunks)


LABEL_RE = re.compile(r"^(center|left|right):\s*(.*)$", re.IGNORECASE)


def reading_order_lines(path: Path) -> list:
    """The Reading Order's lines with %%comments%% removed, including
    comment blocks spanning several lines (like the starter vault's help)."""
    return re.sub(r"%%.*?%%", "", path.read_text(encoding="utf-8"), flags=re.S).splitlines()


def reading_order_is_legacy(lines: list) -> bool:
    """True for an older Reading Order (temporary, see parse_reading_order):
    no center:/left: label anywhere, and unlabelled lines under at least half
    of its chapters (and at least two), as POV/date lines were written. A
    stray note under one chapter of a new book doesn't count."""
    if any(LABEL_RE.match(line.strip()) for line in lines):
        return False
    chapters = with_lines = 0
    in_head = counted = False
    for line in lines:
        text = line.strip()
        if text.startswith("## "):
            chapters, in_head, counted = chapters + 1, True, False
        elif text.startswith("#") or SCENE_BULLET_RE.match(text) or FM_BULLET_RE.match(text):
            in_head = False
        elif in_head and text and not counted:
            with_lines, counted = with_lines + 1, True
    return with_lines >= max(2, chapters / 2)


def parse_reading_order(path: Path):
    """Parse Manuscript Reading Order.md into (front_matter, parts, back_matter).
    front_matter/back_matter are lists of filenames (no extension); parts is
    a list of Part, each holding its Chapters in reading order.

    Lines under a part or chapter heading that start with "center:",
    "left:" or "right:" are printed under its title, in that alignment, keeping their
    Obsidian *italic* / **bold** formatting. Other lines there are ignored
    (collected in .ignored so the book check can list them). %%comments%%
    are removed first, so notes to yourself are safe anywhere.

    Older vaults (see reading_order_is_legacy) keep the old reading: under
    a chapter, the first plain line is centred in bold (a POV name) and the
    rest are left-aligned (dates); under a part, ">" lines are a centred
    italic epigraph. (Temporary, until those vaults are converted.)"""
    lines = reading_order_lines(path)
    legacy = reading_order_is_legacy(lines)
    front_matter: list[str] = []
    back_matter: list[str] = []
    parts: list[Part] = []

    current_part = None
    current_chapter = None

    def heading_lines(i: int, target, is_chapter: bool) -> int:
        """Read the lines under a heading into target.lines / .ignored; return the next index."""
        plain = []
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            if line.startswith("#") or SCENE_BULLET_RE.match(line) or FM_BULLET_RE.match(line):
                break
            label = LABEL_RE.match(line)
            if label and label.group(2).strip():
                target.lines.append((label.group(1).lower(), label.group(2).strip()))
            elif label:
                pass  # an empty "center:" prints nothing
            elif legacy and is_chapter:
                plain.append(line)
            elif legacy and line.startswith(">"):
                target.lines.append(("center", f"*{line[1:].strip()}*"))
            else:
                target.ignored.append(line)
            i += 1
        if plain:
            target.lines = [("center", f"**{plain[0]}**")] + [("left", t) for t in plain[1:]]
        return i

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
            if current_part is None:
                current_part = Part(title="", implicit=True)
                parts.append(current_part)
            current_part.chapters.append(current_chapter)
            i = heading_lines(i + 1, current_chapter, True)
            continue

        if stripped.startswith("# "):
            if re.search(r"reading order\s*$", stripped, re.IGNORECASE):
                i += 1  # the note's own title ("# My Book — Reading Order"), not a part
                continue
            current_part = Part(title=stripped[2:].strip())
            parts.append(current_part)
            current_chapter = None
            i = heading_lines(i + 1, current_part, False)
            continue

        scene_m = SCENE_BULLET_RE.match(stripped)
        if scene_m:
            if current_chapter is not None:
                current_chapter.scenes.append(scene_m.group(1))
            i += 1
            continue

        i += 1  # e.g. "*by Author*" byline — not structural

    return front_matter, parts, back_matter


def index_vault_files(vault: Path) -> dict:
    """Map filename (no extension) -> Path, for every .md file under the
    vault's Front Matter/, Manuscript/, and Back Matter/ trees."""
    index = {}
    for section in ("Front Matter", "Manuscript", "Back Matter"):
        d = vault / section
        if d.is_dir():
            for p in d.rglob("*.md"):
                if not p.name.startswith("_"):
                    index[p.stem] = p
    return index


def find_orphaned_files(file_index: dict, referenced: set) -> list:
    """Vault files with non-empty content that aren't referenced anywhere in
    Manuscript Reading Order.md — these get silently left out of the compiled
    book. Files whose body is empty after stripping frontmatter (intentional
    placeholders) are not reported."""
    orphans = []
    for stem, path in file_index.items():
        if stem in referenced:
            continue
        _, body = strip_frontmatter(path.read_text(encoding="utf-8"))
        if body.strip():
            orphans.append(path)
    return orphans


TITLE_PAGE = "Title Page"  # a front-matter note with this title replaces the built-in title page


def parse_title_page(body: str) -> list:
    """The writer's Title Page note as [(kind, text)], kind one of "big"
    (# line), "medium" (## line), "small" (### line), "normal" (plain line),
    "space" (an empty line) or "rule" (--- or ***). Leading and trailing
    empty lines are dropped; %%comments%% and ~~cuts~~ are removed."""
    items = []
    for raw in body.split("\n"):
        if re.fullmatch(r"\s*(-{3,}|\*{3,}|_{3,})\s*", raw):
            items.append(("rule", ""))
            continue
        line = re.sub(r"\s{2,}", " ", DASH_RE.sub("—", COMMENT_RE.sub("", CUT_SPAN_RE.sub("", raw)))).strip()
        if not line:
            items.append(("space", ""))
        else:
            m = re.match(r"(#{1,6})\s+(.*)", line)
            if m:
                kind = {1: "big", 2: "medium"}.get(len(m.group(1)), "small")
                items.append((kind, m.group(2).strip()))
            else:
                items.append(("normal", line))
    while items and items[0][0] == "space":
        items.pop(0)
    while items and items[-1][0] == "space":
        items.pop()
    return items


def render_title_page(body: str) -> list:
    """EPUB markdown for the writer's Title Page note (styled by epub_style.css)."""
    lines = []
    for kind, text in parse_title_page(body):
        if kind == "space":
            lines.append("::: {.tp-space}\n\u00a0\n:::")
        elif kind == "rule":
            lines.append("::: {.tp-rule}\n⁂\n:::")
        else:
            lines.append(f"::: {{.tp-{kind}}}\n{text}\n:::")
    return [f"# {TITLE_PAGE} {{.hidden-title}}", "::: {.titlepage}\n" + "\n\n".join(lines) + "\n:::"]


def render_front_back_item(title: str, body: str) -> list:
    """Render a Front/Back Matter item's markdown chunks per its title's
    house style (see CENTERED_HIDDEN_HEADING_TITLES / CENTERED_VISIBLE_HEADING_TITLES)."""
    prose = render_paragraph_groups(body)
    if title in CENTERED_HIDDEN_HEADING_TITLES:
        return [f"# {title} {{.hidden-title}}", f"::: {{.centered}}\n{prose}\n:::"]
    if title in CENTERED_VISIBLE_HEADING_TITLES:
        heading = f"# {title} {{.fbm-title}}" if title else ""
        return [heading, f"::: {{.centered}}\n{prose}\n:::"]
    heading = f"# {title} {{.fbm-title}}" if title else ""
    return [heading, prose]


def referenced_in_order(front_matter: list, parts: list, back_matter: list) -> list:
    """Every filename the reading order links to, in reading order, with
    the section it appears under (for error messages)."""
    refs = [(f, "Front Matter") for f in front_matter]
    for part in parts:
        for chapter in part.chapters:
            where = chapter.title if part.implicit else f"{part.title} / {chapter.title}"
            refs.extend((s, where) for s in chapter.scenes)
    refs.extend((f, "Back Matter") for f in back_matter)
    return refs


def resolve_cover(vault: Path, book_info: dict):
    """Return the cover image Path from Book Info.md (or None if unset)."""
    cover_str = book_info["cover"]
    if not cover_str:
        return None
    cover = Path(cover_str)
    if not cover.is_absolute():
        cover = vault / cover
    return cover.resolve()


# The starter vault's example text: a reminder (never an error) if it's still in the book.
PLACEHOLDER_RE = re.compile(r"\[(YOUR TITLE|Your Name|Year|Month Year|ISBN|Your Publisher)\]|Replace this\b"
                            r"|Lorem ipsum|Nemo enim ipsam")
PLACEHOLDER_BOOK_INFO = {"title": "My Novel", "author": "Your Name", "author_file_as": "Last, First"}
# The starter Reading Order's example lines under its part and chapter titles.
PLACEHOLDER_ORDER_RE = re.compile(r"Part I The Beginning|A short epigraph|can go here|center: Anna\b|London, 1952")


def placeholder_leftovers(vault: Path, book_info: dict, notes: list) -> list:
    """['Book Info.md: author is still "Your Name"', 'Front Matter/Information.md: [Year], ...']"""
    found = [f'Book Info.md: {key} is still "{value}"' for key, value in PLACEHOLDER_BOOK_INFO.items()
             if book_info.get(key) == value]
    for path in notes:
        hits = list(dict.fromkeys(m.group(0) for m in PLACEHOLDER_RE.finditer(path.read_text(encoding="utf-8"))))
        if hits:
            found.append(f"{path.relative_to(vault)}: {', '.join(hits)}")
    order = "\n".join(reading_order_lines(vault / "Manuscript Reading Order.md"))
    hits = list(dict.fromkeys(m.group(0).removeprefix("center: ") for m in PLACEHOLDER_ORDER_RE.finditer(order)))
    if hits:
        found.append(f"Manuscript Reading Order.md: the example lines {', '.join(hits)}")
    return found


def check_vault(vault: Path, book_info: dict) -> int:
    """Check the reading order against the vault and report problems.
    Returns the number of errors (missing linked files, a missing cover
    image); notes left out of the reading order are warnings only."""
    reading_order_path = vault / "Manuscript Reading Order.md"
    if not reading_order_path.is_file():
        raise BookError(f"ERROR: {reading_order_path} not found — it defines the compile order.")

    front_matter, parts, back_matter = parse_reading_order(reading_order_path)
    file_index = index_vault_files(vault)
    refs = referenced_in_order(front_matter, parts, back_matter)

    missing = [(f, where) for f, where in refs if f not in file_index]
    if missing:
        print("ERROR: Manuscript Reading Order.md links to these notes, but no "
              "matching file exists under Front Matter/, Manuscript/ or Back Matter/:")
        for fname, where in missing:
            print(f"  - [[{fname}]]  (under {where})")
        print("  Fix the link spelling, or create/rename the note to match.")

    orphans = find_orphaned_files(file_index, {f for f, _ in refs})
    if orphans:
        print("WARNING: these vault files have content but aren't referenced in "
              "Manuscript Reading Order.md, so they will NOT be in the compiled book:")
        for p in sorted(orphans, key=lambda p: str(p.relative_to(vault))):
            print(f"  - {p.relative_to(vault)}")

    ignored = [(p.title or "the book", line) for p in parts for line in p.ignored]
    ignored += [(c.title, line) for p in parts for c in p.chapters for line in c.ignored]
    if ignored:
        print("WARNING: these lines in Manuscript Reading Order.md aren't printed. To print a line under a "
              "part or chapter title, start it with center:, left: or right: (formatting like *italic* works):")
        for where, line in ignored:
            print(f"  - under {where}: {line}")
    if reading_order_is_legacy(reading_order_lines(reading_order_path)):
        old = [(p.title, f"{align}: {t}") for p in parts for align, t in p.lines]
        old += [(c.title, f"{align}: {t}") for p in parts for c in p.chapters for align, t in c.lines]
        if old:
            print("WARNING: lines under part and chapter titles in Manuscript Reading Order.md have no "
                  "center: or left: label. They're printed the old way for now. To keep them as they are, "
                  "write them like this:")
            for where, text in old[:20]:
                print(f"  - under {where}: {text}")
            if len(old) > 20:
                print(f"  - ...and {len(old) - 20} more")

    leftovers = placeholder_leftovers(vault, book_info, [file_index[f] for f, _ in refs if f in file_index])
    if leftovers:
        print("WARNING: the starter vault's example text is still in your book. Replace it before "
              "publishing:")
        for item in leftovers:
            print(f"  - {item}")

    cover = resolve_cover(vault, book_info)
    cover_missing = bool(cover) and not cover.is_file()
    if cover_missing:
        print(f"ERROR: the cover image '{book_info['cover']}' named in Book Info.md isn't in the vault. "
              f"Put your cover image in the vault's top folder and make cover: match its file name exactly.")

    return len(missing) + cover_missing


def build_document(vault: Path, book_info: dict) -> str:
    reading_order_path = vault / "Manuscript Reading Order.md"
    front_matter, parts, back_matter = parse_reading_order(reading_order_path)
    file_index = index_vault_files(vault)

    def resolve(fname: str) -> Path:
        return file_index[fname]  # check_vault() has already verified every link

    chunks = []

    for fname in front_matter:
        title, body = strip_frontmatter(resolve(fname).read_text(encoding="utf-8"))
        chunks.extend(render_title_page(body) if title == TITLE_PAGE else render_front_back_item(title, body))

    for part in parts:
        m = PART_TITLE_RE.match(part.title)
        heading, subtitle = (m.group(1).upper(), m.group(2)) if m else (part.title, "")
        if not part.implicit:
            chunks.append(f"# {heading}")
        if subtitle:
            chunks.append(BLANK_LINE)
            chunks.append(BLANK_LINE)
            chunks.append(f"::: {{.partsubtitle}}\n**{subtitle}**\n:::")

        for align, texts in line_groups(part.lines):
            chunks.append(BLANK_LINE)
            chunks.append(BLANK_LINE)
            css = {"center": "epigraph", "left": "partleft", "right": "partright"}[align]
            chunks.append(f"::: {{.{css}}}\n" + "\n".join(f"{t}  " for t in texts) + "\n:::")

        for chapter in part.chapters:
            chunks.append(f"## {chapter.title}")
            groups = line_groups(chapter.lines)
            if groups:
                chunks.append(BLANK_LINE)
                chunks.append(BLANK_LINE)
            for k, (align, texts) in enumerate(groups):
                css = {"center": "povname", "left": "chapterdate", "right": "chapterright"}[align]
                chunks.append(f"::: {{.{css}}}\n" + "  \n".join(texts) + "\n:::")
                if align == "center":
                    chunks.append(BLANK_LINE)
                    if k < len(groups) - 1:
                        chunks.append(BLANK_LINE)
            if groups:
                chunks.append(BLANK_LINE)
                chunks.append(BLANK_LINE)

            scenes = chapter.scenes
            for i, fname in enumerate(scenes):
                _, body = strip_frontmatter(resolve(fname).read_text(encoding="utf-8"))
                body = expand_paragraphs(strip_cuts(body))
                first_para, rest = split_first_paragraph(body)
                css_class = "dropcap" if i == 0 else "noindent"
                chunks.append(f"::: {{.{css_class}}}\n{first_para}\n:::")
                if rest:
                    chunks.append(group_correspondence(rest))
                if i < len(scenes) - 1:
                    sep = scene_break(book_info)
                    css = "sep-blank" if sep == BLANK_LINE else "sep"
                    chunks.append(f"::: {{.{css}}}\n{markdown_literal(sep)}\n:::")

    for fname in back_matter:
        title, body = strip_frontmatter(resolve(fname).read_text(encoding="utf-8"))
        chunks.extend(render_front_back_item(title, body))

    return "\n\n".join(c for c in chunks if c)


def load_vault(vault) -> tuple[Path, dict]:
    """Resolve a vault path and read its Book Info.md. Raises BookError if the
    folder isn't a vault or Book Info.md is unusable."""
    vault = Path(vault).expanduser().resolve()
    if not (vault / "Manuscript").is_dir():
        raise BookError(f"ERROR: {vault} does not look like a vault (no Manuscript/ folder)")
    return vault, parse_book_info(vault)


def resolve_output_dir(book_info: dict, output_dir=None) -> Path:
    """The --output-dir override if given, else Book Info.md's output_dir,
    else the Downloads folder."""
    output_dir_str = str(output_dir) if output_dir else (book_info["output_dir"] or DEFAULT_OUTPUT_DIR)
    return Path(output_dir_str).expanduser().resolve()


def check(vault) -> None:
    """Dry run: validate Book Info.md and the reading order without pandoc or
    writing anything. Prints warnings; raises BookError if the vault can't build."""
    vault, book_info = load_vault(vault)
    errors = check_vault(vault, book_info)
    if shutil.which("pandoc") is None:
        print("WARNING: pandoc is not installed, so a real build would fail. "
              "Install it from https://pandoc.org/installing.html")
    if errors:
        raise BookError(f"Check failed: {errors} problem(s), listed above.")
    build_document(vault, book_info)  # exercises the renderer without pandoc
    print("Check passed: the vault is ready to compile.")


def check_untrusted_cover(vault: Path, book_info: dict) -> None:
    """For uploaded vaults: the cover must be a relative path inside the
    vault, so `cover: /etc/passwd` can't pull a server file into the epub."""
    cover_str = book_info["cover"]
    if not cover_str:
        return
    if Path(cover_str).is_absolute() or ".." in Path(cover_str).parts or "\\" in cover_str:
        raise BookError(f"ERROR: cover '{cover_str}' in Book Info.md must be a file inside the vault "
                        f"(e.g. \"cover.jpg\"), not an absolute path or one using '..'.")
    if not resolve_cover(vault, book_info).is_relative_to(vault):
        raise BookError(f"ERROR: cover '{cover_str}' in Book Info.md points outside the vault.")


def safe_filename(name: str) -> str:
    """A title usable as a file name: no path separators or characters
    Windows rejects, so a title like "Either/Or" can't write elsewhere."""
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .") or "book"


def build_epub(vault, output_dir=None, *, untrusted: bool = False) -> Path:
    """Compile a vault to an epub and return the written file's path.
    output_dir overrides Book Info.md's output_dir. Raises BookError on any
    problem the author needs to fix. This is the entry point for callers
    other than the CLI (e.g. the web tool).

    untrusted=True is for vaults uploaded by strangers: the cover must sit
    inside the vault, YAML metadata blocks inside notes are ignored (they
    could set cover-image/css to a server file), and untrusted.lua drops
    images and raw HTML that point at absolute paths, '..' or URLs.
    pandoc's own --sandbox can't be used here: it also refuses --css and
    --epub-cover-image (checked on pandoc 3.1 and 3.8)."""
    vault, book_info = load_vault(vault)
    if untrusted:
        check_untrusted_cover(vault, book_info)
    errors = check_vault(vault, book_info)
    if errors:
        raise BookError(f"Not building: fix the {errors} broken link(s) above first "
                        f"(run with --check to re-test without building).")

    if shutil.which("pandoc") is None:
        raise BookError("ERROR: pandoc is not installed (or not on your PATH). It does the actual "
                        "epub conversion.\n  Install it from https://pandoc.org/installing.html, "
                        "then run this again.")

    title, author = book_info["title"], book_info["author"]
    author_file_as, publisher, isbn = book_info["author_file_as"], book_info["publisher"], book_info["isbn"]

    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    output = (resolve_output_dir(book_info, output_dir) / f"{safe_filename(title)}_{timestamp}.epub").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    print("Assembling manuscript...")
    content = build_document(vault, book_info)
    # Contents normally lists Parts only; chapters outside any Part (a book
    # with no part divisions) need the chapter level in it too.
    _, parts, _ = parse_reading_order(vault / "Manuscript Reading Order.md")
    toc_depth = 2 if any(p.implicit for p in parts) else 1

    # Supplying title/author via --epub-metadata (raw Dublin Core XML) instead
    # of --metadata avoids pandoc auto-generating a visible, page-turnable
    # title page — the published SS1/SS2 epubs go straight from cover to
    # Information with no separate title page. Values are XML-escaped so a
    # title like "Salt & Smoke" doesn't produce invalid metadata.
    title_x, author_x, file_as_x, publisher_x, isbn_x = map(
        xml_escape, (title, author, author_file_as, publisher, isbn))
    epub_meta = (
        f"<dc:title>{title_x}</dc:title>\n"
        f'<dc:creator id="author">{author_x}</dc:creator>\n'
        f'<meta refines="#author" property="role">aut</meta>\n'
        f'<meta refines="#author" property="file-as">{file_as_x}</meta>\n'
        f"<dc:language>en</dc:language>\n"
        f"<dc:publisher>{publisher_x}</dc:publisher>\n"
    )
    if isbn:
        epub_meta += f'<dc:identifier id="isbn">urn:isbn:{isbn_x}</dc:identifier>\n'

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / "book.md"
        meta_path = Path(tmp_dir) / "metadata.xml"
        tmp_path.write_text(content, encoding="utf-8")
        meta_path.write_text(epub_meta, encoding="utf-8")

        cmd = [
            "pandoc", str(tmp_path),
            "-o", str(output),
            f"--epub-metadata={meta_path}",
            "--metadata", "toc-title=Contents",
            "--toc", f"--toc-depth={toc_depth}",
            "--epub-chapter-level=2",
            "--css", str(CSS),
        ]
        space = chapter_space_above(book_info)
        if space is not None:
            extra_css = Path(tmp_dir) / "chapter-space.css"
            extra_css.write_text(f"h2 {{ margin-top: {space * LINE_SPACING:.2f}rem; }}\n", encoding="utf-8")
            cmd += ["--css", str(extra_css)]
        cover = resolve_cover(vault, book_info)
        if cover and cover.is_file():
            cmd += ["--epub-cover-image", str(cover)]
        if untrusted:
            cmd += ["--from", "markdown-yaml_metadata_block",
                    "--resource-path", str(vault),
                    "--lua-filter", str(UNTRUSTED_FILTER)]

        print("Running pandoc...")
        result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise BookError(f"pandoc error:\n{result.stderr}")

    print(f"Done. Written: {output}")
    return output


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Compile an Obsidian vault (from epub_to_obsidian.py) back into an epub. "
                    "Book metadata is read from <vault>/Book Info.md — see this file's module docstring.")
    parser.add_argument("vault", help="path to the Obsidian vault to compile")
    parser.add_argument("--output-dir", help="override the vault's Book Info.md output_dir for this run")
    parser.add_argument("--check", action="store_true",
                        help="dry run: validate Book Info.md and the reading order, "
                             "report problems, and exit without building")
    args = parser.parse_args(argv)

    try:
        if args.check:
            check(args.vault)
        else:
            build_epub(args.vault, args.output_dir)
    except BookError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
