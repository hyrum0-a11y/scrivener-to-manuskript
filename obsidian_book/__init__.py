"""Compile an Obsidian book vault to EPUB, PDF or ODT.

Library use (e.g. from a web tool):

    from obsidian_book import BookError, build_epub, check
    path = build_epub("/path/to/vault", output_dir="/tmp/job123")

Command line: see `obsidian-book --help`.
"""

from obsidian_book.epub import BookError, build_epub, check

__all__ = ["BookError", "build_epub", "check"]
__version__ = "0.1.0"
