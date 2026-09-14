#!/usr/bin/env python3
"""
epub_to_obsidian.py — Convert a published novel epub into an Obsidian vault:

    <output>/Manuscript/<idx> - Part <roman> <Subtitle>/<idx>-<idx> <Chapter>/<idx>-<idx>-<idx> - <Title>.md
    <output>/Front Matter/...
    <output>/Back Matter/...
    <output>/Manuscript Reading Order.md

Frontmatter on every scene/section file matches the convention used by
convert.py (Scrivener -> Obsidian): title / section / uuid.

Chapters are recovered from the epub's toc.ncx (nested navPoints: a "Part"
navPoint's children are its chapters). Scenes within a chapter are recovered
by splitting on the "—※—" scene-break marker that Scrivener's compiler
leaves in the compiled text.

Requirements:
  - Python 3.10+
  - pandoc  (https://pandoc.org/installing.html)

Usage:
    python3 epub_to_obsidian.py NOVEL.epub [--title "My Novel"] [--author "Your Name"] [--output ./output]
"""

import argparse
import re
import subprocess
import sys
import uuid
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

NCX_NS = "{http://www.daisy.org/z3986/2005/ncx/}"
OPF_NS = "{http://www.idpf.org/2007/opf}"

SEPARATOR_RE = re.compile(r"^-+※-+\s*$", re.MULTILINE)
BACKSLASH_LINE_RE = re.compile(r"^\\$", re.MULTILINE)
FENCE_RE = re.compile(r"^:::.*$", re.MULTILINE)
# Pandoc represents styled <span>s (e.g. italicized inner-thought text) as
# "[text]{style=...}" / "[text]{.class}" bracket spans, even inside body
# prose (not just headings) — strip the attribute wrapper, keep the text.
# When the original HTML had leading/trailing whitespace inside the <span>
# (e.g. "<span style=...>hmm,\" </span>"), that whitespace ends up sitting
# between the text and the emphasis asterisk that wraps the whole span,
# which disqualifies that asterisk from closing per CommonMark's flanking
# rules — so it silently fails to close and opens a new, wrong emphasis run
# instead. Capture an optional surrounding "*" on each side and, if the
# inner text has leading/trailing whitespace, relocate it to outside the
# asterisk instead of leaving it trapped against it.
INLINE_SPAN_RE = re.compile(r"(\*)?\[([^\]\n]*)\]\{[^}]*\}(\*)?")


def _dewrap_inline_span(match: re.Match) -> str:
    lead_star, inner, trail_star = match.group(1) or "", match.group(2), match.group(3) or ""
    core = inner.strip()
    leading_ws = inner[: len(inner) - len(inner.lstrip())]
    trailing_ws = inner[len(inner.rstrip()):]
    return f"{leading_ws}{lead_star}{core}{trail_star}{trailing_ws}"
# Pandoc conservatively backslash-escapes some punctuation (periods before
# ellipses, apostrophes, tildes) that needs no escaping in plain prose.
STRAY_ESCAPE_RE = re.compile(r"\\([.'~*_\[\]()#+!`-])")
HEADING_RE = re.compile(r"^(#+)\s*(.*)$")
ATTR_TAIL_RE = re.compile(r"\s*\{[^}]*\}\s*$")
BRACKET_ATTR_RE = re.compile(r"\[([^\]]*)\]\{[^}]*\}")
BRACKET_PLAIN_RE = re.compile(r"\[([^\]]*)\]")
BOLD_RE = re.compile(r"\*\*")
ITALIC_RE = re.compile(r"\*([^*]*)\*")

ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
         "XI", "XII", "XIII", "XIV", "XV"]


@dataclass
class NavPoint:
    title: str
    src: str  # href relative to the OPS/OEBPS content directory
    children: list = field(default_factory=list)


# ── epub container / toc parsing ────────────────────────────────────────────
def find_opf_path(zf: zipfile.ZipFile) -> str:
    container = ET.fromstring(zf.read("META-INF/container.xml"))
    rootfile = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
    return rootfile.get("full-path")


def parse_manifest_and_toc(zf: zipfile.ZipFile, opf_path: str):
    ops_dir = str(Path(opf_path).parent)
    opf = ET.fromstring(zf.read(opf_path))
    manifest = opf.find(f"{OPF_NS}manifest")

    ncx_href = None
    cover_href = None
    for item in manifest.findall(f"{OPF_NS}item"):
        if item.get("media-type") == "application/x-dtbncx+xml":
            ncx_href = item.get("href")
        if item.get("properties") == "cover-image":
            cover_href = item.get("href")

    if ncx_href is None:
        sys.exit("ERROR: No NCX (table of contents) found in epub manifest.")

    ncx_path = f"{ops_dir}/{ncx_href}" if ops_dir != "." else ncx_href
    ncx_root = ET.fromstring(zf.read(ncx_path))
    navmap = ncx_root.find(f"{NCX_NS}navMap")

    def walk(np_el):
        label_el = np_el.find(f"{NCX_NS}navLabel/{NCX_NS}text")
        label = (label_el.text or "").strip() if label_el is not None else ""
        content_el = np_el.find(f"{NCX_NS}content")
        src = content_el.get("src") if content_el is not None else ""
        children = [walk(c) for c in np_el.findall(f"{NCX_NS}navPoint")]
        return NavPoint(title=label, src=src, children=children)

    toc = [walk(n) for n in navmap.findall(f"{NCX_NS}navPoint")]
    return ops_dir, toc, cover_href


