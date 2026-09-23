"""
Sleeper Fantasy League Sync Pipeline
=====================================
Fetches league data from the Sleeper API, computes the league's custom weekly
Scoreboard Points, rolling averages, all-time head-to-head records and outlier
stats, then upserts everything into Cloud Firestore for index.html to read
live via onSnapshot.

All identity and scoring logic lives in league_core.py — this file owns only
the Firestore document shaping and the write. See league_core.py for why owner
identity is `owner_id` and never a name.

Blog posts are markdown files in blogs/ (see blog_core.py) and ride along to
both sinks, so the Weekly Blog tab works whichever source the page picked.

Two sinks, either or both:
    Firestore  -- live push updates via onSnapshot, reachable from anywhere.
    Static JSON -- a single bundle the page can fetch(); no SDK, no keys.
The dashboard prefers the JSON bundle when it's present and falls back to
Firestore, so running both gives the Pi a local source with a cloud fallback.

Usage:
    python sync_pipeline.py                              # Firestore only
    python sync_pipeline.py --json-out ./data            # Firestore + JSON
    python sync_pipeline.py --json-out ./data --skip-firestore   # JSON only
    python sync_pipeline.py --season 2025                 # one season
    python sync_pipeline.py --dry-run                     # compute, write nothing
    python sync_pipeline.py --credentials path/to/key.json
    python sync_pipeline.py --blogs-dir ./blogs            # markdown blog posts

Requirements:
    pip install -r requirements.txt             # requests only; enough for
                                                # --json-out, --skip-firestore
                                                # and --dry-run
    pip install -r requirements-firestore.txt   # adds firebase-admin, needed
                                                # only for a real Firestore write

Setup:
    1. In the Firebase console: Project Settings -> Service Accounts ->
       Generate new private key. Save the JSON file.
    2. Either name it serviceAccountKey.json next to this script, set the
       FIREBASE_SERVICE_ACCOUNT environment variable to its path, or pass
       --credentials explicitly. All of those names are gitignored.
    3. Schedule this weekly (cron, GitHub Actions, etc.) — see the sample
       workflow comment at the bottom of this file.
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict

import blog_core
import league_core as core

DEFAULT_CREDENTIALS_PATH = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "serviceAccountKey.json")


# ---------------------------------------------------------------------------
# Season document  ->  Firestore seasons/{year}
# ---------------------------------------------------------------------------

def build_season_document(season, league_id, url_base=core.DEFAULT_BASE_URL,
                          requested_max=core.DEFAULT_MAX_WEEKS, verbose=False):
    """
    Returns the seasons/{year} document: standings plus every completed week.

    Accumulators are keyed by owner_id rather than roster_id — a roster slot
    is a per-season artifact, while owner_id is the identity the rest of the
    system (and the dashboard) joins on.
    """
    owner_map, weeks = core.fetch_season_weeks(
        season, league_id, url_base, requested_max, verbose=verbose)

    totals = defaultdict(lambda: {
        "total_scoreboard_points": 0.0,
        "total_points_for": 0.0,
        "rank_sum": 0,
        "weeks_played": 0,
        "display_name": None,
        "team_name": None,
    })

    weeks_doc = {}
    weeks_completed = 0
    for week_number in sorted(weeks):
        week_result = weeks[week_number]
        weeks_completed = week_number
        weeks_doc[str(week_number)] = {"week_number": week_number, **week_result}

        for entry in week_result["results"]:
            acc = totals[entry["owner_id"]]
            acc["total_scoreboard_points"] += entry["points_awarded"]
            acc["total_points_for"] += entry["weekly_score"]
            acc["rank_sum"] += entry["weekly_rank"]
            acc["weeks_played"] += 1
            # Latest week wins, so a mid-season rename shows the current name.
            acc["display_name"] = entry["display_name"]
            acc["team_name"] = entry["team_name"]

    standings = []
    for owner_id, acc in totals.items():
        weeks_played = max(acc["weeks_played"], 1)
        standings.append({
            "owner_id": owner_id,
            "display_name": acc["display_name"],
            "team_name": acc["team_name"],
            "total_scoreboard_points": round(acc["total_scoreboard_points"], 2),
            "total_points_for": round(acc["total_points_for"], 2),
            "rolling_average_score": round(acc["total_points_for"] / weeks_played, 2),
            "rolling_average_rank": round(acc["rank_sum"] / weeks_played, 2),
            "current_standing": 0,  # filled in below after sorting
        })

    # Standings are by Scoreboard Points, with total points-for as the
    # tie-break — head-to-head results deliberately do not affect standings.
    standings.sort(key=lambda s: (-s["total_scoreboard_points"], -s["total_points_for"]))
    for position, team in enumerate(standings, start=1):
        team["current_standing"] = position

    return {
        "year": str(season),
        "weeks_completed": weeks_completed,
        "standings": standings,
        "weeks": weeks_doc,
    }, owner_map


# ---------------------------------------------------------------------------
# Cross-season documents  ->  Firestore meta/career_summary, meta/league_trends
# ---------------------------------------------------------------------------

def build_career_and_trends(season_docs):
    """
    season_docs: {season_str: season_document}
    Returns (career_summary_doc, league_trends_rows).

    Career records key on owner_id, which is what makes a rename harmless:
    the owner who was SillyG00SE69 in 2023 and SillyG00SE13 in 2024-25
    accumulates as one 51-week career, displayed under their current name.
    """
    career = defaultdict(lambda: {
        "career_scoreboard_points": 0.0,
        "career_total_pf": 0.0,
        "weeks_played": 0,
        "display_name": None,
        "best_single_week": None,
        "worst_single_week": None,
        "largest_week_differential": None,
    })
    league_trends = []

    for season in sorted(season_docs):
        doc = season_docs[season]

        # Largest week-to-week swing is measured within a season only, so this
        # resets per season rather than bridging a 2024 week 17 -> 2025 week 1.
        prev_score_by_owner = {}

        for week_key in sorted(doc["weeks"], key=int):
            week = doc["weeks"][week_key]
            week_number = int(week_key)

            league_trends.append({
                "season": int(season),
                "week": week_number,
                "league_average": week["league_average"],
                "high_score": week["high_score"]["points"],
                "low_score": week["low_score"]["points"],
                "spread": week["spread"],
                # Who put up the high and the low. The numbers alone made the
                # trends table unreadable — a 237.54 means nothing without the
                # name attached. Carried on the row rather than looked up from
                # the season doc so the table works identically in Firestore
                # mode, where trends load as their own document.
                "high_score_owner": week["high_score"]["owner"],
                "low_score_owner": week["low_score"]["owner"],
            })

            for entry in week["results"]:
                owner_id = entry["owner_id"]
                if not owner_id:
                    continue
                record = career[owner_id]
                record["display_name"] = entry["display_name"]
                record["career_scoreboard_points"] += entry["points_awarded"]
                record["career_total_pf"] += entry["weekly_score"]
                record["weeks_played"] += 1

                score = entry["weekly_score"]
                if record["best_single_week"] is None or score > record["best_single_week"]["points"]:
                    record["best_single_week"] = {"points": score, "season": str(season), "week": week_number}
                if record["worst_single_week"] is None or score < record["worst_single_week"]["points"]:
                    record["worst_single_week"] = {"points": score, "season": str(season), "week": week_number}

                previous = prev_score_by_owner.get(owner_id)
                if previous is not None:
                    delta = round(score - previous["points"], 2)
                    current_largest = record["largest_week_differential"]
                    if current_largest is None or abs(delta) > abs(current_largest["delta"]):
                        record["largest_week_differential"] = {
                            "delta": delta,
                            "from_week": previous["week"],
                            "to_week": week_number,
                            "season": str(season),
                        }
                prev_score_by_owner[owner_id] = {"points": score, "week": week_number}

    # Rule-table counterfactuals and per-tier counts get their own pass: both
    # need a whole week at once (re-ranking the field), not one entry at a time.
    #
    # The league changed its payout table in 2025 — 2023-24 paid ranks 3-4 a
    # point and zeroed 5-6, while 2025+ pays 3-5 and zeroes only 6. That makes
    # raw career totals and raw zero counts non-comparable across eras: an
    # owner who was bad early collected zeros at twice the rate for the same
    # finishing position. Scoring every week in league history under each table
    # separately is the only way to compare an owner to themselves.
    tiers = defaultdict(lambda: {"two": 0, "one": 0, "zero": 0, "last": 0, "weeks": 0})
    counterfactual = defaultdict(lambda: {"old": 0.0, "new": 0.0})
    old_rules = core.points_for_rank_fn("2023")
    new_rules = core.points_for_rank_fn("2025")

    for season in sorted(season_docs):
        for week in season_docs[season]["weeks"].values():
            entries = [{"owner_id": e["owner_id"], "score": e["weekly_score"]}
                       for e in week["results"] if e["owner_id"]]
            if not entries:
                continue
            # Re-rank from the stored scores rather than trusting the stored
            # rank, so the two tables see an identical field and ties are
            # resolved by the same averaging rule in both.
            for scored, key in ((core.score_week(entries, old_rules), "old"),
                                (core.score_week(entries, new_rules), "new")):
                for entry in scored:
                    counterfactual[entry["owner_id"]][key] += entry["points_awarded"]

            field_size = len(entries)
            for entry in week["results"]:
                owner_id = entry["owner_id"]
                if not owner_id:
                    continue
                tier = tiers[owner_id]
                tier["weeks"] += 1
                awarded = entry["points_awarded"]
                if awarded == 2:
                    tier["two"] += 1
                elif awarded == 0:
                    tier["zero"] += 1
                elif awarded > 0:
                    tier["one"] += 1
                # Last place is rank == field size, not a hardcoded 6, and it
                # is the era-neutral companion to the zero count: exactly one
                # owner finishes last every week regardless of the payout table.
                if entry["weekly_rank"] == field_size:
                    tier["last"] += 1

    # Head-to-head gets its own pass: points_against needs each week's full
    # score-by-owner map, which isn't available while accumulating careers.
    h2h_matrix = defaultdict(lambda: {"wins": 0, "losses": 0, "ties": 0,
                                      "points_for": 0.0, "points_against": 0.0})
    for season in sorted(season_docs):
        for week in season_docs[season]["weeks"].values():
            score_by_owner = {e["owner_id"]: e["weekly_score"] for e in week["results"] if e["owner_id"]}
            for entry in week["results"]:
                owner_id = entry["owner_id"]
                opponent_id = entry["opponent_owner_id"]
                if not owner_id or not opponent_id or entry["h2h_win"] is None:
                    continue
                row = h2h_matrix[f"{owner_id}_vs_{opponent_id}"]
                if entry["weekly_score"] == score_by_owner.get(opponent_id):
                    row["ties"] += 1
                elif entry["h2h_win"]:
                    row["wins"] += 1
                else:
                    row["losses"] += 1
                row["points_for"] = round(row["points_for"] + entry["weekly_score"], 2)
                row["points_against"] = round(row["points_against"] + score_by_owner.get(opponent_id, 0), 2)

    owners = []
    for owner_id, record in career.items():
        weeks_played = max(record["weeks_played"], 1)
        owners.append({
            "owner_id": owner_id,
            "display_name": record["display_name"],
            "career_scoreboard_points": round(record["career_scoreboard_points"], 2),
            "career_total_pf": round(record["career_total_pf"], 2),
            "career_average_score": round(record["career_total_pf"] / weeks_played, 2),
            "weeks_played": record["weeks_played"],
            "best_single_week": record["best_single_week"],
            "worst_single_week": record["worst_single_week"],
            "largest_week_differential": record["largest_week_differential"],
            # Per-tier counts and both counterfactual totals. Rounded because
            # a tied week can pay a third of a point and float noise would
            # otherwise surface in the UI.
            "two_point_weeks": tiers[owner_id]["two"],
            "one_point_weeks": tiers[owner_id]["one"],
            "zero_point_weeks": tiers[owner_id]["zero"],
            "last_place_weeks": tiers[owner_id]["last"],
            "last_place_rate": round(
                100 * tiers[owner_id]["last"] / max(tiers[owner_id]["weeks"], 1), 1),
            "career_points_old_rules": round(counterfactual[owner_id]["old"], 2),
            "career_points_new_rules": round(counterfactual[owner_id]["new"], 2),
        })
    owners.sort(key=lambda o: -o["career_scoreboard_points"])

    league_trends.sort(key=lambda row: (row["season"], row["week"]))
    return {"owners": owners, "h2h_matrix": dict(h2h_matrix)}, league_trends


# ---------------------------------------------------------------------------
# Static JSON sink
# ---------------------------------------------------------------------------

def write_json_bundle(out_dir, season_docs, career_doc, league_trends, blogs=None):
    """
    Write the whole league as one ~113 KB file. A single bundle rather than a
    file per document means the page does one request instead of five, and
    can't render a half-updated league from a partially-fetched set.

    Written to a temp file and renamed, because rename is atomic on POSIX —
    a web server can never serve a half-written bundle mid-cron.
    """
    os.makedirs(out_dir, exist_ok=True)
    bundle = {
        "generated_at": int(time.time() * 1000),  # ms, so JS Date() takes it directly
        "seasons": {str(season): doc for season, doc in season_docs.items()},
        "career_summary": career_doc,
        "league_trends": league_trends,
        "blogs": blogs or [],
    }
    path = os.path.join(out_dir, "league.json")
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(bundle, f, separators=(",", ":"))
    os.replace(tmp, path)
    return path, os.path.getsize(path)


# ---------------------------------------------------------------------------
# Firestore
# ---------------------------------------------------------------------------

def init_firestore(credentials_path):
    """
    firebase_admin is imported here rather than at module scope so that
    --dry-run works in an environment that has no Firebase dependency.
    """
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
    except ImportError:
        print("ERROR: firebase-admin is not installed. Run: pip install -r requirements-firestore.txt")
        print("(or use --dry-run, which needs no Firebase dependency)")
        sys.exit(1)

    if not os.path.exists(credentials_path):
        print(f"ERROR: service account key not found at '{credentials_path}'.")
        print("Pass --credentials, set FIREBASE_SERVICE_ACCOUNT, or place "
              "serviceAccountKey.json next to this script.")
        sys.exit(1)

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(credentials_path))
    return firestore.client()


def push_documents(db, season_docs, career_doc, league_trends,
                   blogs=None, reconcile_blogs=False):
    """
    One batched write for everything, so the dashboard never observes a
    half-updated league (e.g. new standings against a stale career summary).

    `reconcile_blogs` deletes Firestore blog documents that no longer have a
    markdown file behind them. It is off unless a blogs directory actually
    exists: a Pi checked out without the folder must not interpret "I have no
    posts" as "delete every post", and console-authored posts predating the
    markdown pipeline stay put until the folder says otherwise.
    """
    batch = db.batch()
    for season, doc in season_docs.items():
        batch.set(db.collection("seasons").document(str(season)), doc, merge=True)
    batch.set(db.collection("meta").document("career_summary"), career_doc, merge=True)
    batch.set(db.collection("meta").document("league_trends"), {"rows": league_trends}, merge=True)

    blogs = blogs or []
    for post in blogs:
        # merge=False: the markdown file is the whole truth for a post, so a
        # field deleted from front matter must disappear rather than linger.
        batch.set(db.collection("blogs").document(post["id"]), post)

    removed = 0
    if reconcile_blogs:
        keep = {post["id"] for post in blogs}
        for ref in db.collection("blogs").list_documents():
            if ref.id not in keep:
                batch.delete(ref)
                removed += 1

    batch.commit()
    return len(blogs), removed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    config = core.load_config()
    all_league_ids = core.league_ids(config)
    url_base = core.base_url(config)
    requested_max = core.weeks_to_fetch(config)

    parser = argparse.ArgumentParser(description="Sync Sleeper league data into Firestore.")
    parser.add_argument("--season", choices=sorted(all_league_ids), help="Limit sync to a single season.")
    parser.add_argument("--credentials", default=DEFAULT_CREDENTIALS_PATH,
                        help="Path to the Firebase service account JSON key.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute everything and print a summary, but write nothing anywhere.")
    parser.add_argument("--json-out", metavar="DIR",
                        help="Also write a static league.json bundle into DIR "
                             "(e.g. the Pi's web root). The dashboard prefers this over Firestore.")
    parser.add_argument("--skip-firestore", action="store_true",
                        help="Don't write to Firestore. Use with --json-out for a "
                             "Firebase-free deployment.")
    parser.add_argument("--blogs-dir", default=blog_core.DEFAULT_BLOGS_DIR, metavar="DIR",
                        help="Folder of markdown blog posts (default: %(default)s). "
                             "A missing folder simply means no posts.")
    args = parser.parse_args()

    seasons_to_run = [args.season] if args.season else sorted(all_league_ids)
    # A single-season run can't rebuild the cross-season documents, since
    # they'd be recomputed from just this one season's weeks.
    partial_run = bool(args.season) and len(all_league_ids) > 1

    season_docs = {}
    for season in seasons_to_run:
        print(f"[{season}] fetching + computing...")
        doc, _ = build_season_document(season, all_league_ids[season], url_base,
                                       requested_max, verbose=True)
        season_docs[season] = doc
        print(f"[{season}] {doc['weeks_completed']} week(s) completed, "
              f"{len(doc['standings'])} team(s) in standings.")

    if partial_run:
        print("\nNOTE: career_summary and league_trends are rebuilt from only the "
              "season(s) fetched in THIS run, so this --season run would overwrite "
              "them with partial data. Skipping those two documents; run without "
              "--season for a full cross-season recompute.")

    career_doc, league_trends = build_career_and_trends(season_docs)

    # Blogs are independent of --season: they're files, not fetched data, so a
    # single-season run still carries the full set and can't publish a partial
    # archive the way career_summary would.
    print("Loading blog posts...")
    blogs = blog_core.load_posts(args.blogs_dir, verbose=True)
    have_blogs_dir = os.path.isdir(args.blogs_dir)

    if args.dry_run:
        print("\n--dry-run set: skipping Firestore writes.")
        for season in sorted(season_docs):
            print(f"\n=== {season} standings ===")
            for team in season_docs[season]["standings"]:
                print(f"  #{team['current_standing']} {team['display_name']:<20} "
                      f"{team['total_scoreboard_points']:>5} pts  "
                      f"PF {team['total_points_for']:>8.2f}  "
                      f"avg {team['rolling_average_score']:>6.2f}")
        if args.json_out:
            print(f"Would write a static bundle to {os.path.join(args.json_out, 'league.json')}.")
        featured = next((p["id"] for p in blogs if p["is_current"]), None)
        print(f"Would write {len(blogs)} blog post(s)"
              + (f", featuring '{featured}'." if featured else "."))
        if args.skip_firestore:
            print("Would skip Firestore (--skip-firestore).")
        if partial_run:
            print(f"\nWould write: {len(season_docs)} season doc(s) only "
                  f"(career_summary and league_trends skipped for a --season run).")
        else:
            print(f"\nWould write: {len(season_docs)} season doc(s), "
                  f"career_summary ({len(career_doc['owners'])} owners, "
                  f"{len(career_doc['h2h_matrix'])} h2h pairings), "
                  f"league_trends ({len(league_trends)} rows).")
        return

    if args.json_out:
        if partial_run:
            print("REFUSING to write a partial JSON bundle: it would replace the whole "
                  "league with one season. Run without --season to regenerate it.")
        else:
            path, size = write_json_bundle(args.json_out, season_docs, career_doc,
                                           league_trends, blogs=blogs)
            print(f"Wrote {path} ({size / 1024:.1f} KB).")

    if args.skip_firestore:
        print("Skipped Firestore (--skip-firestore).")
        print("\nSync complete.")
        return

    db = init_firestore(args.credentials)
    if partial_run:
        # Partial run: refresh just this season, leave the cross-season docs alone.
        batch = db.batch()
        for season, doc in season_docs.items():
            batch.set(db.collection("seasons").document(str(season)), doc, merge=True)
        batch.commit()
        print(f"[{args.season}] written to Firestore (career/trends/blogs left untouched).")
    else:
        written, removed = push_documents(db, season_docs, career_doc, league_trends,
                                          blogs=blogs, reconcile_blogs=have_blogs_dir)
        print(f"Wrote {len(season_docs)} season doc(s) + meta/career_summary + "
              f"meta/league_trends + {written} blog post(s) to Firestore"
              + (f" ({removed} stale post(s) deleted)." if removed else "."))
    print("\nSync complete.")


if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------------
# Sample GitHub Actions workflow (save as .github/workflows/sync.yml):
#
# name: Sleeper Sync
# on:
#   schedule:
#     - cron: "0 13 * * 2"   # every Tuesday 13:00 UTC
#   workflow_dispatch: {}
# jobs:
#   sync:
#     runs-on: ubuntu-latest
#     steps:
#       - uses: actions/checkout@v4
#       - uses: actions/setup-python@v5
#         with:
#           python-version: "3.11"
#       - run: pip install -r requirements-firestore.txt
#       - run: echo '${{ secrets.FIREBASE_SERVICE_ACCOUNT_JSON }}' > serviceAccountKey.json
#       - run: python sync_pipeline.py
#       - if: always()
#         run: rm -f serviceAccountKey.json
# ---------------------------------------------------------------------------
