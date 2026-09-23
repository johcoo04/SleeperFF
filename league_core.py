"""
league_core.py — the single source of truth for league identity and scoring.
===========================================================================

Everything in here was verified against the live Sleeper API (all three
seasons, 51 weeks, 6 owners). Two entry points consume it:

    main.py           -> 9-sheet Excel workbook (archive / offline analysis)
    sync_pipeline.py  -> Cloud Firestore documents (live dashboard)

Those two files own their *output shaping* only. Anything that answers
"who is this owner?" or "how is a week scored?" lives here and nowhere
else, so a fix lands once instead of twice.

Three hard-won rules encoded below, each from a real bug:

1. Owner identity is `owner_id` (Sleeper's internal user_id) and never a
   name. This league returns no `username` for any of its 6 owners, so a
   name-based key collapsed all of them into one record; and one owner has
   renamed themselves between seasons (SillyG00SE69 -> SillyG00SE13), which
   would fork their career history in two. Names are display-only.

2. Ties use competition ranking (tied scores share a rank; the next
   distinct score jumps by the tie count, e.g. 1, 2, 2, 4) and a tied group
   splits the average of what each of its ranks would individually pay.

3. "Is this week over?" cannot be answered by checking whether Sleeper
   returned a non-empty matchup list — it returns a full list of zeros for
   scheduled-but-unplayed weeks. Ask /state/nfl instead.
"""

import json
import time
from collections import Counter, defaultdict

import requests

DEFAULT_CONFIG_PATH = "league_data.json"
DEFAULT_BASE_URL = "https://api.sleeper.app/v1"
DEFAULT_MAX_WEEKS = 17


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path=DEFAULT_CONFIG_PATH):
    """
    league_data.json is the only place league IDs live. Previously main.py
    read them from here while sync_pipeline.py had its own hardcoded copy —
    two lists to keep in sync, so adding a season could silently update one
    pipeline and not the other.
    """
    with open(path, "r") as f:
        return json.load(f)


def league_ids(config):
    """{season_str: league_id_str}"""
    return {str(year): str(lid) for year, lid in (config.get("league_ids") or {}).items()}


def base_url(config):
    return (config.get("api") or {}).get("base_url") or DEFAULT_BASE_URL


def weeks_to_fetch(config):
    return (config.get("settings") or {}).get("weeks_to_fetch", DEFAULT_MAX_WEEKS)


def owner_names(config):
    """
    {owner_id: preferred display name}

    Sleeper handles (Gatorsby90, SillyG00SE13, ...) are not what anyone calls
    each other, and the blog has always used first names. This maps one to the
    other for every output at once.

    Keyed on owner_id like everything else — see rule 1. Keying the override
    on the Sleeper name would break for the owner who renamed themselves, and
    would need a second entry to cover both spellings of one person.
    """
    return {str(k): v for k, v in (config.get("owner_names") or {}).items()}


# ---------------------------------------------------------------------------
# Sleeper API
# ---------------------------------------------------------------------------

def sleeper_get(path, url_base=DEFAULT_BASE_URL, retries=3, backoff=1.5):
    """
    Retries on transport errors and 429s. Backfilling three seasons is ~54
    requests in a tight loop, which is where rate limiting actually shows up.
    """
    url = f"{url_base}{path}"
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


def fetch_users(league_id, url_base=DEFAULT_BASE_URL):
    return sleeper_get(f"/league/{league_id}/users", url_base) or []


def fetch_rosters(league_id, url_base=DEFAULT_BASE_URL):
    return sleeper_get(f"/league/{league_id}/rosters", url_base) or []


def fetch_week_matchups(league_id, week, url_base=DEFAULT_BASE_URL):
    return sleeper_get(f"/league/{league_id}/matchups/{week}", url_base) or []


def fetch_nfl_state(url_base=DEFAULT_BASE_URL):
    return sleeper_get("/state/nfl", url_base) or {}


def determine_effective_max_week(season, requested_max=DEFAULT_MAX_WEEKS,
                                 url_base=DEFAULT_BASE_URL, verbose=False):
    """
    Rule 3. Only the season matching Sleeper's live current season gets capped
    to "weeks strictly before the current week". Past seasons are assumed
    fully played, and an unreachable /state/nfl falls back to requested_max
    rather than guessing.

    Note this is dormant whenever no configured season matches the live one
    (e.g. live state is 2026 while league_data.json lists 2023-2025) — every
    season is then treated as past and fetched in full, which is correct.
    """
    try:
        state = fetch_nfl_state(url_base)
    except (requests.RequestException, RuntimeError) as exc:
        if verbose:
            print(f"   Could not reach /state/nfl ({exc}) — using requested max of {requested_max}.")
        return requested_max

    current_season = state.get("season")
    current_week = state.get("week")
    if current_season is not None and str(season) == str(current_season) and isinstance(current_week, int):
        capped = max(0, min(requested_max, current_week - 1))
        if verbose:
            print(f"   Live NFL state says season {current_season} is on week {current_week} "
                  f"-> treating weeks 1-{capped} as complete for {season}.")
        return capped
    return requested_max


