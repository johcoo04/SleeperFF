# Fantasy League HQ — Current State (v4)

**This replaces the v3 handoff.** v3 was written as a handoff *into* a Claude
Code session, listing "run it against the real Sleeper API" as the open step.
That session has now happened: the API was reachable, both pipelines were run
for real against all three seasons, and the results were verified. This doc
describes where things actually stand, not what to do next.

Earlier handoffs (v1, v2, v3) are in `archive/` for history.

---

## 1. File layout (as it actually is on disk)

```
SleeperFF/
├── main.py                 # THE Excel pipeline. Corrected + verified against live data.
├── sync_pipeline.py        # Firestore pipeline. Corrected + verified via --dry-run; no Firebase project yet.
├── league_hq.html          # Read-only dashboard for sync_pipeline.py's Firestore data.
├── league_data.json        # Config source of truth (league IDs per season, base_url, weeks_to_fetch).
├── requirements.txt        # requests, pandas, openpyxl, matplotlib, numpy (+ firebase-admin for sync_pipeline.py)
├── fantasy_multi_year_scores_*.xlsx   # Generated output. Gitignored (*.xlsx).
├── 2 Types of Databases.sql           # Unrelated personal notes.
├── CHANGELOG.md / CONTRIBUTORS.md     # Stubs.
└── archive/                # Old buggy main.py + main2.py, handoffs v1-v3, pre-bugfix xlsx.
```

**Note on naming, because v3 got this backwards:** the corrected pipeline is
`main.py` at the root. The old buggy `main.py` *and* `main2.py` are both in
`archive/`. There is no `main2.py` at the root anymore. v3's instruction to
"replace `main2.py`, delete `main.py`" would have deleted the good file.

---

## 2. The three original fixes — now confirmed against real data

All three landed in both `main.py` and `sync_pipeline.py`.

### Fix 1 — Owner identity is `owner_id`, never a name
Sleeper returns **no `username` for any of the 6 owners** in any of the three
seasons (re-confirmed against the live API: 0 of 6, every season). The old
code's `user.get('username', 'Unknown')` defaulted everyone to the literal
string `"Unknown"` and then used it as a dict key, collapsing every owner into
one garbage row.

