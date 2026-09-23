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
venv/bin/pip install -r requirements.txt

# Add these only if you want them:
#   venv/bin/pip install -r requirements-firestore.txt   # keep the Firestore fallback
#   venv/bin/pip install -r requirements-excel.txt       # generate the Excel archive
```

The extras each include the base file, so whichever line you run is complete on
its own. `requirements-excel.txt` is the only one that pulls `pandas`/`numpy`.

## 2. Keep the service account key outside the repo

Only needed if you're keeping Firestore. `.gitignore` stops git, not `cp -r`,
backups, or editor sync — so put it somewhere the repo can't reach. The dev
machine follows the same rule and the same path, so the two don't drift:

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

League members are on other networks, so the page has to be reachable. Two
paths are set up on the Pi **at the same time** — they both just proxy to
local port 80, so they don't conflict, and either can serve the league.

### Cloudflare quick tunnel (currently the working one)

Free, needs no Cloudflare account and no domain. Installed as a service:

```bash
curl -fsSL -o cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
sudo dpkg -i cloudflared.deb
sudo systemctl enable --now cloudflared-quick
```

Unit lives at `/etc/systemd/system/cloudflared-quick.service` and runs
`cloudflared tunnel --no-autoupdate --url http://localhost:80`.

**The hostname is random and changes on every restart** — reboot, crash, or
`systemctl restart` all issue a new one. There is no way to pin it without a
domain. To find the current URL:

```bash
journalctl -u cloudflared-quick | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | tail -1
```

Because the URL moves, share a redirect (a bit.ly or similar) rather than the
raw hostname, and re-point it after a reboot.

### Tailscale Funnel (configured, blocked upstream)

Set up and correct, but **Tailscale never published the public DNS record**,
so `https://bonkheads.tail8b70e6.ts.net/` does not resolve. Everything on our
side checks out:

- `funnel` node attribute present, plus `funnel-ports?ports=443,8443,10000`
- MagicDNS and HTTPS Certificates enabled on the tailnet
- TLS cert issued successfully (so Tailscale's own `SetDNS` works)
- `Hostinfo.IngressEnabled changed to true`
- `tailscale funnel status` reports Funnel on, proxying to `127.0.0.1:80`

Authoritative DNS (`ns1.dnsimple.com`) returns NOERROR with no A, AAAA, or
CNAME record. Tried: waiting well past the documented 10 minutes, toggling
Funnel off and on, restarting `tailscaled`, and renaming the node (which
forces republication under a new name). None produced a record. This matches
a known class of upstream bug — tailscale/tailscale#7103.

It is left **armed on purpose**: if Tailscale ever publishes the record, the
hostname starts working with no action needed, and it costs nothing to leave
running. Check with:

```bash
dig +short @ns1.dnsimple.com bonkheads.tail8b70e6.ts.net A
```

### If you ever buy a domain

A named Cloudflare tunnel gives a stable hostname and drops the restart
problem entirely. Point it at `http://localhost:80` exactly like the quick
tunnel; nothing else on the Pi changes.

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
