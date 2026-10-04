"""Turn an EPUB (from Scrivener, Vellum, Atticus, Calibre, KDP, ...) into an
Obsidian book vault. See importer.py for the vault layout.

How the book's structure is recovered:
  - Title, author, publisher, ISBN and cover come from the EPUB's package
    file (content.opf).
  - The table of contents (EPUB 3 nav.xhtml, else EPUB 2 toc.ncx) gives the
    chapters. A contents entry with entries under it is a Part when it's
    named like one ("Part 2", "Book One", "Act III") or has little text of
    its own; otherwise it's a chapter and the entries under it are scenes.
  - Leading entries named like front matter (Copyright, Dedication, ...) go
    to Front Matter, trailing ones like back matter (Acknowledgments, About
    the Author, ...) to Back Matter. Cover, title page and contents entries
    are dropped: the converters make their own.
  - Scenes are split at scene breaks: a horizontal rule, a line of symbols
    ("* * *", "#", "⁂", "—※—") or an ornament image.
  - With no usable table of contents, each file in the reading order becomes
    a chapter.

The EPUB is read straight from the zip, never unpacked to disk, with a cap
on how much is decompressed. Images other than the cover aren't imported.

Usage:
    python3 -m obsidian_book.from_epub BOOK.epub OUTPUT_DIR
"""

import posixpath
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

from obsidian_book.epub import BookError
from obsidian_book.importer import (LINK_RE, Book, Chapter, Item, Part, is_separator, paragraphs, split_scenes,
                                    to_markdown, word_count, write_vault)

NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/",
      "ncx": "http://www.daisy.org/z3986/2005/ncx/",
      "c": "urn:oasis:names:tc:opendocument:xmlns:container"}
DOC_TYPES = {"application/xhtml+xml", "text/html", "application/x-dtbook+xml"}

DROP_RE = re.compile(r"^(cover|title page|title|half title|contents|table of contents|toc|"
                     r"copyright page|start|landmarks)$", re.I)
FRONT_RE = re.compile(r"^(copyright|dedication|epigraph|also by|praise|books by|other books|"
                      r"information|front matter|a note|note to|map|maps|author'?s note|"
                      r"content warnings?|trigger warnings?|summary|previously|recap|foreword|"
                      r"preface|introduction|cast of characters|dramatis personae)\b", re.I)
BACK_RE = re.compile(r"^(acknowledg|about the authors?|also by|books by|other books|more (books )?(by|from)|"
                     r"author'?s note|afterword|glossary|appendix|bibliography|notes$|endnotes|"
                     r"discussion|reading group|book club|newsletter|excerpt|sneak peek|preview|"
                     r"back matter|copyright|thank you)", re.I)
# Phrases that mark front/back matter wherever they appear in an entry's title
# ("Section About the Author").
BACK_ANY_RE = re.compile(r"about the authors?|acknowledg|also by|books by|glossary|afterword|"
                         r"bibliography|newsletter|reading group|discussion questions", re.I)
PART_RE = re.compile(r"^(part|book|volume|act|section)\b", re.I)
NUMBER_WORD = (r"(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
               r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
               r"eighty|ninety|hundred)")
# A whole line that's just a chapter label: "Chapter 12", "XII", "12.", "Twenty-One".
CHAPTER_LINE_RE = re.compile(rf"^(chapter|ch\.?)\s+([0-9ivxlcdm]+|{NUMBER_WORD}([- ]{NUMBER_WORD})*)\.?$"
                             rf"|^[0-9ivxlcdm]+\.?$|^{NUMBER_WORD}([- ]{NUMBER_WORD})*$", re.I)
# A heading that starts a chapter: the above, or "Chapter 3: The Storm", "Prologue", "12. Home".
CHAPTER_HEAD_RE = re.compile(rf"^(chapter|ch\.)\s|^(prologue|epilogue|interlude)\b|^[0-9]+\.?(\s|$)"
                             rf"|{CHAPTER_LINE_RE.pattern}", re.I)
