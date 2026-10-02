# Deploying authortools.hyrumjones.com

The web app lives in `webapp/`. It runs as one gunicorn process on
`127.0.0.1:8300` behind the server's existing reverse proxy (nginx or
Caddy, whichever already serves links.hyrumjones.com). DNS already points
`authortools.hyrumjones.com` at the Linode (74.207.227.123).

Run these as root (or with `sudo`) on the Linode. Commands assume
Debian/Ubuntu.

## 1. Install packages

```sh
apt update
apt install -y python3 python3-venv git
pandoc --version || true
```

pandoc needs version 2.11 or newer. Ubuntu 24.04 / Debian 12's `apt install pandoc`
is fine. On older releases, install the current `.deb` from
<https://github.com/jgm/pandoc/releases> instead:

```sh
curl -LO https://github.com/jgm/pandoc/releases/download/3.8.2/pandoc-3.8.2-1-amd64.deb
apt install -y ./pandoc-3.8.2-1-amd64.deb
```

## 2. Create the user and get the code

```sh
useradd --system --home /opt/authortools --shell /usr/sbin/nologin authortools
mkdir -p /opt/authortools && chown authortools: /opt/authortools
sudo -u authortools git clone https://github.com/hyrum0-a11y/scrivener-to-manuskript /opt/authortools/app
sudo -u authortools python3 -m venv /opt/authortools/venv
sudo -u authortools /opt/authortools/venv/bin/pip install -r /opt/authortools/app/webapp/requirements.txt
```

## 3. Start the service

```sh
cp /opt/authortools/app/deploy/authortools.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now authortools
curl -s http://127.0.0.1:8300/healthz      # prints: ok
```

## 4. Hook up the reverse proxy and HTTPS

Check which one the server uses: `systemctl is-active nginx caddy`.

**nginx:**

```sh
cp /opt/authortools/app/deploy/nginx-authortools.conf /etc/nginx/sites-available/authortools
ln -s /etc/nginx/sites-available/authortools /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
apt install -y certbot python3-certbot-nginx   # skip if certbot is already there
certbot --nginx -d authortools.hyrumjones.com
```

**Caddy:** append `deploy/Caddyfile.snippet` to `/etc/caddy/Caddyfile`, then
`systemctl reload caddy`. Caddy gets the certificate itself.

Then open <https://authortools.hyrumjones.com>.

## Updating

```sh
sh /opt/authortools/app/deploy/update.sh
```

## Logs and troubleshooting

- `journalctl -u authortools -f` shows the app log.
- Job folders live in `/var/lib/authortools/jobs/` and are deleted an hour
  after a conversion finishes (and on every restart). The uploaded vault is
  deleted as soon as its conversion ends.
- Limits (in `webapp/jobs.py`'s `Limits`): 200 MB / 5000 files unzipped,
  120 s of CPU and 300 s wall time per conversion, 20 uploads waiting.
  Upload size is `AUTHORTOOLS_MAX_UPLOAD_MB` in the service file plus the
  proxy's body limit; change both together.

## Security notes

- Uploads are unzipped with checks for absolute paths, `..`, symlinks and
  zip bombs (`webapp/safezip.py`).
- Each conversion runs in a child process with CPU and file-size limits,
  as the unprivileged `authortools` user, under systemd sandboxing
  (read-only system, no home directories, no outbound network).
- pandoc runs with `build_epub(untrusted=True)`: the cover must be inside
  the vault, and images or raw HTML that point at absolute paths, `..` or
  URLs are dropped. pandoc's own `--sandbox` flag is not used because it
  also blocks the stylesheet and cover image (tested on pandoc 3.1 and 3.8).