# ── HTML -> Markdown ────────────────────────────────────────────────────────
def html_to_markdown(html_bytes: bytes) -> str:
    result = subprocess.run(
        ["pandoc", "-f", "html", "-t", "markdown", "--wrap=none"],
        input=html_bytes, capture_output=True,
    )
    return result.stdout.decode("utf-8")


def clean_common(text: str) -> str:
    text = BACKSLASH_LINE_RE.sub("", text)
    text = FENCE_RE.sub("", text)
    text = INLINE_SPAN_RE.sub(_dewrap_inline_span, text)
    text = STRAY_ESCAPE_RE.sub(r"\1", text)
    # No blank lines between paragraphs (matches make_obsidian_vault.py's
    # convention: paragraphs flow together, one per line, no blank gaps).
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def extract_heading_text(line: str) -> str:
    m = HEADING_RE.match(line)
    text = m.group(2) if m else line
    text = ATTR_TAIL_RE.sub("", text)
    text = BRACKET_ATTR_RE.sub(r"\1", text)
    text = BRACKET_PLAIN_RE.sub(r"\1", text)
    text = BOLD_RE.sub("", text)
    text = ITALIC_RE.sub(r"\1", text)
    return text.strip()


def split_leading_headings(text: str) -> tuple[list[str], str]:
    """Consume leading heading/blank lines; return (heading texts, remaining body)."""
    lines = text.split("\n")
    headings = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line == "" or line == "\\":
            i += 1
            continue
        if line.startswith("#"):
            headings.append(extract_heading_text(line))
            i += 1
            continue
        break
    body = "\n".join(lines[i:])
    return [h for h in headings if h], body


def split_scenes(body: str) -> list[str]:
    parts = SEPARATOR_RE.split(body)
    return [clean_common(p) for p in parts if clean_common(p)]


# ── Filename sanitisation (same convention as convert.py) ──────────────────
def safe_name(title: str) -> str:
    name = title.strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return name[:80] or "untitled"


def frontmatter(title: str, section: str) -> str:
    safe_title = title.replace('"', "'")
    return (
        f'---\n'
        f'title: "{safe_title}"\n'
        f'section: "{section}"\n'
        f'uuid: "{str(uuid.uuid4()).upper()}"\n'
        f'---\n\n'
    )


# ── Export ───────────────────────────────────────────────────────────────
def export_front_back_item(zf, ops_dir, item: NavPoint, idx: int, dest: Path, section: str, reading_order: list):
    html_bytes = zf.read(f"{ops_dir}/{item.src}")
    md = clean_common(html_to_markdown(html_bytes))
    _, body = split_leading_headings(md)
    if not body.strip():
        body = md  # heading-stripping ate everything (e.g. a title-only page)
    fname = f"{idx:02d} - {safe_name(item.title)}.md"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / fname).write_text(frontmatter(item.title, section) + body, encoding="utf-8")
    reading_order.append(("file", section, item.title, fname))


def export_chapter(zf, ops_dir, chapter: NavPoint, part_idx: int, chapter_idx: int,
                    part_dir: Path, reading_order: list):
    html_bytes = zf.read(f"{ops_dir}/{chapter.src}")
    md = html_to_markdown(html_bytes)
    headings, body = split_leading_headings(md)
    # headings[0] (if present) is the redundant chapter-number label (e.g. "EIGHT");
    # headings[1] (if present) is the chapter's POV character name.
    pov = headings[1] if len(headings) >= 2 else None

    chapter_dir = part_dir / f"{part_idx:02d}-{chapter_idx:02d} {safe_name(chapter.title)}"
    chapter_dir.mkdir(parents=True, exist_ok=True)

    scenes = split_scenes(body)
    scene_files = []
    for scene_idx, scene_text in enumerate(scenes, start=1):
        title = pov if (scene_idx == 1 and pov) else f"Scene {scene_idx}"
        fname = f"{part_idx:02d}-{chapter_idx:02d}-{scene_idx:02d} - {safe_name(title)}.md"
        if "](http" in scene_text and len(scene_text) < 400:
            print(f"  WARNING: {fname} looks like a back-matter blurb (short, contains a link) "
                  f"— consider moving it out of Manuscript/ by hand.")
        (chapter_dir / fname).write_text(frontmatter(title, "Manuscript") + scene_text, encoding="utf-8")
        scene_files.append(fname)

    reading_order.append(("chapter", chapter.title, scene_files))


