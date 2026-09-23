"""
Sleeper Fantasy Football Multi-Year Analyzer — Excel archive
============================================================

Produces the 9-sheet workbook (Career, one sheet per season, League Averages,
Week MinMax, Owner Summary, Scoreboard, Scoreboard Summary) from live Sleeper
data. Config comes from league_data.json; run with `python main.py`.

All identity and scoring logic lives in league_core.py — this file owns only
the workbook shaping. That split exists because the same rules previously had
two independent implementations here and in sync_pipeline.py, so every fix had
to be made twice and they would eventually have drifted.

The workbook is an archive/offline-analysis artifact; the live dashboard is fed
by sync_pipeline.py from the same core.
"""

import sys
from collections import defaultdict
from datetime import datetime

import league_core as core


# ---------------------------------------------------------------------------
# Console summaries
# ---------------------------------------------------------------------------

def print_season_summary(season, weeks):
    """Per-season leaderboard by total points, plus that season's high/low week."""
    print(f"\n--- {season} SEASON ({len(weeks)} weeks) ---")

    totals = defaultdict(lambda: {"team_name": "", "owner_name": "", "total": 0.0, "weeks": 0})
    best = worst = None
    for week_number in sorted(weeks):
        for entry in weeks[week_number]["results"]:
            acc = totals[entry["owner_id"]]
            acc["team_name"] = entry["team_name"]
            acc["owner_name"] = entry["display_name"]
            acc["total"] += entry["weekly_score"]
            acc["weeks"] += 1

            marker = {"week": week_number, "team": entry["team_name"], "points": entry["weekly_score"]}
            if best is None or marker["points"] > best["points"]:
                best = marker
            if worst is None or marker["points"] < worst["points"]:
                worst = marker

    print(f"{'Rank':<4} {'Team':<25} {'Owner':<20} {'Total':<8} {'Avg':<6}")
    print("-" * 70)
    ranked = sorted(totals.values(), key=lambda t: t["total"], reverse=True)
    for position, team in enumerate(ranked, start=1):
        average = team["total"] / team["weeks"] if team["weeks"] else 0
        print(f"{position:<4} {team['team_name'][:24]:<25} {team['owner_name'][:19]:<20} "
              f"{team['total']:<8.1f} {average:<6.1f}")

    if best and worst:
        print(f"🏆 High: {best['team']} - Week {best['week']} - {best['points']:.1f} pts")
        print(f"💀 Low: {worst['team']} - Week {worst['week']} - {worst['points']:.1f} pts")


# ---------------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------------

def build_season_rows(season, weeks):
    """
    One row per team per week, carrying that team's running average and total
    through that week. Owner_Key (owner_id) rides along for aggregation and is
    dropped before the sheet is written.
    """
    rows = []
    running = defaultdict(lambda: {"total": 0.0, "weeks": 0})
    for week_number in sorted(weeks):
        for entry in weeks[week_number]["results"]:
            acc = running[entry["owner_id"]]
            acc["total"] += entry["weekly_score"]
            acc["weeks"] += 1
            rows.append({
                "Season": str(season),
                "Week": week_number,
                "Team_Name": entry["team_name"],
                "Owner_Name": entry["display_name"],
                "Owner_Key": entry["owner_id"],
                "Rolling_Average": round(acc["total"] / acc["weeks"], 2),
                "Total_Points": round(acc["total"], 2),
                "Weekly_Score": round(entry["weekly_score"], 2),
            })
    return rows


def build_week_tables(seasons_weeks):
    """League Averages + Week MinMax rows, in (season, week) order."""
    league_averages, week_minmax = [], []
    for season in sorted(seasons_weeks):
        weeks = seasons_weeks[season]
        for week_number in sorted(weeks):
            week = weeks[week_number]
            high, low = week["high_score"], week["low_score"]
            teams_playing = len(week["results"])

            league_averages.append({
                "Season": str(season), "Week": week_number,
                "League_Average": week["league_average"], "Teams_Playing": teams_playing,
                "High_Score": high["points"], "Low_Score": low["points"],
                "Point_Spread": week["spread"],
            })
            week_minmax.append({
                "Season": str(season), "Week": week_number, "Teams_Playing": teams_playing,
                "League_Average": week["league_average"],
                "High_Score": high["points"], "High_Team": high["team"], "High_Owner": high["owner"],
                "Low_Score": low["points"], "Low_Team": low["team"], "Low_Owner": low["owner"],
                "Point_Spread": week["spread"],
            })
    return league_averages, week_minmax


