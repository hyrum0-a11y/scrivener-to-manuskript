"""Shared pieces of the "bring your work in" importers (from_epub.py,
from_scrivener.py): a small book model, Markdown clean-up, and write_vault(),
which lays the book out as a vault the converters in this package accept:

    Book Info.md                    (```book-info block, see epub.py)
    Manuscript Reading Order.md     (# Part / ## Chapter / - [[scene]] bullets)
    Front Matter/<title>.md
    Manuscript/<NN - Part>/<NN - Chapter>/<NN-NN Scene>.md
    Back Matter/<title>.md
    cover.<ext>                     (the source's, else vault-template's cover.jpg as a placeholder)
    .obsidian/, Start Here.md       (copied from vault-template)

Prose is written in the vault's convention: one paragraph per line, no blank
line between paragraphs.
"""

import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from obsidian_book.epub import BookError

VAULT_TEMPLATE = Path(__file__).resolve().parent.parent / "vault-template"
COVER_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}  # not .svg: it can carry script


@dataclass
class Item:
    """A front/back matter page or a scene: a title and Markdown prose."""
    title: str
    body: str


@dataclass
class Chapter:
    title: str
    scenes: list = field(default_factory=list)   # [Item]
    pov: str = ""                                # line under the chapter title (e.g. a POV name)
    subtitle_lines: list = field(default_factory=list)  # more lines under it (e.g. a date)


@dataclass
class Part:
    title: str                                   # "" = chapters with no part page
    chapters: list = field(default_factory=list)  # [Chapter]
    epigraph: list = field(default_factory=list)  # [str], up to a few short lines


@dataclass
class Book:
    title: str
    author: str
    author_file_as: str = ""
    publisher: str = ""
    isbn: str = ""
    front: list = field(default_factory=list)    # [Item]
    parts: list = field(default_factory=list)    # [Part]
    back: list = field(default_factory=list)     # [Item]
    cover: tuple | None = None                   # (suffix, bytes)

    def chapters(self) -> list:
        return [c for p in self.parts for c in p.chapters]


# --- text ---------------------------------------------------------------------

def to_markdown(data: bytes, fmt: str) -> str:
    """Convert HTML or RTF to CommonMark with pandoc. No raw HTML survives,
    and pandoc's Markdown writer never fetches anything."""
    result = subprocess.run(["pandoc", "-f", fmt, "-t", "commonmark-raw_html", "--wrap=none"],
                            input=data, capture_output=True)
    if result.returncode != 0:
        raise BookError(f"pandoc couldn't read part of the book: "
                        f"{result.stderr.decode(errors='replace')[:300]}")
    return result.stdout.decode("utf-8", errors="replace")


IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]*)\)")
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
# Characters a scene-break line can be made of. Not "." or quotes, so a
# paragraph of just "..." or "…" in dialogue stays prose.
SEPARATOR_CHARS = set("*#~•·※⁂◆◇❖✦✧✤❦❧☙§=+°_-–—⸻⁕✱❋✻✽❊◊♦★☆∗⋆")
SEPARATOR_IMAGE_RE =re.compile(r"orn|sep|break|divid|fleur|scene|dinkus|flourish", re.I)


def paragraphs(md: str) -> list:
    """Split CommonMark into paragraphs (blank-line separated blocks).
    Setext headings ("Title" over "===") become "# Title"; blocks that are
    only line breaks (spacing in the source) are dropped."""
    out = []
    for block in re.split(r"\n\s*\n", md.replace("\r\n", "\n")):
        block = block.strip()
        setext = re.fullmatch(r"(.*?)\n(=+|-+)", block, re.S)
        if setext and setext.group(1).strip():
            text = re.sub(r"\\\n|\n", " ", setext.group(1)).strip(" \\")
            block = ("#" if setext.group(2)[0] == "=" else "##") + " " + text if text else ""
        block = re.sub(r"\\\n", "\n", block)   # hard line break -> its own line
        block = re.sub(r"(?m)^\\$", "", block).strip()
        if block:
            out.append(block)
    return out


