#!/usr/bin/env python3
"""
convert.py — Convert a Scrivener 3 .scriv project to:
  1. Organized Markdown folder tree  (<output>/markdown/)
  2. Manuskript .msk project          (<output>/<title>.msk + <output>/<title>/)

Manuskript format (version 1):
  - <title>.msk  : small entry-point file containing "1"
  - <title>/     : project folder containing all data
      infos.txt, MANUSKRIPT, labels.txt, status.txt,
      settings.txt, summary.txt, plots.xml, world.opml,
      outline/<items>

Requirements:
  - Python 3.10+
  - pandoc  (https://pandoc.org/installing.html)

Usage:
    python3 convert.py PROJECT.scriv [--title "My Novel"] [--author "Your Name"] [--output ./output]
"""

import argparse
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# Module-level path variables — set by main() before any export runs.
DATA_DIR: Path
OUTPUT: Path

# Scrivener inline placeholder tags to strip from converted text
SCRIVENER_TAG_RE = re.compile(r"<[!/]?\$Scr_[^>]+>")


# ── Data model ─────────────────────────────────────────────────────────────────
@dataclass
class Node:
    uuid: str
    title: str
    type: str          # DraftFolder, Folder, Text, Image, …
    children: list = field(default_factory=list)


# ── XML parsing ────────────────────────────────────────────────────────────────
def parse_binder(xml_path: Path) -> list[Node]:
    """Return top-level binder nodes (Front Matter, Manuscript, etc.)."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    binder = root.find("Binder")
    if binder is None:
        sys.exit("ERROR: No <Binder> element found in .scrivx")
    return [_parse_item(el) for el in binder if el.tag == "BinderItem"]


def _parse_item(el: ET.Element) -> Node:
    uuid = el.get("UUID", "")
    itype = el.get("Type", "")
    title_el = el.find("Title")
    title = title_el.text.strip() if title_el is not None and title_el.text else "(untitled)"
    children_el = el.find("Children")
    children = []
    if children_el is not None:
        for child in children_el:
            if child.tag == "BinderItem":
                children.append(_parse_item(child))
    return Node(uuid=uuid, title=title, type=itype, children=children)


def find_draft(nodes: list[Node]) -> Node | None:
    for n in nodes:
        if n.type == "DraftFolder":
            return n
    return None


# Types to skip entirely when collecting extra sections
_SKIP_TYPES = {"DraftFolder", "ResearchFolder", "TrashFolder", "Text", "Image", "PDF"}
# Folder titles that are Scrivener internals, not user content
_SKIP_TITLES = {"template sheets"}


def find_extra_sections(nodes: list[Node]) -> list[Node]:
    """Return all top-level binder folders that aren't the manuscript or internals."""
    result = []
    for n in nodes:
        if n.type in _SKIP_TYPES:
            continue
        if n.title.lower() in _SKIP_TITLES:
            continue
        result.append(n)
    return result


# ── RTF → text conversion ──────────────────────────────────────────────────────
def rtf_to_text(rtf_path: Path, fmt: str = "plain") -> str:
    """Convert an RTF file to plain text or markdown using pandoc."""
    if not rtf_path.exists():
        return ""
    result = subprocess.run(
        ["pandoc", str(rtf_path), "-f", "rtf", "-t", fmt, "--wrap=none"],
        capture_output=True,
        text=True,
    )
    text = result.stdout
    # Strip Scrivener placeholder tags (<$Scr_Ps::N> etc.)
    text = SCRIVENER_TAG_RE.sub("", text)
    # Strip remaining Scrivener compile tags (<$author>, <$year>, etc.)
    text = re.sub(r"<\$[^>]*>", "", text)
    # Remove 4-space leading indents that pandoc adds from RTF paragraph styles.
    # In a novel manuscript these are never intentional code blocks.
    text = re.sub(r"^    ", "", text, flags=re.MULTILINE)
    # Replace asterisks used as note markers with ※ so they don't interfere
    # with markdown emphasis syntax. In plain-text output all remaining * are
    # literal characters the author typed (not RTF formatting artifacts).
    text = text.replace("*", "※")
    # Collapse more than 2 consecutive blank lines into 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def get_content(uuid: str, fmt: str = "plain") -> str:
    rtf_path = DATA_DIR / uuid / "content.rtf"
    return rtf_to_text(rtf_path, fmt)


# ── Filename sanitisation ──────────────────────────────────────────────────────
def safe_name(title: str) -> str:
    """Turn a title into a safe filesystem name."""
    name = title.strip()
    # Replace characters that are problematic on most filesystems
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return name[:80]  # keep it reasonably short


