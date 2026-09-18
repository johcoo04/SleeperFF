# Fantasy League HQ — Handoff for Claude Code

This is where things stand after the initial build-and-test pass in this chat.
Two deliverables exist so far; nothing has been deployed or run against real
data yet. Use this doc to pick the project back up.

---

## 1. Current file layout

Right now you have two standalone files, not yet organized into a repo:

```
sync_pipeline.py     # backend: Sleeper API -> computed stats -> Firestore
league_hq.html        # frontend: reads Firestore, renders the dashboard
```

Recommended structure once this moves into an actual repo:

```
fantasy-league-hq/
├── sync_pipeline.py
├── requirements.txt              # NOT YET CREATED — see TODOs
├── serviceAccountKey.json        # NOT YET CREATED — you generate this, gitignored
├── .gitignore                    # NOT YET CREATED — must exclude serviceAccountKey.json
├── .github/
│   └── workflows/
│       └── sync.yml              # NOT YET CREATED — sample is commented at bottom of sync_pipeline.py
├── index.html                    # rename league_hq.html to this once it's the only index.html in its own repo
└── README.md                     # NOT YET CREATED
```

The frontend was named `league_hq.html` instead of `index.html` only because
this chat already has an unrelated project (a "Glizzy Tracker" app) using
`index.html` as its output filename. Once this lives in its own repo, rename
it back to `index.html` — nothing in the file itself assumes that name.

---

## 2. What each file does

### `sync_pipeline.py`
Backend, run on a schedule (weekly) or manually. Talks to the Sleeper API and
Firestore — never touched by the browser.

- Fetches, per configured season: league users, rosters, and matchups for
  weeks 1–17 (stops early once Sleeper returns an empty matchup list for a
  week that hasn't happened yet).
- Computes, per week: rank-by-score across the whole league (competition
  ranking — ties share a rank), Scoreboard Points per that season's rule
  table (2023/24 vs 2025+ have different tiers), the tie-breaker averaging
  rule, league average/high/low/spread, and head-to-head win/loss per
  matchup pairing.
- Rolls all of that up into: `seasons/{year}` documents (standings + a
  `weeks` map), `meta/career_summary` (all-time per-owner totals, best/worst
  single week, largest week-to-week swing, and a symmetric `h2h_matrix`
  keyed both `a_vs_b` and `b_vs_a`), and `meta/league_trends` (a flat,
  season+week-sorted array under a `rows` field, used for the trends chart).
- Writes via `firebase-admin`, `set(..., merge=True)`.
- CLI: `--season YEAR` to limit scope, `--dry-run` to compute and print
  without writing, `--credentials PATH` to point at a specific service
  account key. **`--full` is currently a documented no-op** — see TODOs.
- A sample GitHub Actions workflow is commented at the very bottom of the
  file (weekly cron + manual dispatch).

### `league_hq.html` (rename to `index.html` in its own repo)
Frontend. Entirely read-only — it never writes to Firestore, only
`onSnapshot`s three docs and one collection: `seasons/{2023,2024,2025}`,
`meta/career_summary`, `meta/league_trends`, and `blogs` (the whole
collection, sorted/filtered client-side).

- Two visual themes (Stadium Scoreboard / Modern Dashboard) toggled by
  swapping a body class; everything reads CSS custom properties so there's
  only one copy of the markup.
- Four tabs: **Scoreboard** (season selector, standings table, week browser
  with per-week results), **Career/Multi-Year** (all-time leaderboard, a
  Chart.js bar chart of career averages, an H2H owner-vs-owner selector, and
  an outlier-records grid), **League Trends** (a Chart.js line chart plus a
  full table), **Weekly Blog** (a featured post rendered from Markdown via
  `marked.js`, plus a filterable archive that opens posts in a modal).
- No build step — Tailwind, Chart.js, marked, and the Firebase v10 modular
  SDK are all loaded via CDN `<script>`/`import` tags.

---

## 3. What's been verified vs. not

**Verified in this chat**, without real network/Firestore access:
- `sync_pipeline.py`'s core computation functions (ranking, tie-breaking,
  Scoreboard Points, career aggregation, H2H) were unit-tested against
  hand-built synthetic matchup data with assertions on the exact expected
  output — including a tie that crosses a point-value tier boundary.