SPLIT_MARK = "AUTHORTOOLSSPLIT"
SPLIT_RE = re.compile(SPLIT_MARK + r"(\d+)X")
FILE_MARK = "AUTHORTOOLSFILEX"  # between the EPUB's files: a heading after it starts a new section
PART_TEXT_WORDS = 150       # a contents entry with less text than this can be a Part page
EPIGRAPH_MAX_LINES = 6


@dataclass
class Node:
    title: str
    path: str                  # file inside the zip
    frag: str                  # "" = start of file
    children: list = field(default_factory=list)
    text: str = ""             # its own Markdown, up to the next contents entry


class _Zip:
    """Reads members of the EPUB with a cap on total decompressed bytes."""

    def __init__(self, path: Path, max_bytes: int):
        try:
            self.zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile:
            raise BookError("That file isn't a valid EPUB (it isn't a zip archive).") from None
        self.names = set(self.zf.namelist())
        self.left = max_bytes

    def read(self, name: str) -> bytes:
        if name not in self.names:
            raise BookError(f"The EPUB is missing a file it refers to ({name!r}).")
        out = bytearray()
        try:
            with self.zf.open(name) as f:
                while chunk := f.read(64 * 1024):
                    self.left -= len(chunk)
                    if self.left < 0:
                        raise BookError("The EPUB is too large once unpacked.")
                    out += chunk
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as e:
            raise BookError(f"Couldn't read {name!r} from the EPUB ({e}).") from None
        return bytes(out)

    def xml(self, name: str) -> ET.Element:
        try:
            return ET.fromstring(self.read(name))
        except ET.ParseError as e:
            raise BookError(f"The EPUB's {name!r} isn't valid XML ({e}).") from None


def _resolve(base_file: str, href: str) -> tuple:
    """(zip path, fragment) for an href relative to base_file."""
    href, _, frag = href.partition("#")
    if not href:
        return base_file, unquote(frag)
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base_file), unquote(href)))
    return path.lstrip("/"), unquote(frag)