def is_separator(par: str) -> bool:
    """A scene break: a rule, or a short line of only symbols ("* * *", "#",
    "⁂", "—※—"), or a lone ornament image."""
    if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", par.replace(" ", "")):
        return True
    img = IMAGE_RE.fullmatch(par)
    if img:
        return bool(SEPARATOR_IMAGE_RE.search(img.group(1)))
    core = re.sub(r"[\\\s]", "", par)
    return 0 < len(core) <= 12 and all(c in SEPARATOR_CHARS for c in core)


def clean_paragraph(par: str) -> str:
    """Make one paragraph safe for the vault: no images, links reduced to
    their text, headings turned into bold lines, and Obsidian syntax the
    converters treat specially (%% comments, ~~cuts~~, [[links]]) escaped."""
    par = IMAGE_RE.sub("", par)
    par = LINK_RE.sub(lambda m: m.group(1), par)
    par = re.sub(r"^#{1,6}\s+(.*?)\s*#*$", lambda m: f"**{m.group(1)}**" if m.group(1) else "",
                 par, flags=re.M)
    par = par.replace("%%", r"\%\%").replace("~~", r"\~\~").replace("[[", r"\[\[")
    return par.strip()


def join_paragraphs(pars: list) -> str:
    """The vault convention: one paragraph per line."""
    lines = []
    for p in pars:
        p = clean_paragraph(p)
        if p:
            lines.extend(line.strip() for line in p.split("\n") if line.strip())
    return "\n".join(lines)


def split_scenes(md: str) -> list:
    """Split a chapter's Markdown at scene breaks into vault-format bodies,
    dropping empty scenes."""
    scenes, current = [], []
    for par in paragraphs(md):
        if is_separator(par):
            scenes.append(current)
            current = []
        else:
            current.append(par)
    scenes.append(current)
    return [body for body in (join_paragraphs(s) for s in scenes) if body]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def take_header_lines(body: str, title: str, limit: int = 2) -> tuple:
    """Split up to `limit` short, unpunctuated opening lines (a POV name, a
    date) off a vault-format body. Lines repeating the title and lone
    characters (drop caps, symbol-font glyphs) are dropped. Returns
    (header lines, rest of body)."""
    lines = body.split("\n")
    header, i = [], 0
    while i < len(lines) and i < 6 and len(header) < limit:
        plain = re.sub(r"^[-*+]\s+|[*_#\\]", "", lines[i]).strip()
        if not plain or len(plain) <= 1 or (_norm(plain) and _norm(plain) == _norm(title)):
            i += 1
        elif len(plain) <= 40 and not re.search(r"[.!?,;:\"”’'…)\]]$", plain):
            header.append(plain)
            i += 1
        else:
            break
    if i >= len(lines) and header and len(lines) > len(header) + 2:
        return [], body  # nothing but short lines: it's prose (a poem, a list), not a header
    return header, "\n".join(lines[i:]).strip()


def word_count(md: str) -> int:
    return len(re.findall(r"\w+", IMAGE_RE.sub("", md)))


# --- writing the vault -----------------------------------------------------------