def build_owner_summary(seasons_rows, seasons):
    """Career totals per owner, keyed by Owner_Key so a rename can't split them."""
    aggregates = {}
    for season in sorted(seasons_rows):
        for record in seasons_rows[season]:
            key = record["Owner_Key"]
            agg = aggregates.setdefault(key, {
                "owner_name": record["Owner_Name"], "latest_team": record["Team_Name"],
                "total": 0.0, "weeks": 0, "season_totals": defaultdict(float),
            })
            # Display fields follow the latest season processed, so a renamed
            # owner shows their current name and team.
            agg["owner_name"] = record["Owner_Name"]
            agg["latest_team"] = record["Team_Name"]
            agg["total"] += record["Weekly_Score"]
            agg["weeks"] += 1
            agg["season_totals"][str(record["Season"])] += record["Weekly_Score"]

    rows = []
    for agg in aggregates.values():
        row = {
            "Owner_Name": agg["owner_name"], "Latest_Team": agg["latest_team"],
            "Career_Total_Points": round(agg["total"], 2),
            "Career_Average": round(agg["total"] / agg["weeks"], 2) if agg["weeks"] else 0,
            "Weeks_Played": agg["weeks"],
        }
        for season in seasons:
            row[f"{season}_Total"] = round(agg["season_totals"].get(str(season), 0), 2)
        rows.append(row)
    rows.sort(key=lambda r: r["Career_Total_Points"], reverse=True)
    return rows


def build_scoreboard(seasons_weeks, seasons):
    """
    Scoreboard + Scoreboard Summary.

    Rolling_Total is cumulative across every season (all-time Scoreboard
    Points), while Rolling_Average_Rank resets each season. Current_Standing
    is the team's position by Rolling_Total as of that week.
    """
    scoreboard_rows = []
    career_totals = {}
    rolling_total = defaultdict(float)
    rank_history = defaultdict(list)

    for season in sorted(seasons_weeks):
        weeks = seasons_weeks[season]
        for week_number in sorted(weeks):
            week_rows = []
            for entry in weeks[week_number]["results"]:
                key = entry["owner_id"]
                awarded = entry["points_awarded"]

                totals = career_totals.setdefault(key, {
                    "team_name": entry["team_name"], "owner_name": entry["display_name"],
                    "total": 0.0, "weeks": 0, "season_totals": defaultdict(float),
                })
                totals["team_name"] = entry["team_name"]
                totals["owner_name"] = entry["display_name"]
                totals["total"] += awarded
                totals["weeks"] += 1
                totals["season_totals"][str(season)] += awarded

                rolling_total[key] += awarded
                rank_history[(key, str(season))].append(entry["weekly_rank"])
                history = rank_history[(key, str(season))]

                week_rows.append({
                    "Season": str(season), "Week": week_number,
                    "Team_Name": entry["team_name"], "Owner_Name": entry["display_name"],
                    "Weekly_Score": entry["weekly_score"], "Weekly_Rank": entry["weekly_rank"],
                    "Points_Awarded": awarded,
                    "Rolling_Total": round(rolling_total[key], 3),
                    "Rolling_Average_Rank": round(sum(history) / len(history), 3),
                })

            week_rows.sort(key=lambda r: r["Rolling_Total"], reverse=True)
            for position, row in enumerate(week_rows, start=1):
                row["Current_Standing"] = position
            scoreboard_rows.extend(week_rows)

    summary_rows = []
    for totals in career_totals.values():
        row = {
            "Team_Name": totals["team_name"], "Owner_Name": totals["owner_name"],
            "Total_Scoreboard_Points": round(totals["total"], 3),
            "Weeks_Played": totals["weeks"],
            "Points_Per_Week": round(totals["total"] / totals["weeks"], 3) if totals["weeks"] else 0,
        }
        for season in seasons:
            row[f"{season}_Points"] = round(totals["season_totals"].get(str(season), 0), 3)
        summary_rows.append(row)
    summary_rows.sort(key=lambda r: r["Total_Scoreboard_Points"], reverse=True)

    return scoreboard_rows, summary_rows


# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------

SHEET_COLUMNS = ["Season", "Week", "Team_Name", "Owner_Name",
                 "Rolling_Average", "Total_Points", "Weekly_Score"]


def export_excel(seasons_rows, seasons_weeks):
    try:
        import pandas as pd
    except ImportError:
        print("pandas is required for Excel export. Run: pip install -r requirements-excel.txt")
        sys.exit(1)

    print("Preparing multi-year data with rolling averages for Excel export...")
    seasons = sorted(seasons_rows)

    career_rows = []
    for season in seasons:
        career_rows.extend(seasons_rows[season])

    league_averages, week_minmax = build_week_tables(seasons_weeks)
    owner_summary = build_owner_summary(seasons_rows, seasons)
    scoreboard_rows, scoreboard_summary = build_scoreboard(seasons_weeks, seasons)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"fantasy_multi_year_scores_{timestamp}.xlsx"

    with pd.ExcelWriter(filename, engine="openpyxl") as writer:
        pd.DataFrame(career_rows)[SHEET_COLUMNS].to_excel(writer, sheet_name="Career", index=False)
        for season in seasons:
            pd.DataFrame(seasons_rows[season])[SHEET_COLUMNS].to_excel(
                writer, sheet_name=str(season), index=False)

        pd.DataFrame(league_averages).to_excel(writer, sheet_name="League Averages", index=False)
        pd.DataFrame(week_minmax).to_excel(writer, sheet_name="Week MinMax", index=False)
        pd.DataFrame(owner_summary).to_excel(writer, sheet_name="Owner Summary", index=False)
        pd.DataFrame(scoreboard_rows).to_excel(writer, sheet_name="Scoreboard", index=False)
        pd.DataFrame(scoreboard_summary).to_excel(writer, sheet_name="Scoreboard Summary", index=False)

        for sheet in writer.sheets.values():
            for column in sheet.columns:
                width = max((len(str(cell.value)) for cell in column if cell.value is not None), default=0)
                sheet.column_dimensions[column[0].column_letter].width = min(width + 2, 50)

    print(f"✅ Multi-year data with rolling averages exported to Excel: {filename}")
    print(f"📊 Total records: {len(career_rows)}")
    print(f"📋 Seasons included: {', '.join(seasons)}")
    for season in seasons:
        rows = seasons_rows[season]
        weeks = max((r["Week"] for r in rows), default=0)
        print(f"   {season}: {len(rows)} records, {weeks} weeks")
    return filename


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    config = core.load_config()
    all_league_ids = core.league_ids(config)
    url_base = core.base_url(config)
    requested_max = core.weeks_to_fetch(config)
    name_overrides = core.owner_names(config)

    print("🏈 SLEEPER FANTASY FOOTBALL MULTI-YEAR ANALYZER")
    print("=" * 60)
    print(f"Available seasons: {', '.join(sorted(all_league_ids))}")

    seasons_weeks, seasons_rows = {}, {}
    for season in sorted(all_league_ids):
        print(f"\n🏈 Fetching data for {season} season...")
        print(f"   League ID: {all_league_ids[season]}")
        _, weeks = core.fetch_season_weeks(
            season, all_league_ids[season], url_base, requested_max, verbose=True,
            name_overrides=name_overrides)
        if not weeks:
            print(f"   ❌ No completed weeks for {season}")
            continue
        print(f"   ✅ Fetched {len(weeks)} weeks for {season}")
        seasons_weeks[season] = weeks
        seasons_rows[season] = build_season_rows(season, weeks)

    if not seasons_weeks:
        print("❌ No data could be fetched from any season.")
        sys.exit(1)

    print(f"\n✅ Successfully fetched data from {len(seasons_weeks)} seasons")
    print("\n" + "=" * 80)
    print("MULTI-YEAR SEASON SUMMARIES")
    print("=" * 80)
    for season in sorted(seasons_weeks):
        print_season_summary(season, seasons_weeks[season])

    excel_file = export_excel(seasons_rows, seasons_weeks)
    print(f"\n📁 Excel file created: {excel_file}")
    return excel_file


if __name__ == "__main__":
    main()
