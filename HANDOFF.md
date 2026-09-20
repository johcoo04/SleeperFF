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
| `test_league_core.py` | 24 tests, no network. `python -m unittest test_league_core` |
| `league_data.json` | The only place league IDs live. |
| `firestore.rules` | Read-only for browsers; all writes denied. |
| `DEPLOY.md` | Raspberry Pi deployment procedure. |
| `archive/` | Superseded `main.py`/`main2.py`, handoffs v1-v4, pre-fix xlsx. |

### sync_pipeline.py flags

```
python sync_pipeline.py                            # Firestore only
python sync_pipeline.py --json-out ./data          # Firestore + static bundle
python sync_pipeline.py --json-out ./data --skip-firestore   # no Firebase at all
python sync_pipeline.py --season 2026              # one season (Firestore only)
python sync_pipeline.py --dry-run                  # compute, write nothing
```

`--season` refuses to write a JSON bundle: the bundle is whole-league, so a
single season would replace all of it.

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
- **24 unit tests**, no network required.

### Not verified
- The tie-breaker path (no real ties exist).
- `index.html` rendering — the first browser load happened and "looks good",
  but no systematic pass over every tab.
- Any Pi deployment.

---

## 6. Current data

4 seasons / 52 weeks / 6 owners. Entire league history is ~108 KB of JSON;
the page is ~36 KB. Growth is ~38 KB/season. Resources are not a constraint
on any Pi. `pandas`/`numpy` are the only heavy ARM dependencies and are used
**only** by `main.py` — a Pi that just serves the dashboard needs `requests`
alone (plus `firebase-admin` if keeping the Firestore fallback).

As of 2026-09-20 the live NFL state is season 2026 week 2, so week 1 is the
only completed 2026 week. There is nothing new to sync until the NFL rolls to
week 3.

---

## 7. Open items

1. **Port to the Raspberry Pi.** Full procedure in `DEPLOY.md`: nginx, weekly
   cron running `--json-out /var/www/leaguehq/data`, service account key moved
   to `~/.config/sleeperff/` (outside the repo), and **Cloudflare Tunnel** for
   outside access — league members are on other networks, and a tunnel needs
   no port forwarding and doesn't expose the home IP.
2. **Blog authoring — the last real gap.** Nothing writes blog posts, so that
   tab renders "No featured post yet." Decision needed: markdown files in a
   folder the pipeline bundles, vs. hand-writing documents in the Firebase
   console. Nothing else is blocked on this.
3. **Merge to `main`.** All work is on `fix/owner-id-keying-and-live-verification`,
   pushed to GitHub. `main` is untouched.
4. **Decide whether the Pi keeps writing Firestore.** Keeping both means a
   viewer still sees data when the Pi is down, at the cost of the service
   account key living on the Pi. `--skip-firestore` drops the key entirely.
5. **2027 and beyond:** add the league ID to `league_data.json`. Nothing else
   needs to change — season list, rule table, and week capping all follow.

---

## 8. Environment gotcha

Claude Code runs inside **Warp** (`dev.warp.Warp-Stable`). macOS TCC gates
`~/Documents`, and an OS or app update can silently revoke it, after which
every file operation fails with `Operation not permitted` even though the
files are fine. Fix: System Settings -> Privacy & Security -> Files and
Folders -> Warp -> enable Documents Folder, then fully quit (Cmd-Q) and
relaunch Warp. `claude --resume` returns to the session.
