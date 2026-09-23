# Fantasy League HQ — Current State (v5)

Last updated 2026-09-20. Replaces v4. Earlier handoffs are in `archive/`.

v4 described an Excel-only pipeline whose Firestore track was optional and
unbuilt. That's no longer the shape of the project: the scoring logic has been
consolidated into one module, four seasons are live in Firestore *and* in a
static JSON bundle, and the dashboard reads from either. What's left is
deployment and one product decision.

---

## 1. Architecture

```
league_data.json          config: 4 league IDs, base_url, weeks_to_fetch
        |
league_core.py            THE rules: fetch, owner identity, ranking,
        |                 tie-breaks, live-week capping
        +-- main.py           -> 10-sheet Excel workbook (archive)
        +-- sync_pipeline.py  -> Firestore documents and/or static league.json
                    |             ^
blogs/*.md -> blog_core.py -------+  markdown posts, both sinks
                    |
              index.html      prefers data/league.json, falls back to Firestore
```

`league_core.py` exists because `main.py` and `sync_pipeline.py` previously
carried independent copies of the same scoring rules — every fix had to land
twice. Core now owns anything answering "who is this owner?" or "how is a week
scored?"; the two entry points own only their output shaping, which
legitimately differs.

### Files

| File | Role |
|---|---|
| `league_core.py` | Shared rules. Change scoring here and nowhere else. |
| `main.py` | Excel archive. `python main.py` |
| `sync_pipeline.py` | Firestore + JSON sinks. See flags below. |
| `index.html` | Dashboard. Dual-source, no build step. |
| `blog_core.py` | Markdown blog posts -> post documents. |
| `blogs/` | The posts themselves. `_`-prefixed files don't publish. |
| `test_league_core.py` | 24 tests, no network. |
| `test_blog_core.py` | 28 tests, no network. |
| `league_data.json` | The only place league IDs live. |
| `firestore.rules` | Read-only for browsers; all writes denied. |
| `requirements*.txt` | Split by intent: base (`requests`), `-firestore`, `-excel`. |
| `DEPLOY.md` | Raspberry Pi deployment procedure. |
| `archive/` | Superseded `main.py`/`main2.py`, handoffs v1-v4, pre-fix xlsx. |

### sync_pipeline.py flags

```
python sync_pipeline.py                            # Firestore only
python sync_pipeline.py --json-out ./data          # Firestore + static bundle
python sync_pipeline.py --json-out ./data --skip-firestore   # no Firebase at all
python sync_pipeline.py --season 2026              # one season (Firestore only)
python sync_pipeline.py --dry-run                  # compute, write nothing
python sync_pipeline.py --blogs-dir ./blogs        # default is ./blogs
```

`--season` refuses to write a JSON bundle: the bundle is whole-league, so a
single season would replace all of it.

### Credentials

The service account key is **not in the repo** on any machine. It lives at
`~/.config/sleeperff/serviceAccountKey.json` (dir `700`, file `600`), and the
pipeline finds it through the environment:

```bash
export FIREBASE_SERVICE_ACCOUNT=~/.config/sleeperff/serviceAccountKey.json
```

Without that variable `sync_pipeline.py` looks for `serviceAccountKey.json`
next to itself, finds nothing, and exits with a message telling you so — it
never writes to Firestore unauthenticated. `--dry-run`, `--skip-firestore`,
and JSON-only runs need no key at all. `.gitignore` covers the key by name,
but gitignore only stops git; keeping the file outside the repo is what stops
`cp -r`, backups, and editor sync.

---

## 2. The owner identity rule (most important thing in this repo)

**`owner_id` is the canonical identity. Nothing may key on a name — not a
dict key, not a lookup, not a join field.** Two independent reasons, both
confirmed against live data:

1. Sleeper returns **no `username` for any of the 6 owners**, in any season.
   The original code defaulted them all to the literal string `"Unknown"` and
   used it as a key, collapsing all six into one garbage row.
2. **One owner renamed themselves** — `SillyG00SE69` in 2023, `SillyG00SE13`
   in 2024+ (owner_id `1004588088431058944`). A name key forks their career
   into two partial owners.

Display names follow the latest season seen, so a renamed owner shows their
current name while accumulating under one ID.

This deliberately deviates from the original spec, which says to key on
`username` (§5.2) and shows `"user1_vs_user2"` H2H keys (§4). The live data
disproves the spec. Firestore stores `owner_id`, `opponent_owner_id`, and
`{owner_id}_vs_{owner_id}`, with `display_name` alongside for rendering.

---

## 3. Scoring rules

Points are awarded by weekly rank across the whole league, not by matchup:

- **2023-2024:** ranks 1-2 = 2 pts, 3-4 = 1 pt, 5-6 = 0 → 6 pts/week
- **2025+:** ranks 1-2 = 2 pts, 3-5 = 1 pt, 6 = 0 → 7 pts/week

Ties use competition ranking (1, 2, 2, 4) and a tied group splits the average
of what its ranks would each pay. **There has never been a tie in 52 weeks of
league history**, so that path is covered by synthetic tests only and has no
real-data evidence. Nothing to do about it but wait for one.

