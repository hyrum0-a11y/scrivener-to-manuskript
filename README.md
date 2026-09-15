# scrivener-to-manuskript

Tools for moving a novel manuscript between **Scrivener 3**, **Manuskript**,
**Obsidian**, and distributable **epub**/**PDF**/**ODT** output — built for
migrating off Windows/Scrivener onto Linux while keeping a writable,
plain-text source of truth.

Two independent pipelines live here:

1. **`convert.py`** — one-shot Scrivener → Markdown/Manuskript migration
2. **The Obsidian pipeline** — an ongoing vault-based workflow: write in
   Obsidian, compile to epub/PDF whenever you want a distributable build.
   Also includes the reverse direction (`epub_to_obsidian.py`) for pulling
   an already-published book *into* the same vault convention.

## Requirements

- Python 3.10+
- [pandoc](https://pandoc.org/installing.html) — RTF/epub → Markdown, and Markdown → epub
- For `obsidian_to_pdf.py` only: `weasyprint` and `pymupdf` (`pip install weasyprint pymupdf`, or `pacman -S python-weasyprint python-pymupdf`)

Install pandoc on Debian/Ubuntu: `sudo apt install pandoc`
On Arch/CachyOS: `sudo pacman -S pandoc`

---

## 1. `convert.py` — Scrivener → Markdown + Manuskript

Converts a Scrivener 3 `.scriv` project into:

1. **Organized Markdown** — a folder tree of `.md` files with YAML frontmatter, one file per scene
2. **Manuskript** — a `.msk` project you can open directly in [Manuskript](https://www.theologeek.ch/manuskript/)

Preserves your full binder structure: Manuscript (parts → chapters →
scenes), Front Matter, Back Matter, and any other top-level folders.

```
python3 convert.py PROJECT.scriv [--title "My Novel"] [--author "Your Name"] [--output ./output]
```

| Argument | Description |
|---|---|
| `PROJECT.scriv` | Path to your Scrivener project folder (required) |
| `--title` | Project title — defaults to the `.scriv` folder name |
| `--author` | Author name written into the Manuskript project metadata |
| `--output` | Where to write the output (default: `output/` next to the `.scriv` folder) |

If `--author` is not provided, you will be prompted to enter one (or leave it blank).

### Example

```
python3 convert.py "My Novel.scriv" --title "My Novel" --author "Jane Smith"
```

Output:
```
output/
├── markdown/
│   ├── Manuscript/
│   │   ├── 01 - Part One/
│   │   │   ├── 01 - Chapter 1/
│   │   │   │   ├── 01 - Opening scene.md
│   │   │   │   └── 02 - Second scene.md
│   │   │   └── ...
│   │   └── ...
│   ├── Front Matter/
│   └── Back Matter/
├── My Novel.msk          ← open this in Manuskript
└── My Novel/             ← Manuskript project data
    ├── infos.txt
    ├── outline/
    └── ...
```

Open the Manuskript project: `manuskript "output/My Novel.msk"`

**What gets exported:** Manuscript (all parts/chapters/scenes), and every
other top-level folder (Front Matter, Back Matter, notes, research, etc.).
Research (PDF/web archive folders), Trash, and Template Sheets are skipped
automatically.

**Notes:**
- Scene content is converted from RTF using pandoc. Scrivener placeholder tags (`<$Scr_Ps::N>`, `<$author>`, etc.) are stripped automatically.
- The Markdown export strips pandoc's stray backticks (from RTF decorative-font spans) and converts author-note markers (`***note text`) to Obsidian's `%%comment%%` syntax, so the output is ready to drop straight into an Obsidian vault.
- The Manuskript export uses plain text instead, for the same backtick reason.
- Running the script again overwrites the previous output.

`make_obsidian_vault.py` is a small optional follow-up: it copies
`output/markdown/` to `output/markdown_obsidian/`, collapsing single blank
lines between paragraphs (Obsidian/this repo's vault convention stores one
paragraph per line, no blank line, so paragraphs read as one flowing block
in Obsidian's editor) while preserving intentional multi-line gaps and
leaving YAML frontmatter untouched.

---

## 2. The Obsidian pipeline

A vault-based workflow for writing and maintaining a novel long-term, with
repeatable compiles to distributable formats. One vault = one book. Works
with any number of books side by side — nothing is hardcoded per-book;
everything comes from two files at the vault root.

**[`vault-template/`](vault-template/)** is a ready-to-copy starting point —
a real (if tiny) vault in this exact convention, complete with a working
Obsidian setup (theme, plugins, editor preferences), that compiles as-is.
Copy it, edit `Book Info.md` and `Manuscript Reading Order.md`, and start
writing. See its own README for details.

### The vault convention

**`Manuscript Reading Order.md`** is the single source of truth for compile
order and structure — not folder names. Format:

```
- [[file|Title]]  _Front Matter_        (before the first Part; resolved by filename anywhere in the vault)

# Part I Some Part Name
> An optional
> three-line epigraph
> tercet, centered

## 1. Chapter Title
POV Character Name                      (required: bold/centered)
Location: wherever                      (optional: any number of extra lines,
Year: 999                                stacked below the POV name)
- [[scene-file]]                        (scenes, in reading order)
- [[scene-file]]

- [[file|Title]]  _Back Matter_         (after the last chapter)
```

Scene files are located by filename anywhere under the vault (only the
note's bullet order matters, not folder placement), so you're free to
organize the actual folder tree however you like.

**`Book Info.md`** holds book-specific metadata in a fenced ` ```book-info `
code block (not a `---` frontmatter block — Obsidian would hijack that into
its own Properties panel):

```
```book-info
title: "My Novel"
author: "Jane Smith"
author_file_as: "Smith, Jane"
publisher: "My Press"
isbn: "9780000000000"
cover: "cover.jpg"
output_dir: "/path/to/put/builds"
series: "My Series, Book 1"
```
```

`title`/`author`/`author_file_as`/`publisher` are required; the rest are
optional (blank `""` to omit). `cover` resolves relative to the vault root
unless given as an absolute path. If `series` is set, it (along with
`title`/`isbn`) is kept in sync automatically into the vault's Information
front-matter page at every compile, instead of being hand-edited there.

`obsidian_to_pdf.py` reads a few additional optional fields — see its
module docstring for the full list (title-page subtitle/layout, a
series-position dot row, page trim size, running headers, per-POV "sign"
glyphs).

### Scripts

**`obsidian_to_epub.py`** — compiles a vault into a distributable `.epub` via pandoc.
```
python3 obsidian_to_epub.py <vault> [--output-dir DIR]
```

**`obsidian_to_pdf.py`** — compiles the same vault into a print-style PDF via WeasyPrint (CSS Paged Media: page size, running headers, a page-numbered Contents page). Requires `weasyprint` and `pymupdf`.
```
python3 obsidian_to_pdf.py <vault> [--output-dir DIR]
```

**`obsidian_to_odt.py`** — compiles the same vault into an ODT (OpenDocument Text) manuscript via pandoc, styled to match the PDF as closely as ODT reasonably allows: a real title page, centered/hidden front matter, centered POV names, chapter dates, epigraphs, drop caps, and a running header, via pandoc's `custom-style` mechanism (a div/span attribute that maps onto a real named ODF paragraph/character style — unlike CSS-style `.classname` divs, which pandoc's ODT writer silently drops). Has its own independent renderer (not reused from `obsidian_to_epub.py`, unlike an earlier version) since ODT styling works fundamentally differently from epub's CSS classes. One known simplification versus the PDF: the header and page numbers appear on every page, including the title page and front matter, rather than starting fresh at page 1 on the manuscript body — a mid-document page-style switch is the spec-correct ODF way to do that, but confirmed via isolated testing that LibreOffice's headless PDF export doesn't honor it. Meant as an editable draft format for further work in a word processor, not a finished distributable.
```
python3 obsidian_to_odt.py <vault> [--output-dir DIR]
```

**`epub_to_obsidian.py`** — the reverse direction: turns an already-published epub into a vault in this same convention (chapters/scenes recovered from the epub's table of contents and its `—※—` scene-break markers), so you can bring an existing book under this workflow.
```
python3 epub_to_obsidian.py NOVEL.epub [--title "My Novel"] [--author "Your Name"] [--output ./output]
```

**`epub_style.css`** — the stylesheet `obsidian_to_epub.py` compiles with (`obsidian_to_pdf.py` uses its own equivalent CSS inline, tuned for print). Covers drop caps, POV/chapter-date header lines, part epigraphs, scene separators, and front/back-matter styling.

---

## License

MIT
