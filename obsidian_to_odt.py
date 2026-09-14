#!/usr/bin/env python3
"""
obsidian_to_odt.py — Compile an Obsidian vault into an ODT (OpenDocument
Text) manuscript via pandoc, for further formatting in a word processor
(LibreOffice Writer, etc.) or handing to an editor/proofreader who wants a
plain document rather than an epub/PDF.

Like obsidian_to_epub.py and obsidian_to_pdf.py, the compile order/structure
comes entirely from "Manuscript Reading Order.md" at the vault root, and
book metadata (title, author, ...) comes from a "Book Info.md" note — see
obsidian_to_epub.py's module docstring for both file formats. This script
reuses obsidian_to_epub.py's document assembly directly (parse_book_info,
build_document) instead of duplicating it: the same markdown chunks that
produce the epub are handed to pandoc's ODT writer instead of its epub
writer. Pandoc's ODT output silently drops the epub-only fenced-div classes
(.povname, .chapterdate, .dropcap, etc.) used there for CSS styling — bold
and italic survive, but none of the epub/PDF's typographic styling (drop
caps, centered POV names, running headers, page layout) does. This is meant
as an editable draft format, not a finished distributable, unlike the other
two scripts.

Requirements:
  - Python 3.10+
  - pandoc  (https://pandoc.org/installing.html)

Usage:
    python3 obsidian_to_odt.py <vault> [--output-dir DIR]
"""

import argparse
import io
import re
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from obsidian_to_epub import parse_book_info, build_document

SCRIPT_DIR = Path(__file__).parent
REFERENCE_ODT = SCRIPT_DIR / "reference.odt"


def patch_styles(xml: str) -> str:
    """Patch Text_20_body style for novel paragraph formatting: justified,
    0.14" first-line indent, no extra spacing between paragraphs."""
    new_props = (
        '<style:paragraph-properties '
        'fo:margin-top="0in" fo:margin-bottom="0in" '
        'fo:text-align="justify" fo:text-indent="0.14in" '
        'style:contextual-spacing="false" />'
    )

    def replace_props(m: re.Match) -> str:
        block = m.group(0)
        return re.sub(r'<style:paragraph-properties[^/]*/>', new_props, block)

    return re.sub(
        r'<style:style style:name="Text_20_body".*?</style:style>',
        replace_props,
        xml,
        flags=re.DOTALL,
    )


def build_reference_odt() -> None:
    """(Re)generate reference.odt next to this script, with novel paragraph
    styles patched into pandoc's default ODT template. Rebuilt on every run
    so it always matches the current patch_styles() logic — not checked
    into git (see .gitignore), same as the old compile_book.py convention."""
    result = subprocess.run(
        ["pandoc", "--print-default-data-file", "reference.odt"],
        capture_output=True,
    )
    src = zipfile.ZipFile(io.BytesIO(result.stdout))
    dst_buf = io.BytesIO()
    with zipfile.ZipFile(dst_buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "styles.xml":
                data = patch_styles(data.decode()).encode()
            dst.writestr(name, data)
    REFERENCE_ODT.write_bytes(dst_buf.getvalue())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compile an Obsidian vault into an ODT manuscript (pandoc). "
                    "Book metadata is read from <vault>/Book Info.md — see "
                    "obsidian_to_epub.py's module docstring.")
    parser.add_argument("vault", help="path to the Obsidian vault to compile")
    parser.add_argument("--output-dir", help="override the vault's Book Info.md output_dir for this run")
    args = parser.parse_args()

    vault = Path(args.vault).expanduser().resolve()
    if not (vault / "Manuscript").is_dir():
        sys.exit(f"ERROR: {vault} does not look like a vault (no Manuscript/ folder)")

    book_info = parse_book_info(vault)
    output_dir_str = args.output_dir or book_info["output_dir"]
    if not output_dir_str:
        sys.exit("ERROR: no output directory given — set output_dir in Book Info.md or pass --output-dir.")
    output_dir = Path(output_dir_str).expanduser().resolve()

    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    output = (output_dir / f"{book_info['title']}_{timestamp}.odt").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    print("Building reference styles...")
    build_reference_odt()

    print("Assembling manuscript...")
    content = build_document(vault, book_info)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", encoding="utf-8", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    # No --metadata title=/author= here: pandoc's default template turns
    # those into a visible title block before the content, duplicating the
    # vault's own Information page — same gotcha obsidian_to_epub.py works
    # around via --epub-metadata instead of --metadata for that reason.
    print("Running pandoc...")
    result = subprocess.run(
        ["pandoc", tmp_path,
         "--reference-doc", str(REFERENCE_ODT),
         "-o", str(output)],
        capture_output=True, text=True,
    )
    Path(tmp_path).unlink()

    if result.returncode != 0:
        sys.exit(f"pandoc error:\n{result.stderr}")

    print(f"Done. Written: {output}")


if __name__ == "__main__":
    main()