class _NavParser(HTMLParser):
    """Collects the nested <ol>/<li>/<a> tree inside <nav epub:type="toc">."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_toc = 0
        self.nav_depth = 0
        self.root: list = []
        self.stack: list = []          # list of children lists for open <ol>s
        self.current_li: list = []     # open <li> entries: [title, href, children]
        self.in_a = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "nav":
            self.nav_depth += 1
            kinds = (a.get("epub:type", "") + " " + a.get("role", "")).lower()
            if not self.in_toc and ("toc" in kinds.split() or "doc-toc" in kinds.split()):
                self.in_toc = self.nav_depth
        if not self.in_toc:
            return
        if tag == "ol":
            self.stack.append(self.current_li[-1][2] if self.current_li else self.root)
        elif tag == "li" and self.stack:
            entry = ["", "", []]
            self.stack[-1].append(entry)
            self.current_li.append(entry)
        elif tag in ("a", "span") and self.current_li:
            self.in_a = True
            if tag == "a" and a.get("href") and not self.current_li[-1][1]:
                self.current_li[-1][1] = a["href"]

    def handle_endtag(self, tag):
        if tag == "nav":
            if self.in_toc == self.nav_depth:
                self.in_toc = 0
            self.nav_depth -= 1
        if not self.in_toc:
            return
        if tag == "ol" and self.stack:
            self.stack.pop()
        elif tag == "li" and self.current_li:
            self.current_li.pop()
        elif tag in ("a", "span"):
            self.in_a = False

    def handle_data(self, data):
        if self.in_toc and self.in_a and self.current_li:
            self.current_li[-1][0] += data


def _nav_nodes(entries: list, nav_file: str) -> list:
    nodes = []
    for title, href, children in entries:
        path, frag = _resolve(nav_file, href) if href else ("", "")
        node = Node(" ".join(title.split()), path, frag, _nav_nodes(children, nav_file))
        if node.path or node.children:
            nodes.append(node)
    return nodes


def _ncx_nodes(parent: ET.Element, ncx_file: str) -> list:
    nodes = []
    for point in parent.findall("ncx:navPoint", NS):
        title = " ".join("".join(point.find("ncx:navLabel", NS).itertext()).split()) \
            if point.find("ncx:navLabel", NS) is not None else ""
        content = point.find("ncx:content", NS)
        path, frag = _resolve(ncx_file, content.get("src", "")) if content is not None else ("", "")
        nodes.append(Node(title, path, frag, _ncx_nodes(point, ncx_file)))
    return nodes


def _walk(nodes: list):
    for n in nodes:
        yield n
        yield from _walk(n.children)


def _metadata(opf: ET.Element) -> dict:
    md = opf.find("opf:metadata", NS)
    if md is None:
        return {}
    text = lambda el: " ".join("".join(el.itertext()).split()) if el is not None else ""
    creator = md.find("dc:creator", NS)
    file_as = creator.get(f"{{{NS['opf']}}}file-as", "") if creator is not None else ""
    if creator is not None and not file_as and creator.get("id"):
        for meta in md.findall("opf:meta", NS):
            if meta.get("refines") == "#" + creator.get("id") and meta.get("property") == "file-as":
                file_as = text(meta)
    isbn = ""
    for ident in md.findall("dc:identifier", NS):
        digits = re.sub(r"[^0-9Xx]", "", text(ident).lower().replace("urn:isbn:", ""))
        if len(digits) in (10, 13) and ("isbn" in text(ident).lower() or digits.startswith("97")
                                        or "isbn" in (ident.get(f"{{{NS['opf']}}}scheme", "") or "").lower()):
            isbn = digits.upper()
            break
    return {"title": text(md.find("dc:title", NS)), "author": text(creator), "file_as": file_as,
            "publisher": text(md.find("dc:publisher", NS)), "isbn": isbn}


def _cover(epub: _Zip, opf: ET.Element, opf_path: str, manifest: dict):
    cover_id = next((i for i, m in manifest.items() if "cover-image" in m["props"]), None)
    if cover_id is None:
        meta = opf.find("opf:metadata/opf:meta[@name='cover']", NS)
        cover_id = meta.get("content") if meta is not None else None
    if cover_id in manifest and manifest[cover_id]["type"].startswith("image/"):
        path = manifest[cover_id]["path"]
        return posixpath.splitext(path)[1], epub.read(path)
    return None


def _segment_texts(epub: _Zip, spine: list, nodes: list) -> str:
    """Convert each spine document to Markdown, with a marker where each
    contents entry starts. Returns the joined text and sets nothing; the
    caller splits on the markers. Text before the first marker is dropped."""
    by_file: dict = {}
    for k, node in enumerate(nodes):
        by_file.setdefault(node.path, []).append((k, node.frag))
    pieces = []
    for path in spine:
        html = epub.read(path).decode("utf-8", errors="replace")
        prefix = ""
        for k, frag in by_file.get(path, []):
            mark = f"<p>{SPLIT_MARK}{k}X</p>"
            m = re.search(r"""\bid\s*=\s*["']""" + re.escape(frag) + r"""["']""", html) if frag else None
            if m:
                tag_start = html.rfind("<", 0, m.start())
                html = html[:tag_start] + mark + html[tag_start:]
            else:
                prefix += f"{SPLIT_MARK}{k}X\n\n"
        pieces.append(prefix + to_markdown(html.encode("utf-8"), "html"))
    return f"\n\n{FILE_MARK}\n\n".join(pieces)


def _assign_text(joined: str, nodes: list) -> None:
    parts = SPLIT_RE.split(joined)
    # parts = [before, k0, text0, k1, text1, ...]
    for i in range(1, len(parts) - 1, 2):
        node = nodes[int(parts[i])]
        node.text += parts[i + 1]


def _pars(md: str) -> list:
    return [p for p in paragraphs(md) if p != FILE_MARK]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _plain(par: str) -> str:
    return re.sub(r"[*_#\\]", "", LINK_RE.sub(lambda m: m.group(1), par)).strip()


def _is_heading(par: str) -> bool:
    return bool(re.match(r"#{1,6}(\s|$)", par))


def _level(par: str) -> int:
    return len(par) - len(par.lstrip("#"))


def _is_title_line(par: str, title: str) -> bool:
    plain = _plain(par)
    return bool(_norm(plain)) and (_norm(plain) == _norm(title) or bool(CHAPTER_LINE_RE.match(plain))
                                   or (len(plain) < 60 and f" {_norm(plain)} " in f" {_norm(title)} "))


