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
    series: "Cloud World, Book 2"
    ```

title/author/author_file_as/publisher are required; isbn/cover/series may be
left blank ("") if not applicable. cover is resolved relative to the vault
root unless given as an absolute path. output_dir is the default
destination; pass --output-dir to override it for a single run. If series
is set, the vault's Front Matter "Information" note has its opening two
lines rewritten at compile time to match Book Info.md: the book title
(uppercased) on the first line, then series verbatim on the second — plus
its "ISBN (eBook):" line synced to isbn — so those facts only need to be
kept correct in this file, not hand-edited in Information.md too. series
is written as-is, so it should read as the full second line, e.g. "The
Saldari Theater, Book 1", not just the bare series name. Leave series blank
to skip that rewrite entirely (Information.md is left untouched, as for
SS1/SS2, whose Information pages predate this convention). This keeps the
same script usable for every book — one vault, one Book Info.md, no
per-book copy of this file.
"""

import argparse
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

CSS = Path(__file__).parent / "epub_style.css"  # first-line indent, no paragraph spacing

BOOK_INFO_REQUIRED_KEYS = ("title", "author", "author_file_as", "publisher")
BOOK_INFO_OPTIONAL_KEYS = {"isbn": "", "cover": "", "output_dir": "", "series": ""}

BOOK_INFO_BLOCK_RE = re.compile(r"```book[- ]info\s*\n(.*?)```", re.DOTALL)
BOOK_INFO_FIELD_RE = re.compile(r'^([a-z_]+):\s*"(.*)"\s*$', re.MULTILINE)
ISBN_EBOOK_RE = re.compile(r'^(ISBN \(eBook\):\s*).*$', re.MULTILINE)
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
    pov: str = ""
    subtitle_lines: list = field(default_factory=list)
    scenes: list = field(default_factory=list)


@dataclass
class Part:
    title: str
    epigraph: list = field(default_factory=list)
    chapters: list = field(default_factory=list)


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


def parse_book_info(vault: Path) -> dict:
    """Read <vault>/Book Info.md and return its frontmatter fields as a dict.
    Exits with a clear error if the file is missing or a required field is absent."""
    path = vault / "Book Info.md"
    if not path.is_file():
        sys.exit(f"ERROR: {path} not found — it holds this book's title/author/"
                  f"ISBN/cover/output_dir. See the module docstring for the format.")
    text = path.read_text(encoding="utf-8")
    m = BOOK_INFO_BLOCK_RE.search(text)
    if m is None:
        sys.exit(f"ERROR: {path} has no ```book-info code block. "
                  f"See the module docstring for the format.")
    info = {k: v for k, v in BOOK_INFO_FIELD_RE.findall(m.group(1))}

    missing = [k for k in BOOK_INFO_REQUIRED_KEYS if not info.get(k)]
    if missing:
        sys.exit(f"ERROR: {path} is missing required field(s): {', '.join(missing)}")

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


def parse_reading_order(path: Path):
    """Parse Manuscript Reading Order.md into (front_matter, parts, back_matter).
    front_matter/back_matter are lists of filenames (no extension); parts is
    a list of Part, each holding its Chapters in reading order."""
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


def apply_information_overrides(body: str, book_info: dict) -> str:
    """For the "Information" front-matter item only: if Book Info.md declares
    a 'series' field, that's treated as opting in to keeping Information.md's
    opening two lines — the book title, then the series/book-number line —
    and its "ISBN (eBook):" line in sync with Book Info.md automatically,
    instead of hand-edited independently. Vaults with no 'series' field
    (e.g. SS1/SS2) are returned unchanged — their Information.md pages
    predate this convention and have their own hand-tuned formatting (e.g.
    SS1's ISBN is written with dashes, which differs from Book Info.md's
    plain-digit isbn field used for epub metadata, so blindly syncing it
    there would corrupt it)."""
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


