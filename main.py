"""
Sleeper Fantasy Football Multi-Year Analyzer — CORRECTED

This replaces main2.py. Same output (a 9-sheet Excel workbook covering
Career, per-season, League Averages, Week MinMax, Owner Summary, Scoreboard,
and Scoreboard Summary), same config file (league_data.json), same CLI
(`python main2.py`) — but fixes three real bugs found by inspecting
fantasy_multi_year_scores_20251217_222523.xlsx:

1. Owner Summary and Scoreboard Summary collapsed all 7 real owners into one
   garbage row, because Sleeper doesn't return a `username` for anyone in
   this league, and the old code defaulted everyone to the literal string
   "Unknown" — which then became a shared, colliding dictionary key.
   FIX: every aggregation below keys off `owner_id` (Sleeper's internal
   user_id, guaranteed unique and always present), not `username`.

2. No tie-breaker averaging: teams tied at the same weekly score got
   different Scoreboard Points depending on arbitrary sort order, instead
   of sharing the average of what their tied ranks would each pay.
   FIX: proper competition ranking (ties share a rank) with the point
   value for a tied group averaged across the ranks it spans.

3. "Is this week over?" was decided by checking whether Sleeper returned a
   non-empty matchup list — but Sleeper returns a non-empty list (with
   every team at 0 points) for weeks that are scheduled but not yet
   played, so that check can't tell the difference.
   FIX: ask Sleeper's /state/nfl endpoint what week it currently is, and
   for whichever season matches that live state, only fetch weeks
   strictly before the current one. Past seasons are assumed fully played.

Everything else (season summaries printed to the console, the shape of
each Excel sheet, rolling averages, high/low week finding) is unchanged.
"""

import sys
import json
from collections import defaultdict
from datetime import datetime

import requests


# ---------------------------------------------------------------------------
# Config / API
# ---------------------------------------------------------------------------

def load_json(file_path):
    with open(file_path, 'r') as f:
        return json.load(f)


def rosters_response(league_id, base_url):
    data = {}
    try:
        response = requests.get(f"{base_url}/league/{league_id}/rosters")
        response.raise_for_status()
        data = response.json()
        print(f"API response for league {league_id} is successful.")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from API: {e}")
    return data


def users_response(league_id, base_url):
    data = {}
    try:
        response = requests.get(f"{base_url}/league/{league_id}/users")
        response.raise_for_status()
        data = response.json()
        print(f"API response for league {league_id} is successful.")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from API: {e}")
    return data


def nfl_state_response(base_url):
    try:
        response = requests.get(f"{base_url}/state/nfl")
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching NFL state: {e}")
        return {}


def determine_effective_max_week(year, requested_max, base_url):
    """
    FIX for bug #3. Only the season matching Sleeper's live current season
    gets capped to "weeks strictly before the current week" — past seasons
    are assumed fully played, and if the live state can't be fetched at all,
    we fall back to requested_max rather than guessing.
    """
    state = nfl_state_response(base_url)
    current_season = state.get('season')
    current_week = state.get('week')
    if current_season is not None and str(year) == str(current_season) and isinstance(current_week, int):
        capped = max(0, min(requested_max, current_week - 1))
        print(f"   Live NFL state says season {current_season} is on week {current_week} "
              f"-> treating weeks 1-{capped} as complete for {year}.")
        return capped
    return requested_max


def matchup_response(league_id, base_url, max_week=17):
    all_scores = {}
    for i in range(1, max_week + 1):
        try:
            response = requests.get(f"{base_url}/league/{league_id}/matchups/{str(i)}")
            response.raise_for_status()
            data = response.json()
            if data:
                all_scores[i] = data
            else:
                print(f"No data found for week {i}.")
        except requests.exceptions.RequestException as e:
            print(f"Error fetching data from API: {e}")
    return all_scores


# ---------------------------------------------------------------------------
# Owner / roster mapping — FIX for bug #1
# ---------------------------------------------------------------------------

