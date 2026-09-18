# Deploying on a Raspberry Pi

The whole league is ~105 KB of JSON and the page is ~36 KB, so resources are a
non-issue — this runs comfortably on any Pi. The only sizing note: `pandas` and
`numpy` are heavy on ARM and are needed **only** by `main.py` (the Excel
archive). The sync path doesn't import them, so a Pi that only serves the
dashboard can skip them.

## 1. Install

```bash
sudo apt update && sudo apt install -y python3-venv nginx
git clone git@github.com:johcoo04/SleeperFF.git /home/pi/SleeperFF
cd /home/pi/SleeperFF
python3 -m venv venv

# Dashboard only (no Excel, no Firestore): one dependency.
venv/bin/pip install requests

# Add these only if you want them:
#   venv/bin/pip install firebase-admin        # keep the Firestore fallback
#   venv/bin/pip install pandas openpyxl       # generate the Excel archive
```

## 2. Keep the service account key outside the repo

Only needed if you're keeping Firestore. `.gitignore` stops git, not `cp -r`,
backups, or editor sync — so put it somewhere the repo can't reach:

```bash
mkdir -p /home/pi/.config/sleeperff && chmod 700 /home/pi/.config/sleeperff
mv sleeper-league-hq-firebase-adminsdk-*.json /home/pi/.config/sleeperff/serviceAccountKey.json
chmod 600 /home/pi/.config/sleeperff/serviceAccountKey.json
```

## 3. Serve the page

```bash
sudo mkdir -p /var/www/leaguehq
sudo chown pi:pi /var/www/leaguehq
```

`/etc/nginx/sites-available/leaguehq`:

```nginx
server {
    listen 80;
    server_name _;
    root /var/www/leaguehq;
    index index.html;

    # The bundle is rewritten weekly; never let a stale copy stick around.
    location = /data/league.json {
        add_header Cache-Control "no-cache, must-revalidate";
    }
}
```

```bash
sudo ln -sf /etc/nginx/sites-available/leaguehq /etc/nginx/sites-enabled/leaguehq
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

## 4. Sync weekly

`sync.sh` in the repo root:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /home/pi/SleeperFF
export FIREBASE_SERVICE_ACCOUNT=/home/pi/.config/sleeperff/serviceAccountKey.json

# Writes BOTH sinks: the static bundle nginx serves, and Firestore as a
# fallback for anyone loading the page from somewhere without the bundle.
# Drop --json-out for Firestore only, or add --skip-firestore for Pi only.
venv/bin/python sync_pipeline.py --json-out /var/www/leaguehq/data

cp index.html /var/www/leaguehq/index.html
```

```bash
chmod +x sync.sh
crontab -e
# Tuesdays 09:05 local — after Monday Night Football has settled.
5 9 * * 2 /home/pi/SleeperFF/sync.sh >> /home/pi/sync.log 2>&1
```

The bundle is written to a temp file and renamed (atomic on POSIX), so nginx
can never serve a half-written file mid-sync.

## 5. Reach it from outside the house

League members are on other networks, so the page needs to be reachable. In
preference order:

1. **Cloudflare Tunnel (recommended).** Free, gives HTTPS and a real hostname,
   and needs **no port forwarding** — the Pi makes an outbound connection, so
   your home IP is never exposed and no inbound ports are opened. Install
   `cloudflared` on the Pi, then `cloudflared tunnel login`, create a tunnel,
   point it at `http://localhost:80`, and route a DNS hostname to it. Run it as
   a systemd service so it survives reboots. (Follow Cloudflare's current docs
   for exact commands — they change.)
2. **Tailscale.** Simplest and most private, but every viewer must install
   Tailscale and be on your tailnet. Fine for you, awkward for 5 league members.
3. **Port forwarding + dynamic DNS.** Works, but exposes your home IP and an
   inbound port, and you're responsible for TLS. Least preferred.

Whichever you pick, the page itself doesn't change.

## 6. Which source is the page using?

The header shows it. `Static data · synced <timestamp>` means it read
`data/league.json`; `Live Firestore` means the bundle wasn't reachable and it
fell back. In static mode the Firebase SDK is never downloaded at all.

## Notes

- Use `http://`, never `file://` — ES module imports and `fetch()` both fail on
  `file://`.
- `--season` refuses to write a JSON bundle, because the bundle is whole-league
  and a single season would replace all of it. Run without `--season`.
- The `blogs` array in the bundle is currently always empty; nothing writes blog
  posts yet. See HANDOFF.md.