def _looks_like_header_line(par: str) -> bool:
    """A short line with no closing punctuation, like a POV name or a date."""
    plain = _plain(par)
    return 0 < len(plain) <= 40 and not re.search(r"[.!?,;:\"”’'…)\]]$", plain) and "\n" not in plain


def _chapter_from_blocks(title: str, blocks: list) -> Chapter:
    """Build a chapter from its text blocks (its own text, then any contents
    entries under it). The chapter's heading is dropped from the top of the
    first block; up to two lines right under it (extra headings, or short
    unpunctuated lines like "Gerald" / "January 2009") become the lines
    shown under the chapter title."""
    header, scenes = [], []
    for n, block in enumerate(blocks):
        pars = _pars(block)
        i = 0
        while i < len(pars) and i < 6 and (_is_heading(pars[i]) or _is_title_line(pars[i], title)):
            plain = _plain(pars[i])
            if n == 0 and plain and not _is_title_line(pars[i], title) and len(header) < 2:
                header.append(plain)
            i += 1
        if n == 0 and not header:
            while (i < len(pars) - 3 and len(header) < 2 and _looks_like_header_line(pars[i])
                   and not is_separator(pars[i])):
                header.append(_plain(pars[i]))
                i += 1
        scenes.extend(split_scenes("\n\n".join(pars[i:])))
    return Chapter(title or "Chapter", [Item("", body) for body in scenes],
                   pov=header[0] if header else "", subtitle_lines=header[1:])


def _heading_groups(md: str) -> list:
    """[(level, [(level, heading text)], [body paragraphs])]: runs of
    headings with the text under them. Text before any heading has level 0."""
    groups, new_file = [], False
    for par in paragraphs(md):
        if par == FILE_MARK:
            new_file = True
            continue
        if _is_heading(par):
            text = _plain(par)
            if not text:
                continue
            if (groups and groups[-1][0] and not groups[-1][2] and not new_file
                    and not CHAPTER_HEAD_RE.match(text) and not PART_RE.match(text)):
                groups[-1][1].append((_level(par), text))
            else:
                groups.append([_level(par), [(_level(par), text)], []])
        else:
            if not groups:
                groups.append([0, [], []])
            groups[-1][2].append(par)
        new_file = False
    return groups


def _chapters_from_groups(groups: list) -> list:
    """Chapters at the heading level most groups use (so one stray bigger
    heading, like a closing "Continue to Book III" link, doesn't win);
    deeper headings stay in the text (as bold lines)."""
    levels = Counter(g[0] for g in groups if g[0])
    level = max(sorted(levels), key=lambda lvl: levels[lvl], default=0)
    chapters, current = [], None
    for lvl, heads, body in groups:
        if lvl == level and heads:
            current = [heads, list(body)]
            chapters.append(current)
        elif current is not None:
            current[1] += ["#" * hl + " " + h for hl, h in heads] + body
    return [c for c in (_chapter_from_blocks(heads[0][1], ["\n\n".join(["#" * hl + " " + h for hl, h in heads]
                                                                        + body)])
                        for heads, body in chapters) if c.scenes]


def _split_leaf(node: Node):
    """For a contents entry with nothing under it, look for chapter headings
    in its text. Returns None if there's nothing to split, else a list of
    (heading lines, text before the first chapter, [Chapter]) sections. A
    part-like entry can hold several parts when the contents skip one
    ("PART V" only appears as a heading), so "Part" headings start a new
    section."""
    groups = _heading_groups(node.text)
    own_heads, own_body = [], []
    if groups and (groups[0][0] == 0 or (groups[0][1] and _is_title_line("# " + groups[0][1][0][1], node.title)
                                         and not CHAPTER_HEAD_RE.match(groups[0][1][0][1]))):
        own_heads, own_body = groups[0][1], groups[0][2]
        groups = groups[1:]
    if PART_RE.match(node.title):
        sections, current = [], [own_heads, own_body, []]
        for g in groups:
            if g[1] and PART_RE.match(g[1][0][1]) and not CHAPTER_HEAD_RE.match(g[1][0][1]):
                sections.append(current)
                current = [g[1], g[2], []]
            else:
                current[2].append(g)
        sections.append(current)
        return [(heads, body, _chapters_from_groups(gs) if gs else []) for heads, body, gs in sections]
    if sum(bool(CHAPTER_HEAD_RE.match(g[1][0][1])) for g in groups if g[1]) >= 2:
        return [(own_heads, own_body, _chapters_from_groups(groups))]
    return None