def build_document(vault: Path, book_info: dict) -> str:
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

    for fname in front_matter:
        title, body = strip_frontmatter(resolve(fname).read_text(encoding="utf-8"))
        if title == "Information":
            body = apply_information_overrides(body, book_info)
        chunks.extend(render_front_back_item(title, body))

    for part in parts:
        m = PART_TITLE_RE.match(part.title)
        heading, subtitle = (m.group(1).upper(), m.group(2)) if m else (part.title, "")
        chunks.append(f"# {heading}")
        if subtitle:
            chunks.append(BLANK_LINE)
            chunks.append(BLANK_LINE)
            chunks.append(f"::: {{.partsubtitle}}\n**{subtitle}**\n:::")

        if part.epigraph:
            chunks.append(BLANK_LINE)
            chunks.append(BLANK_LINE)
            poem = "\n".join(f"*{line}*  " for line in part.epigraph)
            chunks.append(f"::: {{.epigraph}}\n{poem}\n:::")

        for chapter in part.chapters:
            chunks.append(f"## {chapter.title.upper()}")
            if chapter.pov or chapter.subtitle_lines:
                chunks.append(BLANK_LINE)
                chunks.append(BLANK_LINE)
            if chapter.pov:
                chunks.append(f"::: {{.povname}}\n**{chapter.pov}**\n:::")
                chunks.append(BLANK_LINE)
            if chapter.pov and chapter.subtitle_lines:
                chunks.append(BLANK_LINE)
            if chapter.subtitle_lines:
                subtitle_md = "  \n".join(chapter.subtitle_lines)
                chunks.append(f"::: {{.chapterdate}}\n{subtitle_md}\n:::")
            if chapter.pov or chapter.subtitle_lines:
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
                    chunks.append("::: {.sep}\n—※—\n:::")

    for fname in back_matter:
        title, body = strip_frontmatter(resolve(fname).read_text(encoding="utf-8"))
        chunks.extend(render_front_back_item(title, body))

    return "\n\n".join(c for c in chunks if c)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compile an Obsidian vault (from epub_to_obsidian.py) back into an epub. "
                    "Book metadata is read from <vault>/Book Info.md — see this file's module docstring.")
    parser.add_argument("vault", help="path to the Obsidian vault to compile")
    parser.add_argument("--output-dir", help="override the vault's Book Info.md output_dir for this run")
    args = parser.parse_args()

    vault = Path(args.vault).expanduser().resolve()
    if not (vault / "Manuscript").is_dir():
        sys.exit(f"ERROR: {vault} does not look like a vault (no Manuscript/ folder)")

    book_info = parse_book_info(vault)
    title, author = book_info["title"], book_info["author"]
    author_file_as, publisher, isbn = book_info["author_file_as"], book_info["publisher"], book_info["isbn"]

    output_dir_str = args.output_dir or book_info["output_dir"]
    if not output_dir_str:
        sys.exit("ERROR: no output directory given — set output_dir in Book Info.md or pass --output-dir.")
    output_dir = Path(output_dir_str).expanduser().resolve()

    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    output = (output_dir / f"{title}_{timestamp}.epub").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    print("Assembling manuscript...")
    content = build_document(vault, book_info)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", encoding="utf-8", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    # Supplying title/author via --epub-metadata (raw Dublin Core XML) instead
    # of --metadata avoids pandoc auto-generating a visible, page-turnable
    # title page — the published SS1/SS2 epubs go straight from cover to
    # Information with no separate title page.
    epub_meta = (
        f"<dc:title>{title}</dc:title>\n"
        f'<dc:creator id="author">{author}</dc:creator>\n'
        f'<meta refines="#author" property="role">aut</meta>\n'
        f'<meta refines="#author" property="file-as">{author_file_as}</meta>\n'
        f"<dc:language>en</dc:language>\n"
        f"<dc:publisher>{publisher}</dc:publisher>\n"
    )
    if isbn:
        epub_meta += f'<dc:identifier id="isbn">urn:isbn:{isbn}</dc:identifier>\n'
    with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", encoding="utf-8", delete=False) as tmp_meta:
        tmp_meta.write(epub_meta)
        meta_path = tmp_meta.name

    cmd = [
        "pandoc", tmp_path,
        "-o", str(output),
        f"--epub-metadata={meta_path}",
        "--metadata", "toc-title=Contents",
        "--toc", "--toc-depth=1",
        "--epub-chapter-level=2",
        "--css", str(CSS),
    ]
    cover_str = book_info["cover"]
    cover = None
    if cover_str:
        cover = Path(cover_str)
        if not cover.is_absolute():
            cover = vault / cover
        cover = cover.resolve()
    if cover and cover.is_file():
        cmd += ["--epub-cover-image", str(cover)]

    print("Running pandoc...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    Path(tmp_path).unlink()
    Path(meta_path).unlink()

    if result.returncode != 0:
        sys.exit(f"pandoc error:\n{result.stderr}")

    print(f"Done. Written: {output}")


if __name__ == "__main__":
    main()
