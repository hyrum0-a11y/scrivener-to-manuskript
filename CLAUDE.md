# CLAUDE.md

## Purpose
Help authors write books in Obsidian and convert them to other formats. EPUB, PDF and Word (.docx) are on the site; ODT is a local-only script.
The conversions run as free web tools at https://authortools.hyrumjones.com. The site runs on Rum's Akamai Linode
(74.207.227.123, the same box as links.hyrumjones.com). The Obsidian plugin route was rejected; stick with the web tools.

## Layout
- `obsidian_book/`: the converters (`epub.py`, `pdf.py`, `docx.py`, `odt.py`) plus the `obsidian-book` CLI (`cli.py`).
  `docx.py` = pandoc custom-style Markdown + XML post-processing (sections, headers, PAGEREF contents); no
  LibreOffice. A front-matter note titled "Title Page" (epub.parse_title_page) replaces the built-in title page
  in every format. Check LibreOffice renders with IsSkipEmptyPages=false or odd-page blanks vanish.
  Book Info `scene_break` (default —※—; "blank" = one empty line) is printed between scenes in every format.
  Importers ("Bring your work in"): `from_epub.py` (any EPUB) and `from_scrivener.py` (.scriv) build a
  `Book` that `importer.py`'s `write_vault()` lays out as a vault. Their docstrings explain the structure guessing.
  Structure comes from the vault's `Manuscript Reading Order.md`; see the docstring at the top of `epub.py`.
  Parts are optional. Chapters listed before any `# Part` heading compile with no Part page.
  Under a `#` part or `##` chapter heading, only `center:` / `left:` lines print (Obsidian *italic*/**bold** kept);
  other lines are ignored and listed by the check; `%%comments%%` are stripped. Chapter titles print as typed.
  TEMPORARY: `reading_order_is_legacy()` keeps the old positional POV/date lines and `>` epigraphs for Rum's
  unconverted vaults. Remove it once Rum has converted SilentSub1/2, Sky's the Limit and the Saldari vaults.
- `webapp/`: Flask app (home page, `/convert` upload, `/import/epub` and `/import/scrivener`, job queue,
  `/starter-vault.zip`). Converter status: a header badge on every page (`status.js` polls `/queue`) and a
  `/status` page with a 24-hour jobs chart; `jobs.py` keeps that history in memory (no titles, lost on restart). Import jobs use the format name `vault`; see `runner.py`.
- `vault-template/`: starter vault for writers (plain-language `Start Here.md`, two-block `Book Info.md`, example
  `cover.jpg` from Through the Curtains). It must always compile; CI builds it. `cover:` is required in every vault;
  a blank `output_dir:` means `~/Downloads`. Front/back matter (incl. Information) is printed exactly as written;
  the check warns (never blocks) when starter placeholder text like `[Your Name]` is left in.
- `deploy/`: server setup (`DEPLOY.md`), systemd unit, nginx/Caddy config, `update.sh`.
- `convert.py`, `epub_to_obsidian.py`, `make_obsidian_vault.py`: older one-off migration tools, superseded by
  the importers above.

## Rules
- Commit and push straight to `main`. No PRs, and never force-push.
- Pull main before starting and again before pushing.
- Run tests before pushing: `python -m pytest -q tests`, plus the EPUB smoke test:
  `python3 obsidian_to_epub.py vault-template --check && python3 obsidian_to_epub.py vault-template --output-dir build`
  If epubcheck is installed, validate the output with it too. CI (`.github/workflows/epub-smoke-test.yml`) runs all of this.
- Uploaded vaults are untrusted. Keep the hardening in place: `untrusted=True` builds (Lua filter, cover must be inside
  the vault), a locked-down WeasyPrint fetcher, zip-slip and path checks, and per-job temp dirs. pandoc `--sandbox` can't
  be used because it refuses `--css` and `--epub-cover-image`.
- ODT isn't web-safe yet: it starts one shared LibreOffice on fixed port 2002, calls `sys.exit()` on bad
  vaults, and has no `untrusted` mode. (`reference.odt` is already built per run.) The server has ~1 GB RAM.
- `NEXT_STEPS.md` (gitignored) holds the current plan if it exists. Never commit real manuscripts or outputs.
- Rum runs Arch Linux locally, so use pipx or pacman, not a global pip install. Keep replies compact.
- Moon+ Reader ignores EPUB CSS. Test styling in Calibre, Apple Books or Thorium instead.

## Run locally
```sh
pip install -r webapp/requirements.txt   # in a venv; also needs pandoc
flask --app 'webapp.app:create_app()' run
```

## Deploy (on the Linode, as root)
`sh /opt/authortools/app/deploy/update.sh` pulls main, installs requirements and restarts the `authortools` service.
Check it with `curl -s http://127.0.0.1:8300/healthz` and `journalctl -u authortools`. Full setup is in `deploy/DEPLOY.md`.