def export_part(zf, ops_dir, part: NavPoint, part_idx: int, manuscript_dir: Path, reading_order: list):
    html_bytes = zf.read(f"{ops_dir}/{part.src}")
    md = html_to_markdown(html_bytes)
    headings, _ = split_leading_headings(md)
    # headings ~= ["PART I", "MOMENTUM", "After the fire created", "You will see", "The beast illuminated"]
    subtitle = headings[1] if len(headings) >= 2 else ""
    epigraph = headings[2:]

    roman = ROMAN[part_idx] if part_idx < len(ROMAN) else str(part_idx)
    part_title = f"Part {roman}" + (f" {subtitle}" if subtitle else "")
    part_dir = manuscript_dir / f"{part_idx:02d} - {safe_name(part_title)}"
    part_dir.mkdir(parents=True, exist_ok=True)

    if epigraph:
        content = frontmatter("Epigraph", "Manuscript") + "\n".join(epigraph)
        (part_dir / "_epigraph.md").write_text(content, encoding="utf-8")

    reading_order.append(("part", part_title, epigraph))

    for chapter_idx, chapter in enumerate(part.children, start=1):
        export_chapter(zf, ops_dir, chapter, part_idx, chapter_idx, part_dir, reading_order)


def write_reading_order(reading_order: list, output: Path, title: str, author: str):
    lines = [f"# {title} — Reading Order", ""]
    if author:
        lines += [f"*by {author}*", ""]
    for entry in reading_order:
        kind = entry[0]
        if kind == "file":
            _, section, item_title, fname = entry
            lines.append(f"- [[{fname[:-3]}|{item_title}]]  _{section}_")
        elif kind == "part":
            _, part_title, epigraph = entry
            lines.append(f"\n# {part_title}\n")
            if epigraph:
                lines.append("> " + "  \n> ".join(epigraph))
                lines.append("")
        elif kind == "chapter":
            _, chapter_title, scene_files = entry
            lines.append(f"## {chapter_title}")
            for fname in scene_files:
                lines.append(f"- [[{fname[:-3]}]]")
            lines.append("")
    (output / "Manuscript Reading Order.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a novel epub into an Obsidian vault (Manuscript/Part/Chapter/Scene structure).",
        epilog="Example: python3 epub_to_obsidian.py 'My Novel.epub' --author 'Jane Smith'",
    )
    parser.add_argument("epub", metavar="NOVEL.epub", help="Path to the epub file")
    parser.add_argument("--title", default="", help="Book title (default: derived from epub metadata)")
    parser.add_argument("--author", default="", help="Author name")
    parser.add_argument("--output", default="", help="Output directory (default: 'output' next to the epub)")
    args = parser.parse_args()

    epub_path = Path(args.epub).resolve()
    if not epub_path.is_file():
        sys.exit(f"ERROR: {epub_path} is not a file")

    output = Path(args.output).resolve() if args.output else epub_path.parent / "output"
    output.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(epub_path) as zf:
        opf_path = find_opf_path(zf)
        ops_dir, toc, cover_href = parse_manifest_and_toc(zf, opf_path)

        # Drop the epub's own nav/contents page from the reading order.
        toc = [n for n in toc if not n.src.startswith("contents.xhtml")]

        parts = [n for n in toc if n.children]
        if not parts:
            sys.exit("ERROR: No Part-level navPoints with chapter children found in toc.ncx")
        first_part_i = toc.index(parts[0])
        last_part_i = toc.index(parts[-1])
        front_items = toc[:first_part_i]
        back_items = toc[last_part_i + 1:]

        title = args.title or "Untitled"
        author = args.author

        reading_order = []

        if front_items:
            fm_dir = output / "Front Matter"
            for idx, item in enumerate(front_items, start=1):
                print(f"[Front Matter] {item.title}")
                export_front_back_item(zf, ops_dir, item, idx, fm_dir, "Front Matter", reading_order)

        manuscript_dir = output / "Manuscript"
        for part_idx, part in enumerate(parts, start=1):
            print(f"[Manuscript] Part {part_idx}: {part.title} ({len(part.children)} chapters)")
            export_part(zf, ops_dir, part, part_idx, manuscript_dir, reading_order)

        if back_items:
            bm_dir = output / "Back Matter"
            for idx, item in enumerate(back_items, start=1):
                print(f"[Back Matter] {item.title}")
                export_front_back_item(zf, ops_dir, item, idx, bm_dir, "Back Matter", reading_order)

        if cover_href:
            cover_bytes = zf.read(f"{ops_dir}/{cover_href}")
            images_dir = output / "Front Matter" / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            (images_dir / Path(cover_href).name).write_bytes(cover_bytes)
            print(f"[Cover] {Path(cover_href).name}")

    write_reading_order(reading_order, output, title, author)
    print(f"\nAll done. Output in: {output}")


if __name__ == "__main__":
    main()
