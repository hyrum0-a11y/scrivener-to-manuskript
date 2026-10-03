# CLAUDE.md

## Purpose
Help authors write books in Obsidian and convert them to other formats. EPUB comes first, then PDF, and ODT last.
The conversions run as free web tools at https://authortools.hyrumjones.com. The site runs on Rum's Akamai Linode
(74.207.227.123, the same box as links.hyrumjones.com). The Obsidian plugin route was rejected; stick with the web tools.

## Layout
- `obsidian_book/`: the converters (`epub.py`, `pdf.py`, `odt.py`) plus the `obsidian-book` CLI (`cli.py`).
  Structure comes from the vault's `Manuscript Reading Order.md`; see the docstring at the top of `epub.py`.
  Parts are optional. Chapters listed before any `# Part` heading compile with no Part page.
- `webapp/`: Flask app (home page, `/convert` upload, job queue, `/starter-vault.zip`).
- `vault-template/`: starter vault. It must always compile; CI builds it.
- `deploy/`: server setup (`DEPLOY.md`), systemd unit, nginx/Caddy config, `update.sh`.
- `convert.py`, `epub_to_obsidian.py`, `make_obsidian_vault.py`: older one-off migration tools.

## Rules
- Commit and push straight to `main`. No PRs, and never force-push.
- Pull main before starting and again before pushing.
- Run tests before pushing: `python -m pytest -q tests`, plus the EPUB smoke test:
  `python3 obsidian_to_epub.py vault-template --check && python3 obsidian_to_epub.py vault-template --output-dir build`
  If epubcheck is installed, validate the output with it too. CI (`.github/workflows/epub-smoke-test.yml`) runs all of this.
- Uploaded vaults are untrusted. Keep the hardening in place: `untrusted=True` builds (Lua filter, cover must be inside
  the vault), a locked-down WeasyPrint fetcher, zip-slip and path checks, and per-job temp dirs. pandoc `--sandbox` can't
  be used because it refuses `--css` and `--epub-cover-image`.
- ODT isn't web-safe yet: it uses a fixed LibreOffice port and a shared `reference.odt`.
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
