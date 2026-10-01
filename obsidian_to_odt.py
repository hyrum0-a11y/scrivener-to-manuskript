#!/usr/bin/env python3
"""Kept for existing workflows: same as `obsidian-book odt`. The code now
lives in obsidian_book/odt.py (see its module docstring)."""

from obsidian_book.odt import *  # noqa: F401,F403 — keeps old imports working
from obsidian_book.odt import main

if __name__ == "__main__":
    main()
