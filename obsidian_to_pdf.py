#!/usr/bin/env python3
"""Kept for existing workflows: same as `obsidian-book pdf`. The code now
lives in obsidian_book/pdf.py (see its module docstring)."""

from obsidian_book.pdf import *  # noqa: F401,F403 — keeps old imports working
from obsidian_book.pdf import main

if __name__ == "__main__":
    main()
