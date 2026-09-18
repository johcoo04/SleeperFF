"""
Sleeper Fantasy League Sync Pipeline
=====================================
Fetches league data from the Sleeper API, computes the league's custom
weekly Scoreboard Points, rolling averages, all-time head-to-head records,
and outlier stats, then upserts everything into Cloud Firestore for the
index.html frontend to read live via onSnapshot.

Usage:
    python sync_pipeline.py                    # sync all configured seasons (current week only)
    python sync_pipeline.py --full             # recompute every week from scratch, all seasons
    python sync_pipeline.py --season 2025      # limit to one season
    python sync_pipeline.py --season 2025 --full
    python sync_pipeline.py --credentials path/to/key.json

Requirements:
    pip install requests firebase-admin

Setup:
    1. In the Firebase console: Project Settings -> Service Accounts ->
       Generate new private key. Save the JSON file.
    2. Either name it serviceAccountKey.json next to this script, set the
       FIREBASE_SERVICE_ACCOUNT environment variable to its path, or pass
       --credentials explicitly.
    3. Schedule this to run weekly (cron, GitHub Actions, etc.) — see the
       sample workflow comment near the bottom of this file.
"""

import argparse
import os
import sys
import time
from collections import defaultdict

import requests
import firebase_admin
from firebase_admin import credentials, firestore

SLEEPER_BASE = "https://api.sleeper.app/v1"

LEAGUE_IDS = {
    "2023": "1004576507286630400",
    "2024": "1124846364065288192",
    "2025": "1257070625080483840",
}

MAX_WEEKS = 17
DEFAULT_CREDENTIALS_PATH = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "serviceAccountKey.json")


# ---------------------------------------------------------------------------
# Scoreboard Points rules
# ---------------------------------------------------------------------------

def points_for_rank_fn(season: str):
    """Returns a function mapping weekly rank -> Scoreboard Points, per season rules."""
    if season in ("2023", "2024"):
        def points_for_rank(rank):
            if rank <= 2:
                return 2
            if rank <= 4:
                return 1
            return 0
        return points_for_rank

    def points_for_rank_2025(rank):
        if rank <= 2:
            return 2
        if rank <= 5:
            return 1
        return 0
    return points_for_rank_2025


# ---------------------------------------------------------------------------
# Sleeper API
# ---------------------------------------------------------------------------

def sleeper_get(path, retries=3, backoff=1.5):
    url = f"{SLEEPER_BASE}{path}"
    last_exc = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=15)
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(backoff * (attempt + 1))
            continue
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            time.sleep(backoff * (attempt + 1))
            continue
        resp.raise_for_status()
    if last_exc:
        raise last_exc
    raise RuntimeError(f"Failed to fetch {url} after {retries} attempts")


def fetch_league_users(league_id):
    return sleeper_get(f"/league/{league_id}/users") or []


def fetch_league_rosters(league_id):
    return sleeper_get(f"/league/{league_id}/rosters") or []


def fetch_league_meta(league_id):
    return sleeper_get(f"/league/{league_id}") or {}


def fetch_nfl_state():
    return sleeper_get("/state/nfl") or {}


def determine_effective_max_week(season: str, requested_max: int = MAX_WEEKS):
    """
    Sleeper returns a non-empty matchup list — with every team at 0 points —
    for weeks that are scheduled but haven't been played yet, so simply
    checking "is the response non-empty" can't tell a played week from an
    upcoming one. Instead, ask Sleeper what week it currently thinks it is
    (/state/nfl) and, for the season matching that live state, only treat
    weeks strictly before the current one as complete. Past seasons (not
    matching the live current season) are assumed fully played out.
    """
    try:
        state = fetch_nfl_state()
    except requests.RequestException:
        return requested_max  # can't reach NFL state — fall back to the caller's requested cap

    current_season = state.get("season")
    current_week = state.get("week")
    if current_season is not None and str(season) == str(current_season) and isinstance(current_week, int):
        return max(0, min(requested_max, current_week - 1))
    return requested_max


