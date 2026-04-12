# scrivener-to-manuskript

Convert a **Scrivener 3** `.scriv` project to:

1. **Organized Markdown** — a folder tree of `.md` files with YAML frontmatter, one file per scene
2. **Manuskript** — a `.msk` project you can open directly in the [Manuskript](https://www.theologeek.ch/manuskript/) writing app on Linux

The script preserves your full binder structure: Manuscript (parts → chapters → scenes), Front Matter, Back Matter, and any other top-level folders (notes, research, etc.).

## Requirements

- Python 3.10+
- [pandoc](https://pandoc.org/installing.html) — used to convert RTF scene content to text

Install pandoc on Debian/Ubuntu:
```
sudo apt install pandoc
```
On Arch/Manjaro:
```
sudo pacman -S pandoc
```

## Usage

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

Open the Manuskript project:
```
manuskript "output/My Novel.msk"
```

## What gets exported

- **Manuscript** — all parts, chapters, and scenes with their content
- **All other top-level folders** — Front Matter, Back Matter, notes, research folders, etc.
- **Skipped automatically** — Research (PDF/web archive folders), Trash, Template Sheets

## Notes

- Scene content is converted from RTF using pandoc. Scrivener-specific placeholder tags (`<$Scr_Ps::N>`, `<$author>`, etc.) are stripped automatically.
- The Markdown export uses pandoc's markdown output, which preserves bold/italic formatting.
- The Manuskript export uses plain text to avoid pandoc backtick-wrapping non-standard RTF fonts (common for decorative chapter headings).
- Running the script again will overwrite the previous output.

## License

MIT
