"""Turn a Scrivener project (.scriv folder, Scrivener 3 or 2, Mac or
Windows) into an Obsidian book vault. See importer.py for the vault layout.

How the binder maps to the vault:
  - Draft/Manuscript folder: a folder holding other folders is a Part, a
    folder holding only texts is a Chapter (its texts are the scenes), and a
    text on its own is a one-scene chapter. Deeper folders are flattened into
    their chapter. Items unticked for "Include in Compile" are skipped.
  - Top-level "Front Matter" / "Back Matter" folders: their texts become
    front and back matter. When they hold one folder per format (Ebook,
    Paperback, ...), the Ebook one is used. Title pages, blank pages and
    contents pages are skipped; the first image there becomes the cover.
  - Research, Characters, Places, Notes, Template Sheets and Trash are left out.
  - Author comes from the compile settings; the title from the project name,
    unless given.

Usage:
    python3 -m obsidian_book.from_scrivener PROJECT.scriv OUTPUT_DIR [--title T] [--author A]
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from obsidian_book.epub import BookError
from obsidian_book.importer import (COVER_TYPES, Book, Chapter, Item, Part, _norm, split_scenes,
                                    take_header_lines, to_markdown, write_vault)

SCRIVENER_TAG_RE = re.compile(r"\\?<[!/]?\$[^>\n]*?\\?>")  # <$Scr_Ps::0>, <!$Scr_H::1>, <$author>, ...
ID_RE = re.compile(r"^[0-9A-Za-z-]{1,64}$")
SKIP_FRONT_BACK_RE = re.compile(r"^(title page|blank page|half title|table of contents|contents|toc|cover)\b", re.I)
EBOOK_RE = re.compile(r"e-?book|epub|kindle|digital", re.I)
TEXT_TYPES = {"Text"}
FOLDER_TYPES = {"Folder", "DraftFolder"}


@dataclass
class BinderItem:
    id: str
    type: str
    title: str
    included: bool
    children: list = field(default_factory=list)


def find_scrivx(root: Path, max_depth: int = 3) -> Path:
    """The project's .scrivx file: in root or a folder or two below it."""
    level = [root]
    for _ in range(max_depth + 1):
        found = sorted(p for d in level for p in d.glob("*.scrivx") if p.is_file() and not p.is_symlink())
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise BookError("The upload holds more than one Scrivener project. Upload one .scriv at a time.")
        level = [c for d in level for c in sorted(d.iterdir())
                 if c.is_dir() and not c.is_symlink() and c.name != "__MACOSX"]
    raise BookError("Couldn't find a Scrivener project (a .scrivx file) in the upload. "
                    "Upload your whole .scriv folder, or a .zip of it.")


def _parse_items(parent: ET.Element) -> list:
    items = []
    for el in parent.findall("BinderItem"):
        include = (el.findtext("MetaData/IncludeInCompile") or "Yes").strip().lower() != "no"
        children = el.find("Children")
        items.append(BinderItem(el.get("UUID") or el.get("ID") or "", el.get("Type", ""),
                                (el.findtext("Title") or "").strip(), include,
                                _parse_items(children) if children is not None else []))
    return items


class _Project:
    def __init__(self, scrivx: Path):
        self.root = scrivx.parent.resolve()
        try:
            self.xml = ET.parse(scrivx).getroot()
        except ET.ParseError as e:
            raise BookError(f"The Scrivener project file can't be read ({e}).") from None
        binder = self.xml.find("Binder")
        if binder is None:
            raise BookError("The Scrivener project file has no binder.")
        self.binder = _parse_items(binder)

    def _file(self, item_id: str, names: list):
        """A content file for an item, if it exists inside the project."""
        if not ID_RE.match(item_id):
            return None
        candidates = [self.root / "Files" / "Data" / item_id / n for n in names]        # Scrivener 3
        candidates += [self.root / "Files" / "Docs" / (item_id + Path(n).suffix) for n in names]  # Scrivener 2
        for path in candidates:
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(self.root):
                return path
        return None

    def text(self, item: BinderItem) -> str:
        """An item's text as Markdown ("" if it has none)."""
        path = self._file(item.id, ["content.rtf"])
        if path is None:
            return ""
        md = to_markdown(path.read_bytes(), "rtf")
        md = SCRIVENER_TAG_RE.sub("", md)
        md = re.sub(r"(?m)^    ", "", md)   # RTF paragraph indents, never code in a novel
        return md.replace("`", "")         # decorative fonts come through as `code`

    def image(self, item: BinderItem):
        path = self._file(item.id, [f"content{ext}" for ext in sorted(COVER_TYPES)])
        return (path.suffix, path.read_bytes()) if path else None

    def author(self) -> tuple:
        """(author, file-as) from the compile settings, if set."""
        compile_xml = self.root / "Settings" / "compile.xml"
        if compile_xml.is_file() and not compile_xml.is_symlink():
            try:
                root = ET.parse(compile_xml).getroot()
            except ET.ParseError:
                return "", ""
            author = (root.findtext(".//Author") or "").strip()
            surname, forename = (root.findtext(".//Surname") or "").strip(), (root.findtext(".//Forename") or "").strip()
            if author and not author.startswith("<$"):
                return author, f"{surname}, {forename}" if surname and forename else ""
        return "", ""


