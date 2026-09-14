#!/usr/bin/env python3
"""
make_obsidian_vault.py — Copy output/markdown/ to output/markdown_obsidian/
with single blank lines between paragraphs removed.

Rules:
  - 1 blank line between paragraphs  →  removed (paragraphs flow together)
  - 2+ blank lines (intentional gaps) →  kept as 1 blank line
  - YAML frontmatter is never touched

Usage:
    python3 make_obsidian_vault.py
"""

import re
import shutil
import sys
from pathlib import Path

SRC = Path("output/markdown")
DST = Path("output/markdown_obsidian")


def process_body(body: str) -> str:
    # Preserve intentional double gaps: collapse 3+ newlines → 2
    body = re.sub(r"\n{3,}", "\n\n", body)
    # Remove single blank lines (exactly 2 newlines → 1)
    body = re.sub(r"\n\n", "\n", body)
    return body


def process_file(src: Path, dst: Path) -> None:
    raw = src.read_text(encoding="utf-8")

    # Split off YAML frontmatter — don't touch it
    frontmatter = ""
    body = raw
    if raw.startswith("---\n"):
        closing = raw.find("\n---\n", 4)
        if closing != -1:
            frontmatter = raw[: closing + 5]
            body = raw[closing + 5 :]

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(frontmatter + process_body(body), encoding="utf-8")


def main() -> None:
    if not SRC.exists():
        sys.exit(f"ERROR: source not found: {SRC}")

    if DST.exists():
        shutil.rmtree(DST)
    DST.mkdir(parents=True)

    md_files = sorted(SRC.rglob("*.md"))
    print(f"Processing {len(md_files)} files  {SRC} → {DST} …")
    for src in md_files:
        dst = DST / src.relative_to(SRC)
        process_file(src, dst)
        print(f"  {src.relative_to(SRC)}")
    print("Done.")


if __name__ == "__main__":
    main()
