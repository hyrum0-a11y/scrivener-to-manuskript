#!/bin/sh
# Pull the latest main and restart. Run as root on the server.
set -eu
sudo -u authortools git -C /opt/authortools/app pull --ff-only
sudo -u authortools /opt/authortools/venv/bin/pip install -q -r /opt/authortools/app/webapp/requirements.txt
systemctl restart authortools
systemctl --no-pager --lines=5 status authortools