def _all_text(node: Node) -> list:
    """A node's own text followed by its descendants', each as a separate block."""
    return [node.text] + [t for c in node.children for t in _all_text(c)]


def _chapter(node: Node) -> Chapter:
    return _chapter_from_blocks(node.title, _all_text(node))


def _epigraph(pars: list) -> list:
    lines = [re.sub(r"[*_\\]", "", p).strip() for p in pars if not _is_heading(p)]
    lines = [line for line in lines if line]
    if len(lines) <= EPIGRAPH_MAX_LINES and all(len(line) <= 150 for line in lines):
        return lines
    return []


def _own_paragraphs(node: Node) -> list:
    pars = _pars(node.text)
    i = 0
    while i < len(pars) and i < 6 and (_is_heading(pars[i]) or _is_title_line(pars[i], node.title)):
        i += 1
    return pars[i:]


def _item(node: Node) -> Item:
    blocks = [node.text] + [t for c in node.children for t in _all_text(c)]
    pars = []
    for n, block in enumerate(blocks):
        bp = _pars(block)
        i = 0
        while i < len(bp) and i < 6 and (_is_heading(bp[i]) or _is_title_line(bp[i], node.title)):
            i += 1
        pars += bp[i:]
    return Item(node.title, "\n\n".join(split_scenes("\n\n".join(pars))))


def _is_part(node: Node) -> bool:
    return bool(node.children) and (bool(PART_RE.match(node.title))
                                    or word_count("\n\n".join(_own_paragraphs(node))) < PART_TEXT_WORDS)


def _is_front(title: str) -> bool:
    return bool(DROP_RE.match(title) or FRONT_RE.match(title) or BACK_ANY_RE.search(title))


def _is_back(title: str) -> bool:
    return bool(DROP_RE.match(title) or BACK_RE.match(title) or BACK_ANY_RE.search(title))