# ---------------------------------------------------------------------------
# Identity — rule 1
# ---------------------------------------------------------------------------

def build_owner_map(users, rosters, name_overrides=None):
    """
    roster_id -> {owner_id, display_name, team_name}

    `owner_id` is the canonical key for every aggregation and every stored
    document. `display_name` / `team_name` are for rendering only and must
    never be used as keys or join fields — see rule 1 in the module docstring.

    `name_overrides` ({owner_id: name}) replaces the Sleeper handle with the
    name the league actually uses. Applied here rather than in each entry
    point so the Excel workbook, the Firestore documents and the static bundle
    can't drift into showing different names for the same person.
    """
    name_overrides = name_overrides or {}
    user_by_id = {u["user_id"]: u for u in users}
    mapping = {}
    for roster in rosters:
        roster_id = roster["roster_id"]
        owner_id = roster.get("owner_id")
        user = user_by_id.get(owner_id) or {}
        metadata = user.get("metadata") or {}

        # Sleeper omits `username` entirely for this league, so display_name
        # is the best available human label; fall back through team name.
        display_name = user.get("display_name") or metadata.get("team_name") or f"Roster {roster_id}"
        team_name = metadata.get("team_name") or user.get("display_name") or f"Team {roster_id}"

        resolved_id = owner_id or f"roster_{roster_id}"
        mapping[roster_id] = {
            # A roster with no owner (rare: abandoned team) still needs a
            # stable unique key, so fall back to the roster slot itself.
            "owner_id": resolved_id,
            # An override wins over whatever Sleeper reports, including the
            # fallbacks above; team_name is left alone, since it's the team's
            # name and not the person's.
            "display_name": name_overrides.get(resolved_id, display_name),
            "team_name": team_name,
        }
    return mapping


# ---------------------------------------------------------------------------
# Scoring — rule 2
# ---------------------------------------------------------------------------

def points_for_rank_fn(season):
    """
    rank -> Scoreboard Points, per the league's rule tables.

    2023-2024 (6 teams): ranks 1-2 = 2, ranks 3-4 = 1, ranks 5-6 = 0
    2025+     (6 teams): ranks 1-2 = 2, ranks 3-5 = 1, rank 6    = 0
    """
    if str(season) in ("2023", "2024"):
        def points_for_rank(rank):
            if rank <= 2:
                return 2
            if rank <= 4:
                return 1
            return 0
        return points_for_rank

    def points_for_rank_2025_plus(rank):
        if rank <= 2:
            return 2
        if rank <= 5:
            return 1
        return 0
    return points_for_rank_2025_plus


def rank_week_with_ties(entries):
    """
    entries: list of dicts each carrying a 'score'. Returns a new list of
    shallow copies, sorted best-first, with 'rank' added — competition
    ranking, so tied scores share a rank and the next distinct score jumps
    by the tie count (1, 2, 2, 4). Input need not be pre-sorted, and the
    caller's dicts are left untouched.
    """
    ranked = sorted((dict(e) for e in entries), key=lambda e: e["score"], reverse=True)
    rank = 0
    prev_score = None
    for seen, entry in enumerate(ranked, start=1):
        if entry["score"] != prev_score:
            rank = seen
            prev_score = entry["score"]
        entry["rank"] = rank
    return ranked


def averaged_points_for_tied_group(rank, tie_count, points_for_rank):
    """
    The tie-breaker rule: tied teams split the average of what each of their
    tied ranks would individually pay. Two teams tied at rank 2 (pushing the
    next team to rank 4) each get (points(2) + points(3)) / 2.
    """
    values = [points_for_rank(rank + i) for i in range(tie_count)]
    return sum(values) / len(values)


def score_week(entries, points_for_rank, precision=3):
    """
    The one implementation of "how do we score a week".

    entries: list of dicts each carrying a 'score' (any other fields are
    preserved). Returns a new best-first list with 'rank' and
    'points_awarded' added.

    Untied weeks always produce whole numbers; `precision` only matters for
    tied groups, where a 3-way tie can yield e.g. 1.667. No real tie has
    occurred in 51 weeks of league history, so this path is covered by
    synthetic tests only.
    """
    ranked = rank_week_with_ties(entries)
    tie_counts = Counter(entry["rank"] for entry in ranked)
    for entry in ranked:
        entry["points_awarded"] = round(
            averaged_points_for_tied_group(entry["rank"], tie_counts[entry["rank"]], points_for_rank),
            precision,
        )
    return ranked