def get_team_names_mapping(users, rosters):
    """
    Create a mapping of roster_id -> team/owner info.

    owner_key is the field every downstream aggregation should group by.
    It's always Sleeper's user_id (owner_id on the roster) — never the
    possibly-missing `username` — so owners can never collide with each
    other just because Sleeper didn't return a username for them.
    """
    team_mapping = {}

    user_info = {}
    for user in users:
        user_info[user['user_id']] = {
            'display_name': user.get('display_name') or user.get('username') or f"User {user['user_id']}",
            'team_name': (user.get('metadata') or {}).get('team_name'),
            'username': user.get('username'),  # may legitimately be None — display-only, never a dict key
        }

    for roster in rosters:
        roster_id = roster['roster_id']
        owner_id = roster.get('owner_id')

        if owner_id and owner_id in user_info:
            user = user_info[owner_id]
            team_name = user['team_name'] or user['display_name']
            team_mapping[roster_id] = {
                'owner_key': owner_id,
                'team_name': team_name,
                'owner_name': user['display_name'],
                'username': user['username'],  # display-only; may be None
            }
        else:
            fallback_key = owner_id or f"roster_{roster_id}"
            team_mapping[roster_id] = {
                'owner_key': fallback_key,
                'team_name': f'Team {roster_id}',
                'owner_name': 'Unknown Owner',
                'username': None,
            }

    return team_mapping


# ---------------------------------------------------------------------------
# Weekly scores
# ---------------------------------------------------------------------------

def organize_weekly_scores(matchups_data, team_mapping):
    weekly_scores = {}
    for week, matchups in matchups_data.items():
        weekly_scores[week] = {}
        for matchup in matchups:
            roster_id = matchup['roster_id']
            points = matchup.get('points', 0)
            matchup_id = matchup.get('matchup_id')

            team_info = team_mapping.get(roster_id, {
                'owner_key': f'roster_{roster_id}',
                'team_name': f'Team {roster_id}',
                'owner_name': 'Unknown',
                'username': None,
            })

            weekly_scores[week][roster_id] = {
                'owner_key': team_info['owner_key'],
                'team_name': team_info['team_name'],
                'owner_name': team_info['owner_name'],
                'points': points,
                'matchup_id': matchup_id,
                'starters': matchup.get('starters', []),
                'players': matchup.get('players', []),
            }
    return weekly_scores


def calculate_season_summary(weekly_scores):
    team_stats = {}
    for week, week_data in weekly_scores.items():
        for roster_id, team_data in week_data.items():
            if roster_id not in team_stats:
                team_stats[roster_id] = {
                    'team_name': team_data['team_name'],
                    'owner_name': team_data['owner_name'],
                    'total_points': 0,
                    'weeks_played': 0,
                    'weekly_scores': [],
                }
            team_stats[roster_id]['total_points'] += team_data['points']
            team_stats[roster_id]['weeks_played'] += 1
            team_stats[roster_id]['weekly_scores'].append({'week': week, 'points': team_data['points']})

    for roster_id, stats in team_stats.items():
        stats['average_points'] = stats['total_points'] / stats['weeks_played'] if stats['weeks_played'] > 0 else 0
    return team_stats


def find_highest_lowest_weeks(weekly_scores):
    all_scores = []
    for week, week_data in weekly_scores.items():
        for roster_id, team_data in week_data.items():
            all_scores.append({
                'week': week, 'team_name': team_data['team_name'],
                'owner_name': team_data['owner_name'], 'points': team_data['points'], 'roster_id': roster_id,
            })
    if not all_scores:
        return None, None
    all_scores.sort(key=lambda x: x['points'], reverse=True)
    return all_scores[0], all_scores[-1]


def fetch_season_data(year, league_id, base_url, weeks_to_fetch):
    print(f"\n🏈 Fetching data for {year} season...")
    print(f"   League ID: {league_id}")

    max_weeks = determine_effective_max_week(year, weeks_to_fetch, base_url)
    print(f"   Fetching {max_weeks} weeks of data...")

    rosters = rosters_response(league_id, base_url)
    users = users_response(league_id, base_url)
    matchups = matchup_response(league_id, base_url, max_weeks)

    if not rosters or not users or not matchups:
        print(f"   ❌ Failed to fetch data for {year}")
        return None

    print(f"   ✅ Successfully fetched data for {year}")

    team_mapping = get_team_names_mapping(users, rosters)
    weekly_scores = organize_weekly_scores(matchups, team_mapping)

    return {'year': year, 'weekly_scores': weekly_scores, 'team_mapping': team_mapping, 'weeks_fetched': max_weeks}


