# Fantasy League HQ — Handoff for Claude Code (v2, corrected)

**This replaces the previous HANDOFF.md.** That version was written assuming
a clean slate — it didn't know a real, already-working pipeline (`main.py` /
`main2.py`) existed in your `SleeperFF` repo. Having now seen the actual
repo and its real output, the picture is different in a few important ways,
including two confirmed bugs in the existing code. Read section 3 first —
it's the part that changes what you should actually do next.

---

## 1. What's really in the repo

```
SleeperFF/
├── main.py                                    # real, already-run pipeline -> Excel (has dead code, see below)
├── main2.py                                    # same pipeline, cleaner — no dead code (see below)
├── league_data.json                            # real config: league IDs, Sleeper base URL, settings
├── requirements.txt                            # requests, pandas, openpyxl, matplotlib, numpy — NO firebase-admin
├── fantasy_multi_year_scores_20251217_222523.xlsx   # real output from an actual run against the real API
├── README.md                                   # "scrape and call upon the Sleeper APIs ... perform Data Analysis"
├── CONTRIBUTORS.md / CHANGELOG.md / renovate.json   # standard repo scaffolding, nothing notable
├── 2 Types of Databases.sql                    # personal SQL study notes — unrelated to this project, not code
├── venv/, __pycache__/                         # confirms main.py/main2.py have actually been run locally
│
├── sync_pipeline.py                            # what I built from the Gemini spec — Firestore-based, UNRUN against real API
└── league_hq.html                              # frontend for sync_pipeline.py's Firestore output — also UNRUN in a real browser
```

**The important fact this reveals:** you have two separate, disconnected
systems that both talk to the same Sleeper league IDs but produce completely
different outputs and don't share any code:

| | `main.py` / `main2.py` | `sync_pipeline.py` |
|---|---|---|
| Output | Local `.xlsx` file | Firestore documents |
| Has actually run against the real API? | **Yes** (proof: the xlsx exists) | **No** |
| Has tie-breaker averaging? | **No** (bug — see 3.2) | Yes, tested |
| Has head-to-head tracking? | No | Yes, tested |
| Has the owner-collision bug? | **Yes** (bug — see 3.1) | No (guarded against it) |
| Anything reads its output? | You, manually, in Excel | `league_hq.html` (but nothing has ever written real data there) |

Nothing currently connects `sync_pipeline.py` to real data, and nothing
currently connects the real, working Sleeper-fetching logic in `main2.py` to
Firestore. `league_hq.html` will show an empty dashboard forever until one
of these gets resolved — that's the main open decision (see section 4).

---

## 2. What each file actually does