def _texts(item: BinderItem) -> list:
    """Texts in and under item (itself first if it's a text), in binder order."""
    if not item.included:
        return []
    out = [item] if item.type in TEXT_TYPES else []
    for child in item.children:
        out += _texts(child)
    return out


def _scenes(project: _Project, item: BinderItem) -> list:
    """A chapter's scenes: the folder's own text (if any), then every text
    under it. A text containing scene breaks gives several scenes."""
    scenes = []
    for source in [item] + [t for c in item.children for t in _texts(c)]:
        title = "" if source is item and item.type in FOLDER_TYPES else source.title
        for body in split_scenes(project.text(source)):
            scenes.append(Item(title, body))
    return scenes


def _chapter(project: _Project, item: BinderItem) -> Chapter:
    """A chapter from a folder or text. Short opening lines (a POV name, a
    date), whether in the folder's own text or the first scene, become the
    lines under the chapter title."""
    header, scenes = [], []
    for scene in _scenes(project, item):
        if not scenes and len(header) < 2:
            lines, rest = take_header_lines(scene.body, item.title, 2 - len(header))
            header += lines
            if not rest:
                continue
            scene = Item(scene.title, rest)
        scenes.append(scene)
    return Chapter(item.title or "Chapter", scenes, pov=header[0] if header else "",
                   subtitle_lines=header[1:])


def _front_back(project: _Project, folder: BinderItem):
    """(items, cover) from a Front/Back Matter folder."""
    subfolders = [c for c in folder.children if c.type in FOLDER_TYPES]
    chosen = next((f for f in subfolders if EBOOK_RE.search(f.title)), None)
    if chosen is None:
        direct = [c for c in folder.children if c.type in TEXT_TYPES]
        chosen = folder if direct or not subfolders else subfolders[0]
    items_src = [c for c in chosen.children if c.type in TEXT_TYPES] if chosen is folder else _texts(chosen)
    items = []
    for t in items_src:
        if SKIP_FRONT_BACK_RE.match(t.title):
            continue
        body = "\n\n".join(split_scenes(project.text(t)))
        if body:
            items.append(Item(t.title, body))
    images = [c for c in _walk(chosen) if c.type == "Image"] or [c for c in _walk(folder) if c.type == "Image"]
    cover = next((img for img in (project.image(i) for i in images) if img), None)
    return items, cover


def _walk(item: BinderItem):
    for c in item.children:
        yield c
        yield from _walk(c)


def build_book(scrivx: Path, title: str = "", author: str = "") -> Book:
    project = _Project(scrivx)
    draft = next((i for i in project.binder if i.type == "DraftFolder"), None)
    if draft is None:
        raise BookError("The Scrivener project has no Draft (Manuscript) folder.")

    compile_author, compile_file_as = project.author()
    project_name = re.sub(r"\.scriv$", "", scrivx.parent.name, flags=re.I) or scrivx.stem
    book = Book(title=title.strip() or project_name, author=author.strip() or compile_author,
                author_file_as="" if author.strip() else compile_file_as)

    loose = Part("")
    for item in draft.children:
        if not item.included:
            continue
        is_part = item.type in FOLDER_TYPES and any(c.type in FOLDER_TYPES and c.included for c in item.children)
        if is_part:
            if loose.chapters:
                book.parts.append(loose)
                loose = Part("")
            part = Part(item.title or f"Part {len(book.parts) + 1}")
            own = split_scenes(project.text(item))
            if own and sum(len(s.split()) for s in own) < 150:
                lines = [re.sub(r"^[-*+]\s+|[*_\\]", "", line).strip() for s in own for line in s.split("\n")]
                part.epigraph = [line for line in lines
                                 if line and not (_norm(line) and _norm(line) in _norm(item.title))][:6]
            elif own:
                part.chapters.append(Chapter(item.title, [Item("", s) for s in own]))
            for child in item.children:
                if child.included and (child.type in FOLDER_TYPES or child.type in TEXT_TYPES):
                    chapter = _chapter(project, child)
                    if chapter.scenes:
                        part.chapters.append(chapter)
            if part.chapters:
                book.parts.append(part)
        elif item.type in FOLDER_TYPES or item.type in TEXT_TYPES:
            chapter = _chapter(project, item)
            if chapter.scenes:
                (book.parts[-1] if book.parts and book.parts[-1].title else loose).chapters.append(chapter)
    if loose.chapters:
        book.parts.append(loose)

    for item in project.binder:
        name = item.title.strip().lower()
        if item.type in FOLDER_TYPES and name == "front matter":
            book.front, book.cover = _front_back(project, item)
        elif item.type in FOLDER_TYPES and name == "back matter":
            book.back, _ = _front_back(project, item)
    return book


def import_scrivener(src, dest, title: str = "", author: str = "") -> Path:
    """Write the Scrivener project found in src (a .scriv folder, or a folder
    holding one) as a vault folder at dest."""
    return write_vault(build_book(find_scrivx(Path(src)), title, author), Path(dest))


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Turn a Scrivener project into an Obsidian book vault.")
    parser.add_argument("scriv", help="the .scriv folder")
    parser.add_argument("output", help="folder to create for the vault")
    parser.add_argument("--title", default="")
    parser.add_argument("--author", default="")
    args = parser.parse_args(argv)
    try:
        print(import_scrivener(args.scriv, args.output, args.title, args.author))
    except BookError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
