# Book Info

This fenced code block holds this book's metadata, read by
`obsidian_to_epub.py`, `obsidian_to_pdf.py`, and `obsidian_to_odt.py` (a
literal `---` frontmatter block isn't used here — Obsidian would hijack
that into its own Properties panel).

`title`/`author`/`author_file_as`/`publisher` are required — this template
fills them with placeholders so it compiles out of the box; replace them
with your own. Everything else is optional and can stay blank (`""`).

```book-info
title: "My Novel"
author: "Your Name"
author_file_as: "Last, First"
publisher: "Self-Published"
isbn: ""
cover: ""
output_dir: ""
series: ""
subtitle: ""
title_page_lines: ""
series_position: ""
series_length: ""
trim_size: ""
running_header: ""
pov_signs_dir: ""
pov_signs: ""
```

Notes on the optional fields:
- `cover` — a cover image filename, resolved relative to the vault root
  unless given as an absolute path.
- `output_dir` — left blank on purpose, so a first compile requires
  `--output-dir` explicitly rather than silently writing somewhere
  unexpected. Fill it in once you've picked a permanent location.
- `series` — if set, it's kept in sync automatically into `Front
  Matter/Information.md`'s opening two lines (title, then series) at every
  compile — see `obsidian_to_epub.py`'s `apply_information_overrides()`.
- `subtitle` / `title_page_lines` / `series_position` / `series_length` /
  `trim_size` / `running_header` / `pov_signs_dir` / `pov_signs` — read by
  `obsidian_to_pdf.py` (and `running_header` also by `obsidian_to_odt.py`)
  for the print title page, series-progress dot row, page size, running
  headers, and optional per-POV "sign" glyphs. See that script's module
  docstring for details.