### `main.py` and `main2.py` — nearly identical, real, already-run
Both: fetch users/rosters/matchups from Sleeper for all three configured
seasons, build a roster→owner mapping, organize weekly scores, compute
season totals/rolling averages, assign weekly rank + Scoreboard Points
(matching the spec's point tiers), and export a 9-sheet Excel workbook
(`Career`, one sheet per season, `League Averages`, `Week MinMax`,
`Owner Summary`, `Scoreboard`, `Scoreboard Summary`).

`main.py` has ~90 lines of dead code: inside
`export_multi_year_excel_with_rolling`, after its `return filename`
statement, there's an orphaned docstring + function body (`"""Export weekly
scores to Excel format"""` and everything after it) that looks like the
leftover of an older, simpler single-purpose export function that lost its
`def` line during editing. It's unreachable — Python allows code after a
`return` to exist, it just never executes — so it's not currently breaking
anything, but it should be deleted. `main2.py` doesn't have this, which is
the main reason to treat `main2.py` as the more current of the two.

`league_data.json` holds the real league IDs (matching the spec exactly)
plus the Sleeper base URL and a `weeks_to_fetch: 17` setting — this is a
cleaner pattern than `sync_pipeline.py`'s hardcoded `LEAGUE_IDS` dict, and
worth adopting there instead.

### `sync_pipeline.py` — what I built from the Gemini spec, not yet run for real
Does the Sleeper fetch + Firestore write architecture the spec asked for.
Computation logic (ranking, tie-breaking, Scoreboard Points, career
aggregation, head-to-head) was unit-tested against hand-built synthetic
data in this chat — see the previous handoff for details — but has never
touched Sleeper's actual API or a real Firestore project.

**Just patched, in this pass:** it previously had the same "can't tell a
played week from an unplayed one" weakness described in 3.3 below. I added
`determine_effective_max_week()`, which asks Sleeper's `/state/nfl`
endpoint what week it currently is and, for whichever season matches that
live state, only treats weeks strictly before the current one as complete.
Past seasons are assumed fully played. Tested against four scenarios
(mid-season cap, past season unaffected, network failure fallback, zero
completed weeks) — all pass. This fix is **not yet in `main.py`/`main2.py`**.

### `league_hq.html` — untouched since last handoff
Still exactly as described before: dual-theme, four-tab dashboard, reads
Firestore via `onSnapshot`, writes nothing. Its logic was verified with a
jsdom harness against synthetic data shaped like `sync_pipeline.py`'s
output — never against real Firestore or a real browser.

---

## 3. Confirmed bugs, found by actually reading the real xlsx output

I opened `fantasy_multi_year_scores_20251217_222523.xlsx` and checked its
numbers against each other. Two real, currently-live bugs turned up.

### 3.1 — Owner Summary and Scoreboard Summary collapse all 7 owners into 1 row

The `Career` sheet (and every other sheet) correctly shows 7 distinct real
owners: `Doodlebahb`, `xSgtMelonx`, `gatorjoe15`, `Gatorsby90`, `jackcoon04`,
`SillyG00SE13`, `SillyG00SE69`. But `Owner Summary` and `Scoreboard Summary`
each contain exactly **one row**, labeled `Username: Unknown`, whose
`Career_Total_Points` (44379.5) is exactly the **sum of all 7 owners'
individual totals** (7437.40 + 7428.40 + 4863.96 + 2656.16 + 7092.66 +
7835.52 + 7065.40 = 44379.50 — verified in this chat).

**Root cause:** Sleeper's `/league/{id}/users` response for this league
doesn't include a `username` field for any of the 7 owners (only
`display_name` is populated). `get_team_names_mapping()`'s
`user.get('username', 'Unknown')` then defaults every single owner to the
literal string `"Unknown"`. Later, the Owner Summary and Scoreboard Summary
aggregation code keys its dictionaries by that value — since all 7 owners
share the exact same key (`"Unknown"`), they all accumulate into one
dictionary entry instead of seven.

`sync_pipeline.py` doesn't have this bug: its equivalent line falls back to
the Sleeper-internal `owner_id` (guaranteed unique per user) instead of a
shared literal string:
```python
"username": user.get("username") or owner_id or f"roster_{r['roster_id']}",
```
If you keep `main2.py` as the pipeline going forward, this is the one-line
fix: replace the `'Unknown'` default with `owner_id` (or `user['user_id']`),
everywhere `username` is read, including the two "try to find team_username
by re-scanning records" loops in the Scoreboard section.

### 3.2 — No tie-breaker averaging (confirmed with real data)

The spec's rule: teams tied at the same weekly score should split the
average of the point values their tied ranks would each earn. `main.py`/
`main2.py` don't implement this — they just sort by score and assign
sequential ranks, so identical scores get arbitrarily different points
depending on sort order.

Real example from the workbook — **Season 2025, Week 16**, all 6 teams
scored exactly `0.0` (see note in 3.3 on why):
```
Owner_Name     Weekly_Score  Weekly_Rank  Points_Awarded
Doodlebahb          0.0           1            2
SillyG00SE13        0.0           2            2
Gatorsby90          0.0           3            1
jackcoon04          0.0           4            1
xSgtMelonx          0.0           5            1
gatorjoe15          0.0           6            0
```
Per the spec's rule, six identically-scored teams should all share rank 1
and each receive the **average** of what ranks 1–6 pay under the 2025 table
(2, 2, 1, 1, 1, 0) → (2+2+1+1+1+0)/6 ≈ **1.17 points each** — not the six
different values shown above. `sync_pipeline.py` implements this correctly
and was tested against exactly this kind of cross-tier tie.

### 3.3 — Related: "is this week over?" detection is unreliable (affects both codebases)

The Week 16 tie above is six identical **zero** scores — a strong sign that
week hadn't actually been played yet when this file was generated (Dec 17,
2025), but Sleeper still returned a non-empty matchup list for it (with
placeholder 0-point entries), because the matchup structure exists as soon
as it's scheduled, before any game is played. `main.py`/`main2.py`'s
`matchup_response()` only checks `if data:` (is the list non-empty) to
decide whether to keep fetching further weeks — that check can't tell a
played week from a merely-scheduled one, so it can end up folding unplayed,
all-zero weeks into totals, rolling averages, and weeks-played counts.

**This affected `sync_pipeline.py` too**, in this exact same way, before
this pass — see the fix described in section 2 above
(`determine_effective_max_week`, tested, now in place). If you keep
`main2.py` going forward, it needs the equivalent fix — its own
`get_current_nfl_week()` function already attempts something similar (it
hits `/state/nfl` too) but its result isn't actually being used to cap
`matchup_response()`'s week range inside `fetch_season_data()`; it's
computed and passed as `max_week`, so it should be applied for the current
season, but the fallback `if year == 2025: return 2` (hardcoded to 2 weeks,
clearly a debugging leftover) suggests the live `/state/nfl` call was
failing at some point and silently falling back to a stale hardcoded
guess. Worth checking whether that live call actually works reliably before
trusting it.

---

## 4. The actual decision to make before continuing

Pick one:

**Option A — Converge on `main2.py`, add Firestore as a second output.**
Port `sync_pipeline.py`'s three fixes (owner-key collision, tie-breaker
averaging, live-week capping) into `main2.py`, then add `firebase-admin`
writes alongside (or instead of) the Excel export. Keeps the
already-proven-against-real-data fetching code as the foundation.

**Option B — Converge on `sync_pipeline.py`, retire `main.py`/`main2.py`.**
It already has all three fixes and the Firestore-writing architecture
`league_hq.html` expects. Downside: it still needs its first real run
against the actual Sleeper API (only synthetic-data-tested so far), and
you'd lose the Excel export unless it's added back in as an extra output
mode.

**Option C — Keep both, deliberately.** Excel export for personal
spreadsheet-style analysis, Firestore sync for the live dashboard, sharing
a common core computation module so the three bugs only need fixing once.
Most work up front, no duplicated logic long-term.

Whichever you pick, `requirements.txt` needs `firebase-admin` added if
Firestore is involved, and `league_data.json`'s config pattern is worth
reusing in `sync_pipeline.py` instead of its current hardcoded dict.

---

## 5. Everything else from the original handoff still applies

Firebase project setup, `serviceAccountKey.json`, Firestore security rules
(read-only for the browser, write-only via the pipeline's service account),
`.gitignore`, the GitHub Actions sample, and "run `--dry-run` against the
real league before ever writing" are all still accurate and still
outstanding — see the previous handoff's sections 4 and 5 for the full
list. Nothing in this pass changes those; it only corrects the assumption
that there was no existing pipeline to reconcile with.
