# CLAUDE.md

## Purpose
Help authors write books in Obsidian and convert them to other formats. EPUB, PDF and Word (.docx) are on the site;
ODT is a local-only script. The conversions run as free web tools at https://authortools.hyrumjones.com. The site
runs on Rum's Akamai Linode (74.207.227.123, the same box as links.hyrumjones.com). The Obsidian plugin route was
rejected; stick with the web tools.

## Where we left off (2026-10-04)
- Live on the site: Obsidian → EPUB / PDF / Word, Scrivener → Obsidian, EPUB → Obsidian, starter vault download,
  converter status badge + `/status` page. Everything on `main` is deployed.
- Next: Rum converts their own vaults' Reading Orders to the new `center:`/`left:`/`right:` labels and tests them.
  **Don't edit Rum's vaults** (SilentSub1/2 in ~/Dev, Sky's the Limit and the Saldari vaults in Dropbox); Rum does it.
  `obsidian-book check <vault>` lists the exact labelled lines to write. Their chapter titles are mixed case
  (`## One`), which used to be force-capitalised; titles now print as typed, so Rum may want capitals.
- After that: remove the temporary `reading_order_is_legacy()` fallback in `epub.py` (and its tests/warning).
- Open items: SilentSub2 has no cover (required now); the Saldari vaults link to a missing note
  `[[01-01-02 Korvalm energy - pass1]]`; the Word output hasn't been checked in real Microsoft Word, only
  LibreOffice; fonts aren't embedded in the .docx; POV sign images aren't in the Word version.
- `NEXT_STEPS.md` (gitignored) is Rum's own checklist.

## Layout
- `obsidian_book/`: the converters (`epub.py`, `pdf.py`, `docx.py`, `odt.py`) plus the `obsidian-book` CLI (`cli.py`).
  `epub.py` also holds the shared vault reading (Book Info, Reading Order, checks) the others import.
  `docx.py` = pandoc custom-style Markdown + XML post-processing (sections, headers, PAGEREF contents), no
  LibreOffice. When checking Word output in LibreOffice, export with IsSkipEmptyPages=false, or the odd-page blank
  pages vanish and parts look misplaced.
- Importers ("Bring your work in"): `from_epub.py` (any EPUB) and `from_scrivener.py` (.scriv) build a `Book` that
  `importer.py`'s `write_vault()` lays out as a vault. Their docstrings explain the structure guessing. Tested against
  Rum's real EPUBs and .scriv projects (part/chapter counts match).
- `webapp/`: Flask app: home page, `/convert` (epub, pdf, docx), `/import/epub` and `/import/scrivener`, job queue,
  `/starter-vault.zip`, header status badge (`status.js` polls `/queue`) and `/status` (24-hour chart; `jobs.py` keeps
  that history in memory, no titles, lost on restart). Import jobs use the format name `vault`; see `runner.py`.
- `vault-template/`: the starter vault, written for non-technical writers. It must always compile; CI builds it.
- `deploy/`: server setup (`DEPLOY.md`), systemd unit, nginx/Caddy config, `update.sh`.
- `convert.py`, `epub_to_obsidian.py`, `make_obsidian_vault.py`: older one-off tools, superseded by the importers.

## Vault rules (what the converters read)
- `Book Info.md`: one or more ```book-info blocks, read as one. Required: title, author, author_file_as,
  publisher, cover (the file must be in the vault). Optional: isbn, scene_break (default —※—; "blank" = one empty
  line), chapter_space_above (0–12 empty lines above chapter titles; blank = usual gap), header_left / header_right
  (PDF/Word/ODT running headers, as typed; blank = AUTHOR / TITLE; old `running_header` = right), trim_size,
  pov_signs_dir/pov_signs, output_dir (CLI only; blank = ~/Downloads). Old vaults may still have subtitle /
  title_page_lines / series_position / series_length / series; the first four only feed the built-in title page.
- `Manuscript Reading Order.md` (see `parse_reading_order()`): `- [[note]]  _Front Matter_` / `_Back Matter_`,
  `# Part` (optional), `## Chapter` (printed as typed), `- [[scene]]`. A `#` line ending "Reading Order" is the note's
  title. Under a part or chapter heading only `center:` / `left:` / `right:` lines print, with the writer's own
  *italic*/**bold**; other lines are ignored and listed by the check. `%%comments%%` (also multi-line) are stripped;
  the starter Reading Order ends with a `%%` help block.
- A front-matter note titled "Title Page" (`parse_title_page()`: # big, ## medium, ### small, plain, empty lines,
  ---) replaces the built-in title page in every format.
- Front/back matter, including Information, is printed exactly as written. The check warns (never blocks) about
  leftover starter text (`[Your Name]`, lorem ipsum, the example Reading Order lines).

## Rules
- Commit and push straight to `main`. No PRs, and never force-push.
- Pull main before starting and again before pushing.
- Run tests before pushing: `python -m pytest -q tests` (in a venv with pytest + `webapp/requirements.txt`), plus the
  EPUB smoke test: `python3 obsidian_to_epub.py vault-template --check && python3 obsidian_to_epub.py vault-template
  --output-dir build`. If epubcheck is installed, validate the output with it too. CI
  (`.github/workflows/epub-smoke-test.yml`) runs all of this.
- Uploaded vaults are untrusted. Keep the hardening in place: `untrusted=True` builds (Lua filter that also drops raw
  Word XML, cover must be inside the vault), a locked-down WeasyPrint fetcher, zip-slip and path checks, capped EPUB
  decompression in the importer, and per-job temp dirs. pandoc `--sandbox` can't be used because it refuses `--css`
  and `--epub-cover-image`.
- ODT isn't web-safe: it starts one shared LibreOffice on fixed port 2002, calls `sys.exit()` on bad vaults, and has
  no `untrusted` mode. The server has ~1 GB RAM. Word (.docx) replaced it on the site.
- Never commit real manuscripts or outputs. The starter `cover.jpg` (Through the Curtains) is Rum's, by permission.
- Rum runs CachyOS (Arch-based), so use pipx or pacman, not a global pip install. Keep replies compact and plain.
- Moon+ Reader ignores EPUB CSS. Test styling in Calibre, Apple Books or Thorium instead.

## Run locally
```sh
pip install -r webapp/requirements.txt   # in a venv; also needs pandoc
flask --app 'webapp.app:create_app()' run
```

## Deploy (on the Linode, as root)
`ssh root@74.207.227.123 sh /opt/authortools/app/deploy/update.sh` pulls main, installs requirements and restarts
the `authortools` service. Check it with `curl -s http://127.0.0.1:8300/healthz` and `journalctl -u authortools`.
Full setup is in `deploy/DEPLOY.md`. The server's Caddy also serves links, research and homeschool-tracker; leave
those alone.