- `league_hq.html`'s actual script (Firebase imports mocked, everything else
  real) was run in a jsdom harness against synthetic Firestore snapshots
  shaped like the pipeline's real output. All render paths, the season/week
  selectors, H2H picker, blog modal, tab switching, and theme toggle were
  exercised and passed.

**Not yet verified — this is the real gap:**
- The pipeline has **never hit the actual Sleeper API**. The three league
  IDs in the spec haven't been fetched even once. Field names/shapes are
  based on Sleeper's documented API, but real leagues sometimes have edge
  cases (e.g. a bye week, a team that leaves mid-season, playoff bracket
  weeks with different matchup semantics) that synthetic data won't surface.
- Nothing has touched a real Firestore project. `firebaseConfig` in the HTML
  and the service account flow in the pipeline are both untested against
  live infrastructure.
- No browser has actually rendered the page — Tailwind/Chart.js/marked/
  Firebase CDN URLs were never reachable from this sandbox, so the jsdom
  test proves the JS logic works, not that the page looks right or that the
  real CDN scripts load and initialize as expected.

---

## 4. Outstanding TODOs, roughly in the order I'd tackle them

1. **Create the Firebase project** (or reuse an existing one) and drop the
   real `firebaseConfig` into `league_hq.html`.
2. **Generate `serviceAccountKey.json`** for the pipeline (Firebase Console →
   Project Settings → Service Accounts) and keep it out of version control.
3. **Decide Firestore security rules deliberately this time.** Unlike the
   Glizzy Tracker (a throwaway weekend app where `allow read, write: if true`
   was a reasonable tradeoff), this app has a real read/write split: the
   pipeline is the only writer, the browser is read-only. A sensible rule
   set:
   ```
   match /seasons/{year} { allow read: if true; allow write: if false; }
   match /meta/{doc}     { allow read: if true; allow write: if false; }
   match /blogs/{post}   { allow read: if true; allow write: if false; }
   ```
   All writes then only ever happen via the service-account-authenticated
   pipeline, which bypasses these rules entirely (admin SDK). This is worth
   deciding before you go live, not after.
4. **Run `python sync_pipeline.py --dry-run --season 2025`** against the
   real league ID first, and read the printed standings before ever writing
   to Firestore. This is where real-API edge cases will surface.
5. **Create `requirements.txt`** (`requests`, `firebase-admin`) and a
   `.gitignore` (must include `serviceAccountKey.json`).
6. **Wire up the GitHub Actions workflow** from the commented sample at the
   bottom of `sync_pipeline.py` — needs a repo secret holding the service
   account JSON.
7. **Populate at least one `blogs/{post_id}` document manually** (there's no
   UI for creating blog posts — the spec didn't ask for one, so posts are
   presumably written directly in the Firestore console or via a small
   separate script) to confirm the Blog tab renders real Markdown correctly.
8. **Decide what `--full` should actually do.** Right now every pipeline run
   already recomputes the entire season from scratch (simplest correct
   approach, since Sleeper doesn't expose cheap incremental diffs), so
   `--full` is a documented no-op flag. If you want a genuinely incremental
   mode later (e.g. skip re-fetching weeks that are already final), that's
   new logic, not a bug fix.
9. **First real browser load** of `league_hq.html` once Firebase is wired
   up — confirm Tailwind/Chart.js/marked all load from CDN as expected and
   the two themes look right side by side.
10. **Hosting** — same options as before (GitHub Pages is simplest if this
    ends up in a repo you already control).

---

## 5. Known design decisions worth a second look

- **H2H matrix is stored both directions** (`a_vs_b` and `b_vs_a`) to make
  the frontend's arbitrary-order dropdown lookup trivial. This roughly
  doubles that document's size; fine at this league's scale, worth
  revisiting only if the league gets much bigger.
- **Blog posts have no write path in this build.** The spec's schema defines
  the `blogs/{post_id}` shape but doesn't ask for an authoring UI, so none
  exists. If you want the commissioner to write posts from the dashboard
  itself rather than the Firestore console, that's a new feature, not
  something partially built.
- **Career/trends only reflect whatever season(s) were included in the most
  recent pipeline run.** Running with `--season 2025` only refreshes 2025's
  standings but still recomputes career/trends from just that run's data —
  the pipeline prints a warning about this, but it means a habit of always
  running without `--season` (or running all three explicitly) matters for
  data correctness.
