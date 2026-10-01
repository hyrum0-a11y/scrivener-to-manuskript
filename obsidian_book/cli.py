"""`obsidian-book` command: one entry point for every vault conversion.

    obsidian-book check <vault>
    obsidian-book epub  <vault> [--output-dir DIR]
    obsidian-book pdf   <vault> [--output-dir DIR]
    obsidian-book odt   <vault> [--output-dir DIR]

PDF and ODT pull in heavy optional dependencies (WeasyPrint, PyMuPDF,
LibreOffice's uno), so their modules are only imported when that
subcommand runs — `epub` and `check` work with just pandoc installed.
"""

import argparse
import importlib
import sys

from obsidian_book import __version__
from obsidian_book.epub import BookError, build_epub, check

MISSING_DEP_HINTS = {
    "weasyprint": "pip install 'obsidian-book[pdf]'",
    "pymupdf": "pip install 'obsidian-book[pdf]'",
    "uno": "a native (non-Flatpak) LibreOffice install, which provides the uno module",
}


def _run_optional(module: str, argv: list) -> None:
    """Import obsidian_book.<module> and hand argv to its own main(),
    turning a missing optional dependency into a readable message."""
    try:
        mod = importlib.import_module(f"obsidian_book.{module}")
    except ModuleNotFoundError as e:
        hint = MISSING_DEP_HINTS.get(e.name.split(".")[0] if e.name else "")
        if hint is None:
            raise
        sys.exit(f"ERROR: `obsidian-book {module}` needs the '{e.name}' module. Install it with: {hint}")
    mod.main(argv)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="obsidian-book",
        description="Compile an Obsidian book vault. Book metadata comes from <vault>/Book Info.md "
                    "and the compile order from <vault>/Manuscript Reading Order.md.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("check", help="dry run: report broken links, orphaned notes and a missing cover")
    p.add_argument("vault", help="path to the Obsidian vault")

    for name, desc in [("epub", "compile to EPUB (needs pandoc)"),
                       ("pdf", "compile to a print-style PDF (needs the [pdf] extras)"),
                       ("odt", "compile to an editable ODT manuscript (needs pandoc, LibreOffice, PyMuPDF)")]:
        p = sub.add_parser(name, help=desc)
        p.add_argument("vault", help="path to the Obsidian vault")
        p.add_argument("--output-dir", help="override the vault's Book Info.md output_dir for this run")

    args = parser.parse_args(argv)

    if args.command in ("pdf", "odt"):
        forwarded = [args.vault] + (["--output-dir", args.output_dir] if args.output_dir else [])
        _run_optional(args.command, forwarded)
        return

    try:
        if args.command == "check":
            check(args.vault)
        else:
            build_epub(args.vault, args.output_dir)
    except BookError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