# ── Markdown export ────────────────────────────────────────────────────────────
def export_markdown(draft: Node, extra_sections: list[Node], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    print(f"\n[Markdown] Writing to {out}")

    _export_md_folder(draft, out / "Manuscript", section="Manuscript")
    for section in extra_sections:
        _export_md_folder(section, out / safe_name(section.title), section=section.title)

    print("[Markdown] Done.")


def _export_md_folder(node: Node, dest: Path, section: str) -> None:
    """Recursively export a binder node to the markdown folder structure."""
    dest.mkdir(parents=True, exist_ok=True)

    for idx, child in enumerate(node.children, start=1):
        if child.type == "Text":
            fname = f"{idx:02d} - {safe_name(child.title)}.md"
            fpath = dest / fname
            content = get_content(child.uuid, fmt="markdown")
            frontmatter = (
                f"---\n"
                f"title: \"{child.title.replace(chr(34), chr(39))}\"\n"
                f"section: \"{section}\"\n"
                f"uuid: \"{child.uuid}\"\n"
                f"---\n\n"
            )
            fpath.write_text(frontmatter + content, encoding="utf-8")
            print(f"  {fpath.relative_to(OUTPUT)}")

        elif child.type in ("Folder", "DraftFolder"):
            sub = dest / f"{idx:02d} - {safe_name(child.title)}"
            _export_md_folder(child, sub, section)

        # Skip Image, Trash, etc.


# ── Manuskript export ──────────────────────────────────────────────────────────
# Manuskript uses MultiMarkdown-style frontmatter for every outline item.
# Items need unique integer IDs; folders use folder.txt, scenes use <name>.md.

def _msk_item_header(title: str, item_id: int, itype: str) -> str:
    """Return the MMD frontmatter block for a Manuskript outline item."""
    return (
        f"title:          {title}\n"
        f"ID:             {item_id}\n"
        f"type:           {itype}\n"
        f"compile:        2\n"
        f"\n"
    )


def _msk_infos_txt(title: str, author: str) -> str:
    return (
        f"Title:          {title}\n"
        f"Subtitle:       \n"
        f"Serie:          \n"
        f"Volume:         \n"
        f"Genre:          \n"
        f"License:        All rights reserved\n"
        f"Author:         {author}\n"
        f"Email:          \n"
    )


MSK_LABELS = "Idea:                #ffff00\nNote:                #00ff00\nChapter:             #0000ff\nScene:               #ff0000\n"
MSK_STATUS = "TODO\nFirst draft\nSecond draft\nFinal\n"
MSK_PLOTS_XML = b'<?xml version="1.0" encoding="UTF-8"?>\n<plots/>\n'
MSK_WORLD_OPML = b'<?xml version="1.0" encoding="UTF-8"?>\n<opml version="1.0"><head/><body/></opml>\n'
MSK_SETTINGS = """{
    "viewMode": "fiction",
    "defaultTextType": "md",
    "saveToZip": false,
    "autoSave": false,
    "autoSaveNoChanges": false,
    "saveOnQuit": false,
    "folderView": "cork",
    "viewSettings": {
        "Cork": {
            "Background": "Nothing",
            "Border": "Nothing",
            "Corner": "Nothing",
            "Icon": "Nothing",
            "Text": "Nothing"
        },
        "Outline": {
            "Background": "Nothing",
            "Icon": "Nothing",
            "Text": "Compile"
        },
        "Tree": {
            "Background": "Nothing",
            "Icon": "Nothing",
            "InfoFolder": "Nothing",
            "InfoText": "Nothing",
            "Text": "Compile",
            "iconSize": 24
        }
    }
}
"""


def export_manuskript(
    draft: Node,
    extra_sections: list[Node],
    project_title: str,
    out: Path,
    author: str,
) -> None:
    base_name = safe_name(project_title)
    msk_file = out / f"{base_name}.msk"   # Entry-point FILE
    proj_dir = out / base_name            # Project data FOLDER

    # Clean up previous run
    if proj_dir.exists():
        shutil.rmtree(proj_dir)
    proj_dir.mkdir(parents=True)

    print(f"\n[Manuskript] Writing to {proj_dir}/")

    # Entry-point .msk file (just contains "1" — format version)
    msk_file.write_text("1", encoding="utf-8")

    # Required metadata files
    (proj_dir / "MANUSKRIPT").write_text("1", encoding="utf-8")
    (proj_dir / "infos.txt").write_text(_msk_infos_txt(project_title, author), encoding="utf-8")
    (proj_dir / "summary.txt").write_text("", encoding="utf-8")
    (proj_dir / "labels.txt").write_text(MSK_LABELS, encoding="utf-8")
    (proj_dir / "status.txt").write_text(MSK_STATUS, encoding="utf-8")
    (proj_dir / "settings.txt").write_text(MSK_SETTINGS, encoding="utf-8")
    (proj_dir / "plots.xml").write_bytes(MSK_PLOTS_XML)
    (proj_dir / "world.opml").write_bytes(MSK_WORLD_OPML)

    # characters/ stub
    (proj_dir / "characters").mkdir(exist_ok=True)

    # outline/
    outline = proj_dir / "outline"
    outline.mkdir()

    # ID counter — each item gets a unique integer
    id_counter = [1]

    def next_id() -> int:
        v = id_counter[0]
        id_counter[0] += 1
        return v

    def write_folder(node_children: list[Node], parent_dir: Path) -> None:
        for idx, child in enumerate(node_children):
            name_slug = safe_name(child.title)

            if child.type == "Text":
                item_id = next_id()
                fname = f"{idx:02d}-{name_slug}.md"
                fpath = parent_dir / fname
                # Use plain text — avoids malformed markdown from RTF decorative
                # fonts being backtick-wrapped by pandoc (e.g. chapter headings).
                content = get_content(child.uuid, fmt="plain")
                header = _msk_item_header(child.title, item_id, "md")
                fpath.write_text(header + content, encoding="utf-8")
                print(f"  {fpath.relative_to(out)}")

            elif child.type in ("Folder", "DraftFolder"):
                item_id = next_id()
                folder_dir = parent_dir / f"{idx:02d}-{name_slug}"
                folder_dir.mkdir(parents=True, exist_ok=True)
                # Manuskript only recognises "folder" and "md"; other type
                # values return None for the icon and crash the cork board.
                header = _msk_item_header(child.title, item_id, "folder")
                (folder_dir / "folder.txt").write_text(header, encoding="utf-8")
                write_folder(child.children, folder_dir)

            # Skip Image, Trash, etc.

    # Manuscript (top-level folder, always index 0)
    ms_id = next_id()
    ms_dir = outline / "00-Manuscript"
    ms_dir.mkdir()
    (ms_dir / "folder.txt").write_text(_msk_item_header("Manuscript", ms_id, "folder"), encoding="utf-8")
    write_folder(draft.children, ms_dir)

    for sec_idx, section in enumerate(extra_sections, start=1):
        sec_id = next_id()
        sec_dir = outline / f"{sec_idx:02d}-{safe_name(section.title)}"
        sec_dir.mkdir(exist_ok=True)
        (sec_dir / "folder.txt").write_text(_msk_item_header(section.title, sec_id, "folder"), encoding="utf-8")
        write_folder(section.children, sec_dir)

    print(f"[Manuskript] Done. Open with: manuskript \"{msk_file}\"")


# ── Main ───────────────────────────────────────────────────────────────────────
def locate_scrivx(scriv: Path) -> Path:
    """Find the .scrivx file inside a .scriv folder."""
    matches = list(scriv.glob("*.scrivx"))
    if not matches:
        sys.exit(f"ERROR: No .scrivx file found inside {scriv}")
    if len(matches) > 1:
        sys.exit(f"ERROR: Multiple .scrivx files found inside {scriv}: {matches}")
    return matches[0]


def main() -> None:
    global DATA_DIR, OUTPUT

    parser = argparse.ArgumentParser(
        description="Convert a Scrivener 3 .scriv project to Markdown and Manuskript formats.",
        epilog="Example: python3 convert.py 'My Novel.scriv' --author 'Jane Smith'",
    )
    parser.add_argument(
        "scriv",
        metavar="PROJECT.scriv",
        help="Path to the Scrivener .scriv project folder",
    )
    parser.add_argument(
        "--title",
        default="",
        help="Project title (default: derived from .scriv folder name)",
    )
    parser.add_argument(
        "--author",
        default="",
        help="Author name for Manuskript infos.txt",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output directory (default: 'output' folder next to the .scriv file)",
    )
    args = parser.parse_args()

    scriv = Path(args.scriv).resolve()
    if not scriv.is_dir():
        sys.exit(f"ERROR: {scriv} is not a directory")
    if scriv.suffix.lower() != ".scriv":
        print(f"WARNING: {scriv.name} does not end in .scriv — continuing anyway")

    scrivx = locate_scrivx(scriv)
    DATA_DIR = scriv / "Files" / "Data"

    # Derive title from folder name unless overridden
    project_title = args.title or scriv.stem

    # Output directory
    OUTPUT = Path(args.output).resolve() if args.output else scriv.parent / "output"

    author = args.author or input("Author name (leave blank to skip): ").strip()

    print(f"Project : {project_title}")
    print(f"Source  : {scrivx}")
    print(f"Output  : {OUTPUT}")
    print(f"Parsing binder …")

    binder = parse_binder(scrivx)

    draft = find_draft(binder)
    if draft is None:
        sys.exit("ERROR: Could not find DraftFolder (Manuscript) in binder.")

    extra_sections = find_extra_sections(binder)

    print(f"  Manuscript: {len(draft.children)} top-level item(s)")
    for sec in extra_sections:
        print(f"  {sec.title}: {len(sec.children)} item(s)")

    OUTPUT.mkdir(exist_ok=True)

    export_markdown(draft, extra_sections, OUTPUT / "markdown")
    export_manuskript(draft, extra_sections, project_title, OUTPUT, author)

    print(f"\nAll done. Output in: {OUTPUT}")


if __name__ == "__main__":
    main()