Head-to-head records are tracked and displayed but deliberately do not affect
standings.

---

## 4. Bugs found and fixed (all verified against live data)

- **Phantom head-to-head games.** Weeks 15 and 17 of *every* season have
  exactly two rosters with a null `matchup_id` (playoff byes). The old
  grouping paired those two as each other's opponent, inventing 6 fake H2H
  results across league history. Now null matchup_ids are skipped, and H2H
  reconciles exactly: 45 fully-paired weeks x3 + 6 playoff weeks x2 = 147
  games, with all 30 directed pairings mirroring correctly.
- **Owner collapse / rename fork** — see section 2.
- **Frozen owner name** in Scoreboard Summary (showed the 2023 name while
  Owner Summary showed the current one).
- **Duplicated league IDs** — `sync_pipeline.py` had a hardcoded list separate
  from `league_data.json`, so adding a season could update one pipeline only.
- **Hardcoded season list** in `index.html`. Now data-driven: in static mode
  the page uses whatever seasons the bundle contains, so adding 2027 is a
  `league_data.json` edit alone.
- **Credential gitignore gap** — `serviceAccountKey.json` wasn't ignored at
  all, and the later `*-firebase-adminsdk-*.json` pattern matched Google's
  filename only by luck. Patterns now match anywhere in the name, any
  extension. Verified no key file exists anywhere in git history.

---

## 5. What's verified

- **Both pipelines run for real** against the live Sleeper API, all 4 seasons.
- **Excel and Firestore/JSON agree owner-for-owner** on Scoreboard Points and
  points-for — two independent shapings of one core.
- **Firestore documents read back and validated field-by-field** against what
  `index.html` actually dereferences, including non-null on every field it
  calls `.toFixed()` on (a null there blanks a tab).
- **Live-week capping is now active.** Live state is 2026 week 2, so 2026 is
  capped to week 1 complete while 2023-2025 fetch in full. This was dormant
  until 2026 was added.
- **52 unit tests** (24 league + 28 blog), no network required.
- **Live Firestore rules match `firestore.rules`** (read from the console
  2026-09-20): public read, all writes denied.
- **The blog pipeline end to end**: a post in `blogs/` parses, lands in
  `league.json`, and carries every field `index.html` dereferences.

### Deployed (2026-09-22)

Raspberry Pi 4B (`jc-pi-home`, `192.168.1.23`), Debian 13, Python 3.13.
nginx serving `/var/www/leaguehq`, enabled on boot. venv holds `requests` +
`firebase-admin` only — no pandas on the Pi. 52 tests pass there. Key at
`/home/pi/.config/sleeperff/serviceAccountKey.json` (600). Cron runs
`sync.sh` Tuesdays 09:05 America/New_York, logging to `/home/pi/sync.log`.
First real sync wrote both sinks and served back 110.9 KB over HTTP with the
no-cache header intact. Publicly reachable via a Cloudflare quick tunnel
(`cloudflared-quick.service`); Tailscale Funnel is installed and armed but
blocked by an upstream DNS-publication bug. See `DEPLOY.md` §5 for both.

### Not verified
- The tie-breaker path (no real ties exist).
- `index.html` rendering — the first browser load happened and "looks good",
  but no systematic pass over every tab.
- Any Pi deployment.

---

## 6. Blog authoring (decided and built, 2026-09-20)

Posts are **markdown files in `blogs/`**, not documents hand-written in the
Firebase console. Both options worked; markdown won because posts then
version-control with the league data, survive `--skip-firestore`, and don't
need a Firestore write rule (`firestore.rules` denies every write, and the
console path would have required opening one).

`blog_core.py` parses them; `sync_pipeline.py` ships them to **both** sinks,
so the tab works whichever source the page picked. Content ships as **raw
markdown** — `index.html` already loads marked.js and renders client-side, so
the pipeline adds no dependency.

Three decisions inside that are easy to get wrong later:

- **`season` is a string, `week` is an int.** The archive filters compare
  season against a `<select>` value and sort weeks numerically. Swapping them
  silently empties both filters. There's a test pinning this.
- **`created_at` is synthesized from season+week**, not file mtime. `git
  clone` on the Pi stamps every file with the checkout time, which would
  flatten the archive's order. An explicit `date:` still wins.
- **Featured is derived, not flagged.** The newest post is featured
  automatically, because a manual flag needs clearing every single week and
  forgetting leaves a stale recap up forever. `featured: true` pins one.

A malformed post is warned about and skipped, never fatal — a cron sync must
still deliver the week's scores if a recap has a typo. Deleting a file
unpublishes the post from both sinks; the folder is the source of truth.

See `blogs/_README.md` for the authoring format.

---

## 7. Firestore on the Pi (decided 2026-09-20)

**Keep both sinks.** The Pi writes the static bundle *and* Firestore, so a
viewer still sees data when the Pi is down or unreachable. The cost is the
service account key living on the Pi, which `DEPLOY.md` §2 already handles by
keeping it in `~/.config/sleeperff/` outside the repo. `--skip-firestore`
remains available and needs no code change if that trade stops being worth it.