def safe_name(title: str, limit: int = 80) -> str:
    """A title as a file name that also works inside an Obsidian [[link]]."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f#^\[\]]', "-", title.strip())
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"-{2,}", "-", name).strip(" .-_")
    return name[:limit].strip(" .-_") or "Untitled"


def _quote(value: str) -> str:
    return value.replace('"', "'").replace("\n", " ").strip()


def _frontmatter(title: str, section: str) -> str:
    return f'---\ntitle: "{_quote(title)}"\nsection: "{section}"\nuuid: "{uuid.uuid4()}"\n---\n'


def file_as(author: str) -> str:
    """'Jane Q. Smith' -> 'Smith, Jane Q.'"""
    words = author.split()
    return f"{words[-1]}, {' '.join(words[:-1])}" if len(words) > 1 else author


def write_vault(book: Book, dest: Path) -> Path:
    """Write book as a vault folder at dest (which must not exist yet)."""
    if not book.chapters():
        raise BookError("No chapters were found, so there's nothing to put in the vault.")
    dest.mkdir(parents=True)
    used: set = set()

    def unique(stem: str) -> str:
        base, n = stem, 2
        while stem.lower() in used:
            stem = f"{base} ({n})"
            n += 1
        used.add(stem.lower())
        return stem

    def write_note(folder: Path, stem: str, title: str, section: str, body: str) -> str:
        stem = unique(stem)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{stem}.md").write_text(_frontmatter(title, section) + body.strip() + "\n",
                                           encoding="utf-8")
        return stem

    order = [f"# {_quote(book.title)} — Reading Order", ""]

    for item in book.front:
        stem = write_note(dest / "Front Matter", safe_name(item.title), item.title, "Front Matter", item.body)
        order.append(f"- [[{stem}]]  _Front Matter_")

    chapter_no = 0
    for part_no, part in enumerate(book.parts, start=1):
        part_dir = dest / "Manuscript"
        if part.title:
            part_dir = part_dir / f"{part_no:02d} - {safe_name(part.title, 60)}"
            order += ["", f"# {_quote(part.title)}"]
            order += [f"center: *{_quote(line)}*" for line in part.epigraph if line.strip()]
        for chapter in part.chapters:
            chapter_no += 1
            chapter_dir = part_dir / f"{chapter_no:02d} - {safe_name(chapter.title, 60)}"
            order += ["", f"## {_quote(chapter.title)}"]
            if chapter.pov:
                order.append(f"center: **{_quote(chapter.pov)}**")
            order += [f"left: {_quote(line)}" for line in chapter.subtitle_lines if line.strip()]
            for scene_no, scene in enumerate(chapter.scenes, start=1):
                stem = f"{chapter_no:02d}-{scene_no:02d} {safe_name(scene.title or chapter.title, 60)}"
                stem = write_note(chapter_dir, stem, scene.title or chapter.title, "Manuscript", scene.body)
                order.append(f"- [[{stem}]]")

    if book.back:
        order.append("")
    for item in book.back:
        stem = write_note(dest / "Back Matter", safe_name(item.title), item.title, "Back Matter", item.body)
        order.append(f"- [[{stem}]]  _Back Matter_")

    (dest / "Manuscript Reading Order.md").write_text("\n".join(order) + "\n", encoding="utf-8")

    placeholder = VAULT_TEMPLATE / "cover.jpg"
    if book.cover and book.cover[0].lower() in COVER_TYPES:
        cover_name = "cover" + book.cover[0].lower()
        (dest / cover_name).write_bytes(book.cover[1])
    elif placeholder.is_file():  # a cover is required; this one shows where it goes
        cover_name = "cover.jpg"
        shutil.copyfile(placeholder, dest / cover_name)
    else:
        raise BookError("The book has no cover image, and every vault needs one.")

    fields = {
        "title": _quote(book.title) or "Untitled",
        "author": _quote(book.author) or "Unknown Author",
        "author_file_as": _quote(book.author_file_as or file_as(book.author)) or "Author, Unknown",
        "publisher": _quote(book.publisher) or "Self-Published",
        "isbn": _quote(book.isbn), "cover": cover_name,
    }
    for key in ("output_dir", "series", "subtitle", "title_page_lines", "series_position",
                "series_length", "trim_size", "running_header", "pov_signs_dir", "pov_signs"):
        fields[key] = ""
    info = "\n".join(f'{k}: "{v}"' for k, v in fields.items())
    (dest / "Book Info.md").write_text(
        "# Book Info\n\nThis book's details, used when converting it to EPUB, PDF or ODT. "
        "Check the title, author and publisher."
        + ("" if book.cover else " The cover is a placeholder: replace cover.jpg with your own cover "
           "(same name), or put yours in this folder and change cover: to its file name.")
        + "\n\n"
        f"```book-info\n{info}\n```\n", encoding="utf-8")

    if (VAULT_TEMPLATE / ".obsidian").is_dir():
        shutil.copytree(VAULT_TEMPLATE / ".obsidian", dest / ".obsidian")
    if (VAULT_TEMPLATE / "Start Here.md").is_file():
        shutil.copyfile(VAULT_TEMPLATE / "Start Here.md", dest / "Start Here.md")
    return dest
