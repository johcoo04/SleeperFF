#!/usr/bin/env bash
#
# Weekly sync, run from cron on the Pi. See DEPLOY.md.
#
# Order matters: pull first, then sync, then publish the page. A failure
# anywhere aborts (set -e) and leaves the previously-served bundle in place,
# so a bad run degrades to stale data rather than to no data.
set -euo pipefail

REPO=/home/pi/SleeperFF
WEBROOT=/var/www/leaguehq
export FIREBASE_SERVICE_ACCOUNT=/home/pi/.config/sleeperff/serviceAccountKey.json

cd "$REPO"

# Blog posts are files in this checkout, so publishing a post means pushing it
# to main and letting the Pi pull. --ff-only on purpose: if someone has edited
# the Pi's tree directly, fail loudly instead of silently discarding it.
echo "== $(date -Is) pull =="
git pull --ff-only origin main

# Writes BOTH sinks: the static bundle nginx serves, and Firestore as a
# fallback for anyone loading the page from somewhere without the bundle.
# Drop --json-out for Firestore only, or add --skip-firestore for Pi only.
echo "== sync =="
venv/bin/python sync_pipeline.py --json-out "$WEBROOT/data"

# The page is static and versioned, so republish it every run — that way a
# dashboard change ships with the same pull that brought it in.
echo "== publish page =="
cp index.html "$WEBROOT/index.html"

echo "== done $(date -Is) =="