def combine_multi_year_data(season_data_list):
    combined_weekly_scores = {}
    all_team_mappings = {}

    for season_data in season_data_list:
        year = season_data['year']
        weekly_scores = season_data['weekly_scores']
        team_mapping = season_data['team_mapping']

        for week, week_data in weekly_scores.items():
            year_week_key = f"{year}_W{week}"
            combined_weekly_scores[year_week_key] = {}
            for roster_id, team_data in week_data.items():
                team_data_copy = team_data.copy()
                team_data_copy['season'] = year
                team_data_copy['original_week'] = week
                combined_weekly_scores[year_week_key][roster_id] = team_data_copy

        all_team_mappings.update(team_mapping)

    return combined_weekly_scores, all_team_mappings


def calculate_rolling_averages(weekly_scores):
    rolling_data = {}
    all_weeks = sorted(weekly_scores.keys())
    all_teams = set()
    for week_data in weekly_scores.values():
        all_teams.update(week_data.keys())

    for roster_id in all_teams:
        rolling_data[roster_id] = {'team_name': '', 'owner_name': '', 'weekly_totals': {}, 'rolling_averages': {}}

    for week in all_weeks:
        week_data = weekly_scores.get(week, {})
        for roster_id in all_teams:
            if roster_id in week_data:
                team_data = week_data[roster_id]
                rolling_data[roster_id]['team_name'] = team_data['team_name']
                rolling_data[roster_id]['owner_name'] = team_data['owner_name']
                rolling_data[roster_id]['weekly_totals'][week] = team_data['points']

                weeks_played, total_points = [], 0
                for w in all_weeks:
                    if w > week:
                        break
                    if roster_id in weekly_scores.get(w, {}):
                        weeks_played.append(w)
                        total_points += weekly_scores[w][roster_id]['points']

                if weeks_played:
                    rolling_data[roster_id]['rolling_averages'][week] = {
                        'average': total_points / len(weeks_played),
                        'weeks_included': len(weeks_played),
                        'total_points': total_points,
                    }
    return rolling_data


# ---------------------------------------------------------------------------
# Scoreboard Points, with real tie-breaker averaging — FIX for bug #2
# ---------------------------------------------------------------------------

def points_for_rank_fn(season):
    """rank -> Scoreboard Points, per the league's rule tables."""
    if str(season) in ('2023', '2024'):
        def f(rank):
            if rank <= 2:
                return 2
            if rank <= 4:
                return 1
            return 0
        return f

    def f_2025_plus(rank):
        if rank <= 2:
            return 2
        if rank <= 5:
            return 1
        return 0
    return f_2025_plus


def rank_week_with_ties(week_teams):
    """
    week_teams: list of dicts with a 'score' key, already carrying whatever
    other fields the caller needs. Returns the same list with 'rank' added,
    using competition ranking (ties share a rank; the next distinct score
    jumps by however many teams tied ahead of it — e.g. 1, 2, 2, 4).
    Input does not need to be pre-sorted.
    """
    ranked = sorted(week_teams, key=lambda t: t['score'], reverse=True)
    rank = 0
    prev_score = None
    seen = 0
    for t in ranked:
        seen += 1
        if t['score'] != prev_score:
            rank = seen
            prev_score = t['score']
        t['rank'] = rank
    return ranked


def averaged_points_for_tied_group(rank, tie_count, points_for_rank):
    """The tie-breaker rule: average the point values the tied ranks would
    each individually earn. E.g. two teams tied at what would be rank 2
    and rank 3 both get (points_for_rank(2) + points_for_rank(3)) / 2."""
    values = [points_for_rank(rank + i) for i in range(tie_count)]
    return sum(values) / len(values)


# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------