def fetch_week_matchups(league_id, week):
    return sleeper_get(f"/league/{league_id}/matchups/{week}") or []


def build_roster_owner_map(users, rosters):
    """roster_id -> {owner_id, display_name, team_name}

    owner_id (Sleeper's internal user_id) is the canonical identity that every
    Firestore document keys off. Nothing downstream may key on a name: this
    league returns no `username` at all for any owner, and display names do
    change between seasons — one owner has already renamed themselves, which
    would fork their career history into two partial owners. Names below are
    display-only.
    """
    user_by_id = {u["user_id"]: u for u in users}
    mapping = {}
    for r in rosters:
        owner_id = r.get("owner_id")
        user = user_by_id.get(owner_id, {}) or {}
        metadata = user.get("metadata") or {}
        team_name = metadata.get("team_name") or user.get("display_name") or f"Roster {r['roster_id']}"
        mapping[r["roster_id"]] = {
            "owner_id": owner_id or f"roster_{r['roster_id']}",
            "display_name": user.get("display_name") or team_name,
            "team_name": team_name,
        }
    return mapping


# ---------------------------------------------------------------------------
# Weekly computation
# ---------------------------------------------------------------------------

def compute_week_results(matchups, roster_owner_map, points_for_rank):
    """
    Ranks every roster by weekly score across the WHOLE league (competition
    ranking: ties share a rank, e.g. 1-2-2-4), assigns Scoreboard Points off
    that rank (averaging points across tied ranks per the tie-breaker rule),
    and groups by matchup_id to record head-to-head outcomes.
    """
    if not matchups:
        return None

    by_roster = {m["roster_id"]: m for m in matchups}
    ranked = sorted(by_roster.values(), key=lambda m: m.get("points", 0) or 0, reverse=True)

    ranks = {}
    current_rank = 0
    prev_score = None
    seen = 0
    for m in ranked:
        seen += 1
        score = m.get("points", 0) or 0
        if score != prev_score:
            current_rank = seen
            prev_score = score
        ranks[m["roster_id"]] = current_rank

    rank_counts = defaultdict(int)
    for r in ranks.values():
        rank_counts[r] += 1

    def averaged_points(rank, count):
        # Tied ranks (e.g. two teams both rank 2, pushing the next team to rank 4)
        # split the average of the point values those ranks would each earn.
        values = [points_for_rank(rank + i) for i in range(count)]
        return round(sum(values) / len(values), 2)

    groups = defaultdict(list)
    for m in matchups:
        groups[m.get("matchup_id")].append(m)

    opponent_of = {}
    for group in groups.values():
        if len(group) == 2:
            a, b = group
            opponent_of[a["roster_id"]] = b["roster_id"]
            opponent_of[b["roster_id"]] = a["roster_id"]
        # Byes or >2-team groups (non-standard leagues) are left without an opponent.

    scores = [m.get("points", 0) or 0 for m in matchups]
    league_average = sum(scores) / len(scores) if scores else 0
    high = max(by_roster.values(), key=lambda m: m.get("points", 0) or 0)
    low = min(by_roster.values(), key=lambda m: m.get("points", 0) or 0)

    def owner_label(roster_id):
        return roster_owner_map.get(roster_id, {}).get("display_name", f"Roster {roster_id}")

    results = []
    for m in matchups:
        rid = m["roster_id"]
        rank = ranks[rid]
        awarded = averaged_points(rank, rank_counts[rank])
        opp_rid = opponent_of.get(rid)
        opp_info = roster_owner_map.get(opp_rid, {}) if opp_rid is not None else {}
        h2h_win = None
        if opp_rid is not None:
            h2h_win = (m.get("points", 0) or 0) > (by_roster[opp_rid].get("points", 0) or 0)
        owner_info = roster_owner_map.get(rid, {})
        results.append({
            "owner_id": owner_info.get("owner_id"),
            "display_name": owner_info.get("display_name"),
            "team_name": owner_info.get("team_name"),
            "weekly_score": round(m.get("points", 0) or 0, 2),
            "weekly_rank": rank,
            "points_awarded": awarded,
            "opponent_owner_id": opp_info.get("owner_id"),
            "opponent_display_name": opp_info.get("display_name"),
            "h2h_win": h2h_win,
        })

    return {
        "league_average": round(league_average, 2),
        "high_score": {
            "owner": owner_label(high["roster_id"]),
            "team": roster_owner_map.get(high["roster_id"], {}).get("team_name"),
            "points": round(high.get("points", 0) or 0, 2),
        },
        "low_score": {
            "owner": owner_label(low["roster_id"]),
            "team": roster_owner_map.get(low["roster_id"], {}).get("team_name"),
            "points": round(low.get("points", 0) or 0, 2),
        },
        "spread": round((high.get("points", 0) or 0) - (low.get("points", 0) or 0), 2),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Season aggregation (standings, rolling averages, H2H, outliers)
# ---------------------------------------------------------------------------

def build_season_document(season: str, league_id: str, max_weeks: int = MAX_WEEKS):
    users = fetch_league_users(league_id)
    rosters = fetch_league_rosters(league_id)
    roster_owner_map = build_roster_owner_map(users, rosters)
    points_for_rank = points_for_rank_fn(season)

    weeks_doc = {}
    weeks_completed = 0
    effective_max_weeks = determine_effective_max_week(season, max_weeks)

    # Running per-roster accumulators for standings + rolling averages.
    totals = defaultdict(lambda: {
        "total_scoreboard_points": 0.0,
        "total_points_for": 0.0,
        "rank_sum": 0,
        "weeks_played": 0,
    })

    for week in range(1, effective_max_weeks + 1):
        matchups = fetch_week_matchups(league_id, week)
        week_result = compute_week_results(matchups, roster_owner_map, points_for_rank)
        if week_result is None:
            break  # Sleeper returns [] for weeks that haven't happened yet.

        weeks_completed = week
        weeks_doc[str(week)] = {"week_number": week, **week_result}

        for entry, m in zip(week_result["results"], matchups):
            rid = m["roster_id"]
            acc = totals[rid]
            acc["total_scoreboard_points"] += entry["points_awarded"]
            acc["total_points_for"] += entry["weekly_score"]
            acc["rank_sum"] += entry["weekly_rank"]
            acc["weeks_played"] += 1

    standings = []
    for rid, acc in totals.items():
        owner = roster_owner_map.get(rid, {})
        weeks_played = max(acc["weeks_played"], 1)
        standings.append({
            "owner_id": owner.get("owner_id"),
            "display_name": owner.get("display_name"),
            "team_name": owner.get("team_name"),
            "total_scoreboard_points": round(acc["total_scoreboard_points"], 2),
            "total_points_for": round(acc["total_points_for"], 2),
            "rolling_average_score": round(acc["total_points_for"] / weeks_played, 2),
            "rolling_average_rank": round(acc["rank_sum"] / weeks_played, 2),
            "current_standing": 0,  # filled in below after sorting
        })

    standings.sort(key=lambda s: (-s["total_scoreboard_points"], -s["total_points_for"]))
    for i, s in enumerate(standings, start=1):
        s["current_standing"] = i

    return {
        "year": season,
        "weeks_completed": weeks_completed,
        "standings": standings,
        "weeks": weeks_doc,
    }, roster_owner_map


# ---------------------------------------------------------------------------
# Cross-season aggregation (career summary + league trends)
# ---------------------------------------------------------------------------

def build_career_and_trends(season_docs: dict):
    """
    season_docs: {season_str: season_document_dict}
    Returns (career_summary_doc, league_trends_list)
    """
    career = defaultdict(lambda: {
        "career_scoreboard_points": 0.0,
        "career_total_pf": 0.0,
        "weeks_played": 0,
        "display_name": None,
        "best_single_week": None,
        "worst_single_week": None,
        "largest_week_differential": None,
        "_prev_score_by_season": {},
    })
    league_trends = []

    for season, doc in season_docs.items():
        # Map owner_id -> display_name for this season's rosters, for readability.
        display_names = {s["owner_id"]: s["display_name"] for s in doc["standings"]}

        prev_score_by_owner = {}

        for week_key in sorted(doc["weeks"].keys(), key=int):
            week = doc["weeks"][week_key]
            league_trends.append({
                "season": int(season),
                "week": int(week_key),
                "league_average": week["league_average"],
                "high_score": week["high_score"]["points"],
                "low_score": week["low_score"]["points"],
                "spread": week["spread"],
            })

            for entry in week["results"]:
                owner_id = entry["owner_id"]
                if owner_id is None:
                    continue
                c = career[owner_id]
                # Latest season processed wins, so a renamed owner shows their
                # current name while still accumulating under one owner_id.
                c["display_name"] = display_names.get(owner_id, owner_id)
                c["career_scoreboard_points"] += entry["points_awarded"]
                c["career_total_pf"] += entry["weekly_score"]
                c["weeks_played"] += 1

                score = entry["weekly_score"]
                if c["best_single_week"] is None or score > c["best_single_week"]["points"]:
                    c["best_single_week"] = {"points": score, "season": season, "week": int(week_key)}
                if c["worst_single_week"] is None or score < c["worst_single_week"]["points"]:
                    c["worst_single_week"] = {"points": score, "season": season, "week": int(week_key)}

                prev = prev_score_by_owner.get(owner_id)
                if prev is not None:
                    delta = round(score - prev["points"], 2)
                    current_best = c["largest_week_differential"]
                    if current_best is None or abs(delta) > abs(current_best["delta"]):
                        c["largest_week_differential"] = {
                            "delta": delta,
                            "from_week": prev["week"],
                            "to_week": int(week_key),
                            "season": season,
                        }
                prev_score_by_owner[owner_id] = {"points": score, "week": int(week_key)}

    # Head-to-head is computed in a dedicated pass below (needs each week's full
    # score-by-user map to fill in points_against correctly), separate from the
    # per-user career accumulation above.

    # Second pass to fill points_against now that all scores are known per week/season.
    # (Simplest correct approach: rebuild from week results directly.)
    h2h_matrix = defaultdict(lambda: {"wins": 0, "losses": 0, "ties": 0, "points_for": 0.0, "points_against": 0.0})
    for season, doc in season_docs.items():
        for week in doc["weeks"].values():
            score_by_owner = {e["owner_id"]: e["weekly_score"] for e in week["results"] if e["owner_id"]}
            for entry in week["results"]:
                owner_id = entry["owner_id"]
                opp = entry["opponent_owner_id"]
                if not owner_id or not opp or entry["h2h_win"] is None:
                    continue
                key = f"{owner_id}_vs_{opp}"
                row = h2h_matrix[key]
                if entry["weekly_score"] == score_by_owner.get(opp):
                    row["ties"] += 1
                elif entry["h2h_win"]:
                    row["wins"] += 1
                else:
                    row["losses"] += 1
                row["points_for"] = round(row["points_for"] + entry["weekly_score"], 2)
                row["points_against"] = round(row["points_against"] + score_by_owner.get(opp, 0), 2)

    owners = []
    for owner_id, c in career.items():
        weeks_played = max(c["weeks_played"], 1)
        owners.append({
            "owner_id": owner_id,
            "display_name": c["display_name"],
            "career_scoreboard_points": round(c["career_scoreboard_points"], 2),
            "career_total_pf": round(c["career_total_pf"], 2),
            "career_average_score": round(c["career_total_pf"] / weeks_played, 2),
            "weeks_played": c["weeks_played"],
            "best_single_week": c["best_single_week"],
            "worst_single_week": c["worst_single_week"],
            "largest_week_differential": c["largest_week_differential"],
        })
    owners.sort(key=lambda o: -o["career_scoreboard_points"])

    career_doc = {"owners": owners, "h2h_matrix": dict(h2h_matrix)}
    league_trends.sort(key=lambda r: (r["season"], r["week"]))
    return career_doc, league_trends


# ---------------------------------------------------------------------------
# Firestore
# ---------------------------------------------------------------------------

def init_firestore(credentials_path: str):
    if not os.path.exists(credentials_path):
        print(f"ERROR: service account key not found at '{credentials_path}'.")
        print("Pass --credentials, set FIREBASE_SERVICE_ACCOUNT, or place serviceAccountKey.json next to this script.")
        sys.exit(1)
    cred = credentials.Certificate(credentials_path)
    firebase_admin.initialize_app(cred)
    return firestore.client()


def push_season_document(db, season: str, doc: dict):
    db.collection("seasons").document(season).set(doc, merge=True)


def push_meta_documents(db, career_doc: dict, league_trends: list):
    db.collection("meta").document("career_summary").set(career_doc, merge=True)
    db.collection("meta").document("league_trends").set({"rows": league_trends}, merge=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sync Sleeper league data into Firestore.")
    parser.add_argument("--season", choices=list(LEAGUE_IDS.keys()), help="Limit sync to a single season.")
    parser.add_argument("--full", action="store_true",
                         help="Recompute every week from scratch (default already recomputes "
                              "the whole season each run — Sleeper doesn't expose incremental "
                              "diffs cheaply, so --full mainly documents intent for now).")
    parser.add_argument("--credentials", default=DEFAULT_CREDENTIALS_PATH,
                         help="Path to the Firebase service account JSON key.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Compute everything and print a summary, but don't write to Firestore.")
    args = parser.parse_args()

    seasons_to_run = [args.season] if args.season else list(LEAGUE_IDS.keys())

    season_docs = {}
    for season in seasons_to_run:
        league_id = LEAGUE_IDS[season]
        print(f"[{season}] fetching + computing...")
        doc, _ = build_season_document(season, league_id)
        season_docs[season] = doc
        print(f"[{season}] {doc['weeks_completed']} week(s) completed, "
              f"{len(doc['standings'])} team(s) in standings.")

    # Career summary + league trends are computed across ALL seasons on file,
    # even if this run only refreshed one — re-fetch the ones not in this run
    # from Firestore-less local memory isn't possible here, so a full run
    # (no --season filter) is required for career/trends to be fully accurate.
    if args.season and len(LEAGUE_IDS) > 1:
        print("NOTE: --season limits which season's standings get refreshed, but "
              "career_summary/league_trends are only recomputed from the season(s) "
              "run in THIS invocation. Run without --season periodically for a full "
              "cross-season recompute.")

    career_doc, league_trends = build_career_and_trends(season_docs)

    if args.dry_run:
        print("\n--dry-run set: skipping Firestore writes.")
        for season, doc in season_docs.items():
            print(f"\n=== {season} standings ===")
            for s in doc["standings"]:
                print(f"  #{s['current_standing']} {s['display_name']:<20} "
                      f"{s['total_scoreboard_points']:>5} pts  "
                      f"PF {s['total_points_for']:>8.2f}  "
                      f"avg {s['rolling_average_score']:>6.2f}")
        return

    db = init_firestore(args.credentials)
    for season, doc in season_docs.items():
        push_season_document(db, season, doc)
        print(f"[{season}] written to Firestore.")

    push_meta_documents(db, career_doc, league_trends)
    print("meta/career_summary and meta/league_trends written to Firestore.")
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
#       - run: pip install requests firebase-admin
#       - run: python sync_pipeline.py
#         env:
#           FIREBASE_SERVICE_ACCOUNT: ${{ github.workspace }}/serviceAccountKey.json
#         # Write the secret out to that path in a prior step, e.g.:
#         # - run: echo '${{ secrets.FIREBASE_SERVICE_ACCOUNT_JSON }}' > serviceAccountKey.json
# ---------------------------------------------------------------------------