This requires no work: both sinks is what `sync.sh` in `DEPLOY.md` already
does.

---

## 8. Current data

4 seasons / 52 weeks / 6 owners. Entire league history is ~108 KB of JSON;
the page is ~36 KB. Growth is ~38 KB/season. Resources are not a constraint
on any Pi. `pandas`/`numpy` are the only heavy ARM dependencies and are used
**only** by `main.py`, which is why they sit in `requirements-excel.txt` rather
than the base file — a Pi that just serves the dashboard installs `requests`
alone (add `requirements-firestore.txt` if keeping the Firestore fallback).

As of 2026-09-20 the live NFL state is season 2026 week 2, so week 1 is the
only completed 2026 week. There is nothing new to sync until the NFL rolls to
week 3.

---

## 9. Open items

Ordered by what blocks what. Items 1-2 were the two open *decisions*; both are
now made and item 2 is built, so what's left is mostly deployment.

### The Pi — deployed and publicly reachable 2026-09-22
1. **The public URL moves on every restart.** A Cloudflare *quick* tunnel is
   live and serving the league (`cloudflared-quick.service`, enabled on
   boot). Quick tunnels need no account and no domain, but the hostname is
   random and reissued on every restart — so share a redirect (bit.ly) and
   re-point it after a reboot. Find the current URL with:
   `journalctl -u cloudflared-quick | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | tail -1`.
   A domain would fix this permanently via a named tunnel; it was considered
   and declined as not worth the money.

   **Tailscale Funnel is also configured and left armed**, but Tailscale never
   published the public DNS record, so its hostname doesn't resolve. Our side
   is verifiably correct (funnel attr, ports cap, cert issued, ingress
   enabled); authoritative DNS returns no record. Waiting, toggling,
   restarting `tailscaled`, and renaming the node all failed. Known upstream
   bug class. If it ever publishes, it starts working on its own — that's why
   it's still running. See `DEPLOY.md` §5.
2. ~~`sync.sh` doesn't handle blogs.~~ **Done.** `sync.sh` is now in the repo
   and starts with `git pull --ff-only origin main`, so publishing a post is
   "push to main" and the Pi picks it up on the next run.

### Content
3. ~~Write a real post.~~ **Done.** 49 posts are live: the league's back
   catalogue (2023 wk1 - 2025 wk15) plus 2026 weeks 1-2. `blogs/_TEMPLATE.md`
   is the starting point for new ones; keep the `_` while drafting and drop it
   to publish. 2023 week 8 deliberately has two posts (original and a
   "(Corrected)" follow-up) — that's the league's record, not a bug.

### Security / config hygiene
4. **Restrict the Firebase web API key** — *partially attempted 2026-09-22
   and effectively still open.* All 25 available APIs were left ticked, which
   is equivalent to unrestricted; verified by REST probe that nothing is
   blocked. The half that actually matters is **Application restrictions ->
   Websites**, still set to None, and it needs the Cloudflare Tunnel hostname
   before it can be filled in. Do it right after the tunnel. Full detail: in the Google Cloud console (API
   restrictions + HTTP referrer allowlist). The key in `index.html` is a public
   client identifier and is safe in a public repo, but unrestricted it can be
   reused against this project's quota. Flagged in the code comment since day
   one, never done.
5. ~~Confirm `firestore.rules` is deployed.~~ **Done 2026-09-20.** The live
   rules were read out of the Firebase console and match this repo's
   `firestore.rules` exactly: public read on `seasons`/`meta`/`blogs`, every
   write denied, not test mode. Re-check after any console edit — there is no
   CLI on this machine, so changes here don't auto-deploy. Publishing is a
   paste into the console, or `npx firebase-tools deploy --only
   firestore:rules` after a login.
6. **Set `FIREBASE_SERVICE_ACCOUNT` in your shell profile** on the dev machine,
   or Firestore writes fail with a "key not found" exit. See §1 Credentials.

### Verification gaps
7. **Systematic pass over `index.html`.** The LAN page was eyeballed and looks
   right, but no tab has been checked field by field — including the blog tab
   now that it carries 49 posts, and the archive's season/week filters, which
   have never been exercised against a real archive.
8. **The tie-breaker path.** No real tie has occurred in 52 weeks, so it's
   covered by synthetic tests only. Nothing to do but wait for one.

### Housekeeping
9. ~~Merge to `main`.~~ **Done 2026-09-22.** Fast-forwarded 11 commits; the
   Pi tracks `main`.
10. **2027 and beyond:** add the league ID to `league_data.json`. Nothing else
    needs to change — season list, rule table, and week capping all follow.

---

## 10. Environment gotcha

Claude Code runs inside **Warp** (`dev.warp.Warp-Stable`). macOS TCC gates
`~/Documents`, and an OS or app update can silently revoke it, after which
every file operation fails with `Operation not permitted` even though the
files are fine. Fix: System Settings -> Privacy & Security -> Files and
Folders -> Warp -> enable Documents Folder, then fully quit (Cmd-Q) and
relaunch Warp. `claude --resume` returns to the session.