def build_book(epub_path: Path, max_bytes: int = 200 * 2**20) -> Book:
    epub = _Zip(epub_path, max_bytes)
    container = epub.xml("META-INF/container.xml")
    rootfile = container.find(".//c:rootfile", NS)
    if rootfile is None or not rootfile.get("full-path"):
        raise BookError("The EPUB doesn't say where its package file is (META-INF/container.xml).")
    opf_path = rootfile.get("full-path").lstrip("/")
    opf = epub.xml(opf_path)

    manifest = {}
    for item in opf.findall("opf:manifest/opf:item", NS):
        path, _ = _resolve(opf_path, item.get("href", ""))
        manifest[item.get("id")] = {"path": path, "type": item.get("media-type", ""),
                                    "props": (item.get("properties") or "").split()}
    spine_el = opf.find("opf:spine", NS)
    spine = [manifest[ref.get("idref")]["path"] for ref in spine_el.findall("opf:itemref", NS)
             if ref.get("idref") in manifest and ref.get("linear", "yes") != "no"
             and manifest[ref.get("idref")]["type"] in DOC_TYPES] if spine_el is not None else []
    spine = [p for p in dict.fromkeys(spine) if p in epub.names]
    if not spine:
        raise BookError("The EPUB has no readable chapters in its reading order.")

    toc: list = []
    nav_id = next((i for i, m in manifest.items() if "nav" in m["props"]), None)
    if nav_id:
        parser = _NavParser()
        parser.feed(epub.read(manifest[nav_id]["path"]).decode("utf-8", errors="replace"))
        toc = _nav_nodes(parser.root, manifest[nav_id]["path"])
    if not toc and spine_el is not None and spine_el.get("toc") in manifest:
        ncx_path = manifest[spine_el.get("toc")]["path"]
        nav_map = epub.xml(ncx_path).find("ncx:navMap", NS)
        toc = _ncx_nodes(nav_map, ncx_path) if nav_map is not None else []

    spine_set = set(spine)
    flat = [n for n in _walk(toc) if n.path in spine_set]
    if len(flat) < 2:  # no usable contents: one chapter per file
        toc = [Node("", p, "") for p in spine]
        flat = toc
    _assign_text(_segment_texts(epub, spine, flat), flat)
    for node in _walk(toc):
        if not node.title:
            first = next((p for p in _pars(node.text) if p.startswith("#")), "")
            node.title = re.sub(r"[#*_\\]", "", first).strip()

    # Front matter: leading entries; back matter: trailing entries.
    top = [n for n in toc if n.path in spine_set or n.children]
    start, end = 0, len(top)
    while end > start and not top[end - 1].children and _is_back(top[end - 1].title):
        end -= 1
    while start < end and not top[start].children and _is_front(top[start].title):
        start += 1

    meta = _metadata(opf)
    book = Book(title=meta.get("title") or epub_path.stem, author=meta.get("author", ""),
                author_file_as=meta.get("file_as", ""), publisher=meta.get("publisher", ""),
                isbn=meta.get("isbn", ""), cover=_cover(epub, opf, opf_path, manifest))
    book.front = [_item(n) for n in top[:start] if not DROP_RE.match(n.title) and n.text.strip()]
    book.back = [_item(n) for n in top[end:] if not DROP_RE.match(n.title) and n.text.strip()]

    loose = Part("")

    def add_chapter(chapter: Chapter) -> None:
        if book.parts and book.parts[-1].title:   # a chapter after a part belongs to it
            book.parts[-1].chapters.append(chapter)
        else:
            loose.chapters.append(chapter)

    def start_part(title: str, epigraph: list) -> Part:
        nonlocal loose
        if loose.chapters:
            book.parts.append(loose)
            loose = Part("")
        part = Part(title or f"Part {len(book.parts) + 1}", epigraph=epigraph)
        book.parts.append(part)
        return part

    for node in top[start:end]:
        if _is_part(node):
            part = start_part(node.title, _epigraph(_own_paragraphs(node)))
            part.chapters = [c for c in (_chapter(ch) for ch in node.children) if c.scenes]
            continue
        split = None if node.children else _split_leaf(node)
        if split and PART_RE.match(node.title):
            made = False
            for n, (own_heads, own_body, chapters) in enumerate(split):
                # Headings at the part title's level extend it ("PART I" +
                # "THIRTEEN REFUGEES"); deeper ones are an epigraph.
                top_level = own_heads[0][0] if own_heads else 0
                title = node.title if n == 0 else own_heads[0][1]
                extra = [h for hl, h in own_heads if hl == top_level and not _is_title_line("# " + h, title)]
                epigraph = [h for hl, h in own_heads if hl > top_level] + own_body
                if chapters or word_count("\n\n".join(own_body)) < PART_TEXT_WORDS:
                    part = start_part(" ".join([title, *extra]), _epigraph(epigraph))
                    part.chapters = chapters
                    made = True
            if made:
                continue
        elif split:
            own_heads, own_body, chapters = split[0]
            if word_count("\n\n".join(own_body)) >= PART_TEXT_WORDS:
                add_chapter(_chapter_from_blocks(node.title, ["\n\n".join(own_body)]))
            for chapter in chapters:
                add_chapter(chapter)
            continue
        chapter = _chapter(node)
        if chapter.scenes:
            add_chapter(chapter)
    if loose.chapters:  # only when no titled part came before them
        book.parts.append(loose)
    book.parts = [p for p in book.parts if p.chapters]
    return book


def import_epub(epub_path, dest, max_bytes: int = 200 * 2**20) -> Path:
    """Write the EPUB at epub_path as a vault folder at dest."""
    return write_vault(build_book(Path(epub_path), max_bytes), Path(dest))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__.split("Usage:")[1])
    try:
        print(import_epub(sys.argv[1], sys.argv[2]))
    except BookError as e:
        sys.exit(str(e))