# ---------------------------------------------------------------------------
# Normalized week computation
# ---------------------------------------------------------------------------

def _points_of(matchup):
    """Sleeper sends an explicit null for some rosters, so `or 0` is required."""
    return matchup.get("points", 0) or 0


def compute_week(matchups, owner_map, points_for_rank):
    """
    Turn one week's raw Sleeper matchup list into a normalized result:

        {
          "league_average", "spread",
          "high_score": {"owner", "team", "points"},
          "low_score":  {"owner", "team", "points"},
          "results": [ {owner_id, display_name, team_name, roster_id,
                        weekly_score, weekly_rank, points_awarded,
                        opponent_owner_id, opponent_display_name, h2h_win} ]
        }

    Ranking spans the WHOLE league, not each matchup pair — matchup_id is
    used only to record head-to-head outcomes, which the league's standings
    deliberately ignore. Returns None for an empty week.
    """
    if not matchups:
        return None

    by_roster = {m["roster_id"]: m for m in matchups}

    # Opponent lookup from matchup_id. Byes and non-standard >2-team groups
    # are intentionally left without an opponent rather than guessed at.
    groups = defaultdict(list)
    for matchup in matchups:
        matchup_id = matchup.get("matchup_id")
        if matchup_id is None:
            # A roster on bye (or otherwise unscheduled) has a null matchup_id.
            # Grouping those together would pair two unrelated bye teams as
            # each other's opponent and invent an H2H result.
            continue
        groups[matchup_id].append(matchup)
    opponent_of = {}
    for group in groups.values():
        if len(group) == 2:
            a, b = group
            opponent_of[a["roster_id"]] = b["roster_id"]
            opponent_of[b["roster_id"]] = a["roster_id"]

    entries = []
    for matchup in matchups:
        roster_id = matchup["roster_id"]
        owner = owner_map.get(roster_id) or {
            "owner_id": f"roster_{roster_id}",
            "display_name": f"Roster {roster_id}",
            "team_name": f"Team {roster_id}",
        }
        entries.append({
            "roster_id": roster_id,
            "owner_id": owner["owner_id"],
            "display_name": owner["display_name"],
            "team_name": owner["team_name"],
            "score": _points_of(matchup),
        })

    ranked = score_week(entries, points_for_rank)

    results = []
    for entry in ranked:
        roster_id = entry["roster_id"]
        opp_roster_id = opponent_of.get(roster_id)
        opp_owner = owner_map.get(opp_roster_id) or {} if opp_roster_id is not None else {}
        h2h_win = None
        if opp_roster_id is not None and opp_roster_id in by_roster:
            h2h_win = entry["score"] > _points_of(by_roster[opp_roster_id])
        results.append({
            "owner_id": entry["owner_id"],
            "display_name": entry["display_name"],
            "team_name": entry["team_name"],
            "roster_id": roster_id,
            "weekly_score": round(entry["score"], 2),
            "weekly_rank": entry["rank"],
            "points_awarded": entry["points_awarded"],
            "opponent_owner_id": opp_owner.get("owner_id"),
            "opponent_display_name": opp_owner.get("display_name"),
            "h2h_win": h2h_win,
        })

    scores = [entry["score"] for entry in ranked]
    high, low = ranked[0], ranked[-1]
    return {
        "league_average": round(sum(scores) / len(scores), 2) if scores else 0,
        "high_score": {
            "owner": high["display_name"],
            "team": high["team_name"],
            "points": round(high["score"], 2),
        },
        "low_score": {
            "owner": low["display_name"],
            "team": low["team_name"],
            "points": round(low["score"], 2),
        },
        "spread": round(high["score"] - low["score"], 2),
        "results": results,
    }


def fetch_season_weeks(season, league_id, url_base=DEFAULT_BASE_URL,
                       requested_max=DEFAULT_MAX_WEEKS, verbose=False,
                       name_overrides=None):
    """
    Fetch and normalize a whole season.

    Returns (owner_map, {week_number: week_result}). Weeks are capped by
    determine_effective_max_week, and a week Sleeper returns empty ends the
    season (rather than being treated as played-and-scoreless).
    """
    owner_map = build_owner_map(
        fetch_users(league_id, url_base),
        fetch_rosters(league_id, url_base),
        name_overrides,
    )
    points_for_rank = points_for_rank_fn(season)
    max_week = determine_effective_max_week(season, requested_max, url_base, verbose=verbose)

    weeks = {}
    for week in range(1, max_week + 1):
        week_result = compute_week(
            fetch_week_matchups(league_id, week, url_base),
            owner_map,
            points_for_rank,
        )
        if week_result is None:
            break
        weeks[week] = week_result
    return owner_map, weeks