def export_multi_year_excel_with_rolling(combined_weekly_scores, team_mapping):
    try:
        import pandas as pd
    except ImportError:
        print("pandas is required for Excel export. Installing...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pandas", "openpyxl"])
        import pandas as pd

    print("Preparing multi-year data with rolling averages for Excel export...")

    seasons_combined_data = {}
    for year_week_key, week_data in sorted(combined_weekly_scores.items()):
        year = year_week_key.split('_')[0]
        week = int(year_week_key.split('_W')[1])
        seasons_combined_data.setdefault(year, [])

        season_weekly_scores = {}
        for ywk, wk_data in combined_weekly_scores.items():
            if ywk.startswith(year + '_'):
                wk_num = int(ywk.split('_W')[1])
                season_weekly_scores[wk_num] = wk_data

        rolling_data = calculate_rolling_averages(season_weekly_scores)

        for roster_id, team_data in week_data.items():
            rolling_avg, total_points = 0, 0
            if roster_id in rolling_data and week in rolling_data[roster_id]['rolling_averages']:
                info = rolling_data[roster_id]['rolling_averages'][week]
                rolling_avg, total_points = info['average'], info['total_points']

            seasons_combined_data[year].append({
                'Season': year, 'Week': week,
                'Team_Name': team_data['team_name'], 'Owner_Name': team_data['owner_name'],
                'Owner_Key': team_data['owner_key'],
                'Rolling_Average': round(rolling_avg, 2), 'Total_Points': round(total_points, 2),
                'Weekly_Score': round(team_data['points'], 2),
            })

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"fantasy_multi_year_scores_{timestamp}.xlsx"

    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        column_order = ['Season', 'Week', 'Team_Name', 'Owner_Name', 'Rolling_Average', 'Total_Points', 'Weekly_Score']

        all_career_data = []
        for year in sorted(seasons_combined_data.keys()):
            all_career_data.extend(seasons_combined_data[year])
        career_df = pd.DataFrame(all_career_data)[column_order + ['Owner_Key']].drop(columns=['Owner_Key'])
        career_df.to_excel(writer, sheet_name='Career', index=False)

        for year in sorted(seasons_combined_data.keys()):
            season_df = pd.DataFrame(seasons_combined_data[year])[column_order]
            season_df.to_excel(writer, sheet_name=f'{year}', index=False)

        # ---- League Averages / Week MinMax (unchanged logic; now naturally
        # excludes phantom unplayed weeks thanks to the fetch-side fix) ----
        week_averages = {}
        for year in sorted(seasons_combined_data.keys()):
            for record in seasons_combined_data[year]:
                key = (record['Season'], record['Week'])
                if key not in week_averages:
                    week_averages[key] = {'total_points': 0, 'team_count': 0, 'entries': []}
                week_averages[key]['total_points'] += record['Weekly_Score']
                week_averages[key]['team_count'] += 1
                week_averages[key]['entries'].append({
                    'team': record['Team_Name'], 'owner': record['Owner_Name'],
                    'owner_key': record['Owner_Key'], 'score': record['Weekly_Score'],
                })

        league_averages_data, week_minmax_data = [], []
        for (season, week), data in sorted(week_averages.items()):
            league_avg = data['total_points'] / data['team_count'] if data['team_count'] > 0 else 0
            entries = sorted(data['entries'], key=lambda x: x['score'], reverse=True)
            high, low = entries[0], entries[-1]

            league_averages_data.append({
                'Season': season, 'Week': week, 'League_Average': round(league_avg, 2),
                'Teams_Playing': data['team_count'],
                'High_Score': round(high['score'], 2), 'Low_Score': round(low['score'], 2),
                'Point_Spread': round(high['score'] - low['score'], 2),
            })
            week_minmax_data.append({
                'Season': season, 'Week': week, 'Teams_Playing': data['team_count'],
                'League_Average': round(league_avg, 2),
                'High_Score': round(high['score'], 2), 'High_Team': high['team'], 'High_Owner': high['owner'],
                'Low_Score': round(low['score'], 2), 'Low_Team': low['team'], 'Low_Owner': low['owner'],
                'Point_Spread': round(high['score'] - low['score'], 2),
            })

        pd.DataFrame(league_averages_data).to_excel(writer, sheet_name='League Averages', index=False)
        minmax_df = pd.DataFrame(week_minmax_data)
        if not minmax_df.empty:
            minmax_df = minmax_df.sort_values(by=['Season', 'Week'])
        minmax_df.to_excel(writer, sheet_name='Week MinMax', index=False)

        # ---- Owner Summary — FIX: keyed by Owner_Key, not a possibly-missing username ----
        owner_aggregates = {}
        for year in sorted(seasons_combined_data.keys()):
            for record in seasons_combined_data[year]:
                key = record['Owner_Key']
                if key not in owner_aggregates:
                    owner_aggregates[key] = {
                        'owner_key': key, 'owner_name': record['Owner_Name'], 'latest_team': record['Team_Name'],
                        'career_total_points': 0, 'weeks_played': 0, 'season_totals': {},
                    }
                agg = owner_aggregates[key]
                agg['latest_team'] = record['Team_Name']
                agg['owner_name'] = record['Owner_Name']
                agg['career_total_points'] += record['Weekly_Score']
                agg['weeks_played'] += 1
                agg['season_totals'][str(record['Season'])] = agg['season_totals'].get(str(record['Season']), 0) + record['Weekly_Score']

        seasons_list = sorted(seasons_combined_data.keys())
        owner_summary_rows = []
        for key, agg in owner_aggregates.items():
            career_avg = agg['career_total_points'] / agg['weeks_played'] if agg['weeks_played'] > 0 else 0
            row = {
                'Owner_Name': agg['owner_name'], 'Latest_Team': agg['latest_team'],
                'Career_Total_Points': round(agg['career_total_points'], 2),
                'Career_Average': round(career_avg, 2), 'Weeks_Played': agg['weeks_played'],
            }
            for s in seasons_list:
                row[f'{s}_Total'] = round(agg['season_totals'].get(str(s), 0), 2)
            owner_summary_rows.append(row)
        owner_summary_rows.sort(key=lambda r: r['Career_Total_Points'], reverse=True)
        pd.DataFrame(owner_summary_rows).to_excel(writer, sheet_name='Owner Summary', index=False)

        # ---- Scoreboard — FIX: tie-aware ranking + averaged points, keyed by Owner_Key ----
        scoreboard_data = []
        team_scoreboard_totals = {}
        team_rolling_totals = {}
        team_rank_history = {}

        for (season, week), data in sorted(week_averages.items()):
            points_for_rank = points_for_rank_fn(season)
            week_teams = [
                {'owner_key': e['owner_key'], 'team_name': e['team'], 'owner_name': e['owner'], 'score': e['score']}
                for e in data['entries']
            ]
            ranked = rank_week_with_ties(week_teams)

            rank_counts = defaultdict(int)
            for t in ranked:
                rank_counts[t['rank']] += 1

            week_rolling_data = []
            for t in ranked:
                key = t['owner_key']
                awarded = round(averaged_points_for_tied_group(t['rank'], rank_counts[t['rank']], points_for_rank), 3)

                if key not in team_scoreboard_totals:
                    team_scoreboard_totals[key] = {
                        'team_name': t['team_name'], 'owner_name': t['owner_name'],
                        'total_points': 0, 'weeks_played': 0, 'season_totals': {},
                    }
                team_rolling_totals.setdefault(key, 0)
                team_rank_history.setdefault(f"{key}_{season}", [])

                team_scoreboard_totals[key]['team_name'] = t['team_name']
                # Display fields both follow the latest season seen, so this sheet
                # agrees with Owner Summary when an owner renames themselves.
                team_scoreboard_totals[key]['owner_name'] = t['owner_name']
                team_scoreboard_totals[key]['total_points'] += awarded
                team_scoreboard_totals[key]['weeks_played'] += 1
                team_rolling_totals[key] += awarded
                season_key = str(season)
                team_scoreboard_totals[key]['season_totals'][season_key] = \
                    team_scoreboard_totals[key]['season_totals'].get(season_key, 0) + awarded

                team_rank_history[f"{key}_{season}"].append(t['rank'])
                rolling_avg_rank = sum(team_rank_history[f"{key}_{season}"]) / len(team_rank_history[f"{key}_{season}"])

                week_rolling_data.append({
                    'owner_key': key, 'team_name': t['team_name'], 'owner_name': t['owner_name'],
                    'weekly_score': t['score'], 'weekly_rank': t['rank'], 'points_awarded': awarded,
                    'rolling_total': team_rolling_totals[key], 'rolling_avg_rank': rolling_avg_rank,
                })

            week_rolling_data.sort(key=lambda x: x['rolling_total'], reverse=True)
            for standings_rank, t in enumerate(week_rolling_data, 1):
                scoreboard_data.append({
                    'Season': season, 'Week': week, 'Team_Name': t['team_name'], 'Owner_Name': t['owner_name'],
                    'Weekly_Score': round(t['weekly_score'], 2), 'Weekly_Rank': t['weekly_rank'],
                    'Points_Awarded': t['points_awarded'], 'Rolling_Total': round(t['rolling_total'], 3),
                    'Rolling_Average_Rank': round(t['rolling_avg_rank'], 3), 'Current_Standing': standings_rank,
                })

        pd.DataFrame(scoreboard_data).to_excel(writer, sheet_name='Scoreboard', index=False)

        summary_data = []
        for key, totals in team_scoreboard_totals.items():
            row = {
                'Team_Name': totals['team_name'], 'Owner_Name': totals['owner_name'],
                'Total_Scoreboard_Points': round(totals['total_points'], 3), 'Weeks_Played': totals['weeks_played'],
                'Points_Per_Week': round(totals['total_points'] / totals['weeks_played'], 3) if totals['weeks_played'] > 0 else 0,
            }
            for year in sorted(seasons_combined_data.keys()):
                row[f'{year}_Points'] = round(totals['season_totals'].get(year, 0), 3)
            summary_data.append(row)
        summary_data.sort(key=lambda r: r['Total_Scoreboard_Points'], reverse=True)
        pd.DataFrame(summary_data).to_excel(writer, sheet_name='Scoreboard Summary', index=False)

        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except Exception:
                        pass
                worksheet.column_dimensions[column_letter].width = min(max_length + 2, 50)

    print(f"✅ Multi-year data with rolling averages exported to Excel: {filename}")
    total_records = sum(len(data) for data in seasons_combined_data.values())
    print(f"📊 Total records: {total_records}")
    print(f"📋 Seasons included: {', '.join(sorted(seasons_combined_data.keys()))}")
    for year in sorted(seasons_combined_data.keys()):
        season_records = len(seasons_combined_data[year])
        max_week = max(record['Week'] for record in seasons_combined_data[year]) if seasons_combined_data[year] else 0
        print(f"   {year}: {season_records} records, {max_week} weeks")

    return filename


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    config_file = 'league_data.json'
    league_data = load_json(config_file)
    base_url = league_data['api']['base_url']
    league_ids = league_data.get('league_ids', {})
    weeks_to_fetch = league_data.get('settings', {}).get('weeks_to_fetch', 17)

    print("🏈 SLEEPER FANTASY FOOTBALL MULTI-YEAR ANALYZER (corrected)")
    print("=" * 60)
    print(f"Available seasons: {', '.join(sorted(league_ids.keys()))}")

    all_season_data = []
    for year, league_id in sorted(league_ids.items()):
        season_data = fetch_season_data(int(year), league_id, base_url, weeks_to_fetch)
        if season_data:
            all_season_data.append(season_data)

    if not all_season_data:
        print("❌ No data could be fetched from any season.")
        sys.exit(1)

    print(f"\n✅ Successfully fetched data from {len(all_season_data)} seasons")
    combined_weekly_scores, combined_team_mapping = combine_multi_year_data(all_season_data)
    print(f"📊 Total combined records: {sum(len(wd) for wd in combined_weekly_scores.values())}")

    print("\n" + "=" * 80)
    print("MULTI-YEAR SEASON SUMMARIES")
    print("=" * 80)
    for season_data in all_season_data:
        year, weekly_scores, weeks_fetched = season_data['year'], season_data['weekly_scores'], season_data['weeks_fetched']
        print(f"\n--- {year} SEASON ({weeks_fetched} weeks) ---")
        team_stats = calculate_season_summary(weekly_scores)
        sorted_teams = sorted(team_stats.items(), key=lambda x: x[1]['total_points'], reverse=True)
        print(f"{'Rank':<4} {'Team':<25} {'Owner':<20} {'Total':<8} {'Avg':<6}")
        print("-" * 70)
        for i, (roster_id, stats) in enumerate(sorted_teams, 1):
            print(f"{i:<4} {stats['team_name'][:24]:<25} {stats['owner_name'][:19]:<20} "
                  f"{stats['total_points']:<8.1f} {stats['average_points']:<6.1f}")
        highest, lowest = find_highest_lowest_weeks(weekly_scores)
        if highest and lowest:
            print(f"🏆 High: {highest['team_name']} - Week {highest['week']} - {highest['points']:.1f} pts")
            print(f"💀 Low: {lowest['team_name']} - Week {lowest['week']} - {lowest['points']:.1f} pts")

    excel_file = export_multi_year_excel_with_rolling(combined_weekly_scores, combined_team_mapping)
    print(f"\n📁 Excel file created: {excel_file}")
    return {'all_season_data': all_season_data, 'combined_weekly_scores': combined_weekly_scores, 'excel_file': excel_file}


if __name__ == "__main__":
    main()