Every aggregation now keys off `owner_id` (Sleeper's internal `user_id`).
**Confirmed in the real output:** Owner Summary and Scoreboard Summary each
have 6 rows, and their grand total reconciles exactly against the Career sheet
(46,203.48 both ways).

This turned out to matter for a second reason v3 didn't know about: **one owner
renamed themselves between seasons** — `SillyG00SE69` in 2023, `SillyG00SE13`
in 2024-25 (owner_id `1004588088431058944`). Keying on `owner_id` correctly
merges all 51 weeks into one career record (2,656.16 + 5,091.50 = 7,747.66 PF).
Any name-based key would have forked them into two partial owners. Because of
this, **nothing may key on a display name anywhere** — see section 3.

### Fix 2 — Real tie-breaker averaging
`rank_week_with_ties()` does competition ranking (ties share a rank; the next
distinct score jumps by the tie count, e.g. 1, 2, 2, 4), and
`averaged_points_for_tied_group()` splits the average of what each tied rank
would individually pay.

**Status: correct but unexercised by real data.** There are zero tied weekly
scores across all 51 weeks of all three seasons, so this path never fires in
production. It is verified only by synthetic tests. The six-way tie at 0.0 in
2025 Week 16 that originally motivated this fix no longer exists — that week
now returns real scores (233.4 / 164.2 / 162.4 / 157.9 / 149.1 / 142.6); the
zeros were a mid-season snapshot artifact.

### Fix 3 — Live-week capping (currently dormant)
`determine_effective_max_week()` asks `/state/nfl` what week it is and, for the
season matching that live state only, treats weeks strictly before the current
one as complete. Past seasons are fetched in full. Network failure falls back
to the requested week count.

**Status: correct but dormant.** Live state is now **season 2026, week 2**, and
`league_data.json` only configures 2023-2025 — so no configured season matches
the live season and all three are fetched in full as past seasons. The capping
logic will start doing work again the moment a `"2026"` league ID is added to
`league_data.json`.

---

## 3. Owner identity rule (applies to all three files)

`owner_id` is the canonical identity. Display names are cosmetic and **must
never be used as a key, a lookup, or a join field** — an owner has already
renamed themselves once, and a rename must not fork their history or break the
match between a Firestore document and the owner it describes.

Where this is enforced:
- **`main.py`** — aggregates on `owner_key` (= `owner_id`). `Owner_Name` and
  `Team_Name` are display columns that follow the latest season seen, so
  Owner Summary and Scoreboard Summary show the same current name for a
  renamed owner.
- **`sync_pipeline.py`** — every document written to Firestore carries
  `owner_id`. Week results carry `owner_id` / `opponent_owner_id` (plus
  `display_name` / `opponent_display_name` for rendering), standings carry
  `owner_id`, career records are keyed by `owner_id`, and the H2H matrix keys
  are `{owner_id}_vs_{owner_id}`. No `username` field is written at all.
- **`league_hq.html`** — matches rows by `owner_id` (standings ↔ last-week
  result, H2H selector values, career lookups) and only ever *renders* the
  names, falling back to `owner_id` if a name is missing.

---

## 4. What's been verified, and how

**`main.py` — run for real against the live Sleeper API.** Current output:
`fantasy_multi_year_scores_20260917_213930.xlsx`. All 9 sheets present with
expected shapes: Career 306 rows, 2023/2024/2025 102 each, League Averages 51,
Week MinMax 51, Owner Summary 6, Scoreboard 306, Scoreboard Summary 6.
- Owner Summary / Scoreboard Summary: 6 rows, not the old 1-row collapse.
- Totals reconcile: Career sum 46,203.48 = Owner Summary sum 46,203.48.
- Zero rows with a 0.0 score; no all-zero weeks anywhere (no phantom weeks).
- Scoreboard Points per week match the rule tables exactly: 6/week in 2023-24
  (2+2+1+1+0+0 across 6 teams) and 7/week in 2025 (2+2+1+1+1+0).

**`sync_pipeline.py` — run for real in `--dry-run`** (computes everything,
writes nothing). 17 weeks and 6 teams in standings for each season. Its
per-season Scoreboard Points totals match `main.py`'s Excel output exactly
(jackcoon04 18/21/28, Gatorsby90 19/23/20, SillyG00SE 19/21/16, Doodlebahb
23/13/20, xSgtMelonx 16/16/15, gatorjoe15 7/8/20) — two independent
implementations agreeing on the scoring math.

Career/H2H documents were also built from live data and inspected directly:
6 owners keyed by numeric Sleeper IDs, all 6 with the full 51 weeks (no rename
fork), 30 directed H2H pairings with reciprocal rows mirroring correctly, and
51 league-trend rows.

**Not verified:**
- The tie-breaker path (no real ties exist — see Fix 2).
- Any actual Firestore write. `sync_pipeline.py` has only ever run `--dry-run`.
- `league_hq.html` rendering against live data, since nothing populates
  Firestore yet.

---

## 5. What's left (all optional)

Nothing blocks the Excel workflow — it works and has been run.

**If you want the live dashboard:**
1. Create a Firebase project, put its config into `firebaseConfig` in
   `league_hq.html` (currently `YOUR_API_KEY` placeholders).
2. Generate a service account key, save as `serviceAccountKey.json` (or set
   `FIREBASE_SERVICE_ACCOUNT` / pass `--credentials`).
3. Add `firebase-admin` to `requirements.txt` — it's a real dependency of
   `sync_pipeline.py` and still missing from the file.
4. Run `python sync_pipeline.py` (without `--dry-run`) to populate Firestore,
   then open `league_hq.html`.
5. Firestore rules should be read-only for the browser, write-only via the
   pipeline's service account.
6. The blog tab reads a `blogs` collection that nothing currently writes.

**Smaller loose ends:**
- Adding a `"2026"` league ID to `league_data.json` re-activates Fix 3 and is
  what you'd do to track the current season.
- `main.py` and `sync_pipeline.py` still contain two separate implementations
  of the same ranking/scoring rules. They currently agree exactly (verified
  above), but they'll drift. Porting one onto the other is the long-term fix.
- The `--full` flag on `sync_pipeline.py` is documented as mostly intent —
  every run already recomputes the whole season.
- `sync_pipeline.py` breaks out of its week loop when `compute_week_results`
  returns `None`, with a stale comment about Sleeper returning `[]` for
  unplayed weeks. Harmless given Fix 3, but the comment contradicts it.
