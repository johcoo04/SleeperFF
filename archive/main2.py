import sys
import requests
import json
from datetime import datetime

def load_json(file_path):
    with open(file_path, 'r') as f:
        return json.load(f)

def rosters_response(league_id, base_url):
    data = {}
    try:
        response = requests.get(f"{base_url}/league/{league_id}/rosters")
        response.raise_for_status()  # Raise an error for bad responses
        data = response.json()
        print(f"API response for league {league_id} is successful.")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from API: {e}")
    return data

def users_response(league_id, base_url):
    data = {}
    try:
        response = requests.get(f"{base_url}/league/{league_id}/users")
        response.raise_for_status()  # Raise an error for bad responses
        data = response.json()
        print(f"API response for league {league_id} is successful.")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from API: {e}")
    return data

def matchup_response(league_id, base_url, max_week=17):
    all_scores = {}
    for i in range(1,max_week + 1):
        try:
            response = requests.get(f"{base_url}/league/{league_id}/matchups/{str(i)}")
            response.raise_for_status()  # Raise an error for bad responses
            data = response.json()
            if data:
                all_scores[i] = data
            else:
                print(f"No data found for week {i}.")
        except requests.exceptions.RequestException as e:
            print(f"Error fetching data from API: {e}")
    return all_scores

def get_team_names_mapping(users, rosters):
    """Create a mapping of roster_id to team names and owner info"""
    team_mapping = {}
    
    # Create user_id to user info mapping
    user_info = {}
    for user in users:
        user_info[user['user_id']] = {
            'display_name': user.get('display_name', user.get('username', 'Unknown')),
            'team_name': user.get('metadata', {}).get('team_name', None),
            'username': user.get('username', 'Unknown')
        }
    
    # Map roster_id to team information
    for roster in rosters:
        roster_id = roster['roster_id']
        owner_id = roster.get('owner_id')
        
        if owner_id and owner_id in user_info:
            user = user_info[owner_id]
            team_name = user['team_name'] or user['display_name']
            team_mapping[roster_id] = {
                'team_name': team_name,
                'owner_name': user['display_name'],
                'username': user['username']
            }
        else:
            team_mapping[roster_id] = {
                'team_name': f'Team {roster_id}',
                'owner_name': 'Unknown Owner',
                'username': 'unknown'
            }
    
    return team_mapping

def organize_weekly_scores(matchups_data, team_mapping):
    """Organize matchup data into a readable weekly scores format"""
    weekly_scores = {}
    
    for week, matchups in matchups_data.items():
        weekly_scores[week] = {}
        
        for matchup in matchups:
            roster_id = matchup['roster_id']
            points = matchup.get('points', 0)
            matchup_id = matchup.get('matchup_id')
            
            team_info = team_mapping.get(roster_id, {
                'team_name': f'Team {roster_id}',
                'owner_name': 'Unknown',
                'username': 'unknown'
            })
            
            weekly_scores[week][roster_id] = {
                'team_name': team_info['team_name'],
                'owner_name': team_info['owner_name'],
                'points': points,
                'matchup_id': matchup_id,
                'starters': matchup.get('starters', []),
                'players': matchup.get('players', [])
            }
    
    return weekly_scores

def calculate_season_summary(weekly_scores):
    """Calculate season totals, averages, and standings"""
    team_stats = {}
    
    # Calculate totals for each team
    for week, week_data in weekly_scores.items():
        for roster_id, team_data in week_data.items():
            if roster_id not in team_stats:
                team_stats[roster_id] = {
                    'team_name': team_data['team_name'],
                    'owner_name': team_data['owner_name'],
                    'total_points': 0,
                    'weeks_played': 0,
                    'weekly_scores': []
                }
            
            team_stats[roster_id]['total_points'] += team_data['points']
            team_stats[roster_id]['weeks_played'] += 1
            team_stats[roster_id]['weekly_scores'].append({
                'week': week,
                'points': team_data['points']
            })
    
    # Calculate averages
    for roster_id, stats in team_stats.items():
        if stats['weeks_played'] > 0:
            stats['average_points'] = stats['total_points'] / stats['weeks_played']
        else:
            stats['average_points'] = 0
    
    return team_stats

def display_season_summary(team_stats):
    """Display season summary with rankings"""
    print("\n" + "="*80)
    print("SEASON SUMMARY")
    print("="*80)
    
    # Sort teams by total points
    sorted_teams = sorted(team_stats.items(), 
                         key=lambda x: x[1]['total_points'], 
                         reverse=True)
    
    print(f"{'Rank':<4} {'Team':<25} {'Owner':<20} {'Total Pts':<10} {'Avg Pts':<8} {'Weeks':<5}")
    print("-" * 80)
    
    for i, (roster_id, stats) in enumerate(sorted_teams, 1):
        print(f"{i:<4} {stats['team_name'][:24]:<25} {stats['owner_name'][:19]:<20} "
              f"{stats['total_points']:<10.1f} {stats['average_points']:<8.1f} {stats['weeks_played']:<5}")

def find_highest_lowest_weeks(weekly_scores):
    """Find the highest and lowest scoring weeks across all teams"""
    all_scores = []
    
    for week, week_data in weekly_scores.items():
        for roster_id, team_data in week_data.items():
            all_scores.append({
                'week': week,
                'team_name': team_data['team_name'],
                'owner_name': team_data['owner_name'],
                'points': team_data['points'],
                'roster_id': roster_id
            })
    
    if not all_scores:
        return None, None
    
    # Sort by points
    all_scores.sort(key=lambda x: x['points'], reverse=True)
    
    highest = all_scores[0]
    lowest = all_scores[-1]
    
    return highest, lowest

def get_current_nfl_week(year):
    """Get current NFL week for a given year"""
    try:
        response = requests.get("https://api.sleeper.app/v1/state/nfl")
        nfl_state = response.json()
        
        current_season = nfl_state.get('season', '2024')
        current_week = nfl_state.get('week', 1)
        
        if str(year) == current_season:
            # For current season, use actual current week
            return min(current_week, 17)  # Cap at 17 weeks
        else:
            # For past seasons, assume full 17 weeks
            return 17
    except:
        # Fallback logic
        if year == 2025:
            return 2  # You mentioned 2025 only has 2 weeks
        else:
            return 17  # Past seasons are complete

def fetch_season_data(year, league_id, base_url):
    """Fetch data for a specific season"""
    print(f"\n🏈 Fetching data for {year} season...")
    print(f"   League ID: {league_id}")
    
    # Determine how many weeks to fetch
    max_weeks = get_current_nfl_week(year)
    print(f"   Fetching {max_weeks} weeks of data...")
    
    # Fetch all data for this season
    rosters = rosters_response(league_id, base_url)
    users = users_response(league_id, base_url)
    matchups = matchup_response(league_id, base_url, max_weeks)
    
    if not rosters or not users or not matchups:
        print(f"   ❌ Failed to fetch data for {year}")
        return None
    
    print(f"   ✅ Successfully fetched data for {year}")
    
    # Create team mapping and organize scores
    team_mapping = get_team_names_mapping(users, rosters)
    weekly_scores = organize_weekly_scores(matchups, team_mapping)
    
    return {
        'year': year,
        'weekly_scores': weekly_scores,
        'team_mapping': team_mapping,
        'weeks_fetched': max_weeks
    }

def combine_multi_year_data(season_data_list):
    """Combine data from multiple seasons"""
    combined_weekly_scores = {}
    all_team_mappings = {}
    
    for season_data in season_data_list:
        year = season_data['year']
        weekly_scores = season_data['weekly_scores']
        team_mapping = season_data['team_mapping']
        
        # Add year prefix to weeks to avoid conflicts
        for week, week_data in weekly_scores.items():
            year_week_key = f"{year}_W{week}"
            combined_weekly_scores[year_week_key] = {}

            for roster_id, team_data in week_data.items():
                # Add season info to team data
                team_data_copy = team_data.copy()
                team_data_copy['season'] = year
                team_data_copy['original_week'] = week
                # include stable owner username if available in team_mapping
                team_data_copy['username'] = team_mapping.get(roster_id, {}).get('username', team_data_copy.get('username', 'unknown'))
                combined_weekly_scores[year_week_key][roster_id] = team_data_copy
        
        # Keep track of team mappings (use most recent)
        all_team_mappings.update(team_mapping)
    
    return combined_weekly_scores, all_team_mappings

def calculate_rolling_averages(weekly_scores):
    """Calculate rolling averages for each team by week"""
    rolling_data = {}
    
    # Get all teams and weeks
    all_weeks = sorted(weekly_scores.keys())
    all_teams = set()
    for week_data in weekly_scores.values():
        all_teams.update(week_data.keys())
    
    # Initialize rolling data for each team
    for roster_id in all_teams:
        rolling_data[roster_id] = {
            'team_name': '',
            'owner_name': '',
            'weekly_totals': {},
            'rolling_averages': {}
        }
    
    # Calculate rolling averages week by week
    for week in all_weeks:
        week_data = weekly_scores.get(week, {})
        
        for roster_id in all_teams:
            if roster_id in week_data:
                team_data = week_data[roster_id]
                
                # Store team info (use latest available)
                rolling_data[roster_id]['team_name'] = team_data['team_name']
                rolling_data[roster_id]['owner_name'] = team_data['owner_name']
                
                # Store this week's score
                rolling_data[roster_id]['weekly_totals'][week] = team_data['points']
                
                # Calculate rolling average up to this week
                weeks_played = []
                total_points = 0
                
                for w in all_weeks:
                    if w > week:  # Only include weeks up to current week
                        break
                    if roster_id in weekly_scores.get(w, {}):
                        points = weekly_scores[w][roster_id]['points']
                        weeks_played.append(w)
                        total_points += points
                
                if weeks_played:
                    rolling_avg = total_points / len(weeks_played)
                    rolling_data[roster_id]['rolling_averages'][week] = {
                        'average': rolling_avg,
                        'weeks_included': len(weeks_played),
                        'total_points': total_points
                    }
    
    return rolling_data

def export_multi_year_excel_with_rolling(combined_weekly_scores, team_mapping):
    """Export multi-year data to Excel with rolling averages - one tab per year"""
    try:
        import pandas as pd
    except ImportError:
        print("pandas is required for Excel export. Installing...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pandas", "openpyxl"])
        import pandas as pd
    
    print("Preparing multi-year data with rolling averages for Excel export...")
    
    # Organize data by season
    seasons_combined_data = {}
    
    for year_week_key, week_data in sorted(combined_weekly_scores.items()):
        year = year_week_key.split('_')[0]
        week = int(year_week_key.split('_W')[1])
        
        if year not in seasons_combined_data:
            seasons_combined_data[year] = []
        
        # Get just this season's weekly scores for rolling average calculation
        season_weekly_scores = {}
        for ywk, wk_data in combined_weekly_scores.items():
            if ywk.startswith(year + '_'):
                wk_num = int(ywk.split('_W')[1])
                season_weekly_scores[wk_num] = wk_data
        
        # Calculate rolling averages for this season
        rolling_data = calculate_rolling_averages(season_weekly_scores)
        
        # Combine weekly scores with rolling averages
        for roster_id, team_data in week_data.items():
            rolling_avg = 0
            total_points = 0
            
            if roster_id in rolling_data and week in rolling_data[roster_id]['rolling_averages']:
                rolling_info = rolling_data[roster_id]['rolling_averages'][week]
                rolling_avg = rolling_info['average']
                total_points = rolling_info['total_points']
            
            combined_record = {
                'Season': year,
                'Week': week,
                'Team_Name': team_data['team_name'],
                'Owner_Name': team_data['owner_name'],
                'Username': team_data.get('username', team_data['owner_name']),
                'Rolling_Average': round(rolling_avg, 2),
                'Total_Points': round(total_points, 2),
                'Weekly_Score': round(team_data['points'], 2)
            }
            seasons_combined_data[year].append(combined_record)
    
    # Export to Excel with one tab per year
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"fantasy_multi_year_scores_{timestamp}.xlsx"
    
    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        # Create Career tab with all data combined
        all_career_data = []
        for year in sorted(seasons_combined_data.keys()):
            all_career_data.extend(seasons_combined_data[year])
        
        career_df = pd.DataFrame(all_career_data)
        # Ensure columns are in the exact order requested
        career_df = career_df[['Season', 'Week', 'Team_Name', 'Owner_Name', 'Rolling_Average', 'Total_Points', 'Weekly_Score']]
        career_df.to_excel(writer, sheet_name='Career', index=False)
        
        # Individual season sheets with combined data
        for year in sorted(seasons_combined_data.keys()):
            season_df = pd.DataFrame(seasons_combined_data[year])
            # Ensure columns are in the exact order requested
            season_df = season_df[['Season', 'Week', 'Team_Name', 'Owner_Name', 'Rolling_Average', 'Total_Points', 'Weekly_Score']]
            season_df.to_excel(writer, sheet_name=f'{year}', index=False)
        
        # Create League Averages tab - shows average points scored league-wide each week
        league_averages_data = []
        
        # Calculate league average for each season/week combination
        # Also capture full entries so we can identify the team/owner for min/max
        week_averages = {}
        for year in sorted(seasons_combined_data.keys()):
            for record in seasons_combined_data[year]:
                season_week_key = (record['Season'], record['Week'])

                if season_week_key not in week_averages:
                    week_averages[season_week_key] = {
                        'season': record['Season'],
                        'week': record['Week'],
                        'total_points': 0,
                        'team_count': 0,
                        'scores': [],
                        'entries': []  # Hold dicts with team/owner/score
                    }

                week_averages[season_week_key]['total_points'] += record['Weekly_Score']
                week_averages[season_week_key]['team_count'] += 1
                week_averages[season_week_key]['scores'].append(record['Weekly_Score'])
                week_averages[season_week_key]['entries'].append({
                    'team': record['Team_Name'],
                    'owner': record['Owner_Name'],
                    'score': record['Weekly_Score']
                })

        # Build league averages data and week min/max data (with team/owner)
        week_minmax_data = []
        for (season, week), data in sorted(week_averages.items()):
            league_avg = data['total_points'] / data['team_count'] if data['team_count'] > 0 else 0

            # Identify high and low with team/owner
            entries = data.get('entries', [])
            if entries:
                sorted_entries = sorted(entries, key=lambda x: x['score'], reverse=True)
                high = sorted_entries[0]
                low = sorted_entries[-1]
                high_score = high['score']
                high_team = high['team']
                high_owner = high['owner']
                low_score = low['score']
                low_team = low['team']
                low_owner = low['owner']
            else:
                high_score = low_score = 0
                high_team = high_owner = ''
                low_team = low_owner = ''

            # Append league average record (existing sheet)
            league_averages_data.append({
                'Season': season,
                'Week': week,
                'League_Average': round(league_avg, 2),
                'Teams_Playing': data['team_count'],
                'High_Score': round(high_score, 2),
                'Low_Score': round(low_score, 2),
                'Point_Spread': round(high_score - low_score, 2)
            })

            # Append Week Min/Max record (new sheet)
            week_minmax_data.append({
                'Season': season,
                'Week': week,
                'Teams_Playing': data['team_count'],
                'League_Average': round(league_avg, 2),
                'High_Score': round(high_score, 2),
                'High_Team': high_team,
                'High_Owner': high_owner,
                'Low_Score': round(low_score, 2),
                'Low_Team': low_team,
                'Low_Owner': low_owner,
                'Point_Spread': round(high_score - low_score, 2)
            })

        league_averages_df = pd.DataFrame(league_averages_data)
        league_averages_df.to_excel(writer, sheet_name='League Averages', index=False)

        # Write Week Min/Max sheet (season, week, min/max with team/owner)
        week_minmax_df = pd.DataFrame(week_minmax_data)
        # Ensure sort order is Season then Week
        if not week_minmax_df.empty:
            week_minmax_df = week_minmax_df.sort_values(by=['Season', 'Week'])
        week_minmax_df.to_excel(writer, sheet_name='Week MinMax', index=False)
        
        # Create Owner Summary sheet - aggregate all data by stable username
        owner_aggregates = {}
        for year in sorted(seasons_combined_data.keys()):
            for record in seasons_combined_data[year]:
                username = record.get('Username') or record.get('Owner_Name') or 'unknown'
                # fall back to Owner_Name as display if Username not present
                display_owner = record.get('Owner_Name', username)
                if username not in owner_aggregates:
                    owner_aggregates[username] = {
                        'username': username,
                        'owner_name': display_owner,
                        'latest_team': record['Team_Name'],
                        'career_total_points': 0,
                        'weeks_played': 0,
                        'season_totals': {}
                    }

                # update latest team name (most recent entry wins)
                owner_aggregates[username]['latest_team'] = record['Team_Name']
                owner_aggregates[username]['owner_name'] = display_owner
                owner_aggregates[username]['career_total_points'] += record['Weekly_Score']
                owner_aggregates[username]['weeks_played'] += 1
                owner_aggregates[username]['season_totals'].setdefault(str(record['Season']), 0)
                owner_aggregates[username]['season_totals'][str(record['Season'])] += record['Weekly_Score']

        # Build DataFrame rows
        owner_summary_rows = []
        seasons_list = sorted(seasons_combined_data.keys())
        for username, data in owner_aggregates.items():
            career_avg = data['career_total_points'] / data['weeks_played'] if data['weeks_played'] > 0 else 0
            row = {
                'Username': data['username'],
                'Owner_Name': data['owner_name'],
                'Latest_Team': data['latest_team'],
                'Career_Total_Points': round(data['career_total_points'], 2),
                'Career_Average': round(career_avg, 2),
                'Weeks_Played': data['weeks_played']
            }
            for s in seasons_list:
                row[f'{s}_Total'] = round(data['season_totals'].get(str(s), 0), 2)
            owner_summary_rows.append(row)

        # Sort by career total descending
        owner_summary_rows.sort(key=lambda x: x['Career_Total_Points'], reverse=True)
        owner_summary_df = pd.DataFrame(owner_summary_rows)
        owner_summary_df.to_excel(writer, sheet_name='Owner Summary', index=False)
        
        # Create Scoreboard tab - awards points based on weekly performance
        scoreboard_data = []
        team_scoreboard_totals = {}
        team_rolling_totals = {}  # Track rolling totals for standings (scoreboard points)
        team_rank_history = {}  # Track placement history for each team per season

        # Process each week to award scoreboard points
        for (season, week), data in sorted(week_averages.items()):
            # Get all teams and their scores for this week
            week_teams = []
            for record in seasons_combined_data[str(season)]:
                if record['Week'] == week:
                    week_teams.append({
                        'team_name': record['Team_Name'],
                        'owner_name': record['Owner_Name'],
                        'score': record['Weekly_Score']
                    })
            
            # Sort teams by score (highest to lowest) to determine weekly rank
            week_teams.sort(key=lambda x: x['score'], reverse=True)
            
            # Award points and track rolling totals
            week_rolling_data = []
            
            for i, team in enumerate(week_teams):
                weekly_placement = i + 1  # 1st place, 2nd place, etc.
                points_awarded = 0
                
                if str(season) == '2025':
                    # 2025 rules: Top 2 get 2 points, next 3 get 1 point, bottom 1 gets 0
                    if i < 2:  # Top 2
                        points_awarded = 2
                    elif i < 5:  # Next 3 (positions 2-4, but 0-indexed so 2-4)
                        points_awarded = 1
                    else:  # Bottom 1
                        points_awarded = 0
                else:
                    # 2023/2024 rules: Top 2 get 2 points, next 2 get 1 point, bottom 2 get 0
                    if i < 2:  # Top 2
                        points_awarded = 2
                    elif i < 4:  # Next 2 (positions 2-3, but 0-indexed so 2-3)
                        points_awarded = 1
                    else:  # Bottom 2
                        points_awarded = 0
                
                # Track totals keyed by stable username when available
                team_key = team.get('owner_name')
                # try to get username from team entry if present
                team_username = None
                for rec in seasons_combined_data[str(season)]:
                    if rec['Team_Name'] == team['team_name'] and rec['Owner_Name'] == team['owner_name'] and rec['Week'] == week:
                        team_username = rec.get('Username')
                        break

                if team_username:
                    team_key = team_username

                if team_key not in team_scoreboard_totals:
                    team_scoreboard_totals[team_key] = {
                        'team_name': team['team_name'],  # latest seen team name for display
                        'owner_name': team['owner_name'],
                        'username': team_username or team['owner_name'],
                        'total_points': 0,
                        'weeks_played': 0,
                        'season_totals': {}
                    }
                
                # Initialize rolling totals if needed (keyed by owner/username)
                if team_key not in team_rolling_totals:
                    team_rolling_totals[team_key] = 0

                # Initialize rank history tracking per season
                season_key = f"{team_key}_{season}"
                if season_key not in team_rank_history:
                    team_rank_history[season_key] = []

                team_scoreboard_totals[team_key]['total_points'] += points_awarded
                team_scoreboard_totals[team_key]['weeks_played'] += 1
                team_rolling_totals[team_key] += points_awarded

                # Track this week's placement for rolling average calculation
                team_rank_history[season_key].append(weekly_placement)
                
                # Calculate rolling average rank (average placement over all weeks played this season)
                rolling_avg_rank = sum(team_rank_history[season_key]) / len(team_rank_history[season_key])
                
                # Season-specific totals (owner-keyed)
                if str(season) not in team_scoreboard_totals[team_key]['season_totals']:
                    team_scoreboard_totals[team_key]['season_totals'][str(season)] = 0
                team_scoreboard_totals[team_key]['season_totals'][str(season)] += points_awarded
                
                week_rolling_data.append({
                    'owner_key': team_key,
                    'team_name': team['team_name'],
                    'owner_name': team['owner_name'],
                    'weekly_score': team['score'],
                    'weekly_rank': weekly_placement,
                    'points_awarded': points_awarded,
                    'rolling_total': team_rolling_totals[team_key],
                    'rolling_avg_rank': rolling_avg_rank
                })
            
            # Sort by rolling total to get current standings
            week_rolling_data.sort(key=lambda x: x['rolling_total'], reverse=True)
            
            # Add standings rank and create final records
            for standings_rank, team_data in enumerate(week_rolling_data, 1):
                # Use owner as the persistent identifier; include latest team name for display
                scoreboard_data.append({
                    'Season': season,
                    'Week': week,
                    'Team_Name': team_data['team_name'],
                    'Owner_Name': team_data['owner_name'],
                    'Weekly_Score': round(team_data['weekly_score'], 2),
                    'Points_Awarded': team_data['points_awarded'],
                    'Weekly_Rank': team_data['weekly_rank'],
                    'Rolling_Average_Rank': round(team_data['rolling_avg_rank'], 3),
                    'Current_Standing': standings_rank
                })
        
        # Create main scoreboard sheet
        scoreboard_df = pd.DataFrame(scoreboard_data)
        scoreboard_df.to_excel(writer, sheet_name='Scoreboard', index=False)
        
        # Create scoreboard summary sheet (aggregated by username)
        summary_data = []
        for team_key, totals in team_scoreboard_totals.items():
            summary_record = {
                'Username': totals.get('username', totals.get('owner_name')),
                'Team_Name': totals['team_name'],
                'Owner_Name': totals['owner_name'],
                'Total_Scoreboard_Points': totals['total_points'],
                'Weeks_Played': totals['weeks_played'],
                'Points_Per_Week': round(totals['total_points'] / totals['weeks_played'], 2) if totals['weeks_played'] > 0 else 0
            }

            # Add season-specific totals
            for year in sorted(seasons_combined_data.keys()):
                summary_record[f'{year}_Points'] = totals['season_totals'].get(year, 0)

            summary_data.append(summary_record)
        
        # Sort by total scoreboard points
        summary_data.sort(key=lambda x: x['Total_Scoreboard_Points'], reverse=True)
        
        summary_df = pd.DataFrame(summary_data)
        summary_df.to_excel(writer, sheet_name='Scoreboard Summary', index=False)
        
        # Auto-adjust column widths for all sheets
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
    
    print(f"✅ Multi-year data with rolling averages exported to Excel: {filename}")
    total_records = sum(len(data) for data in seasons_combined_data.values())
    print(f"📊 Total records: {total_records}")
    print(f"📋 Seasons included: {', '.join(sorted(seasons_combined_data.keys()))}")
    
    # Show summary by season
    for year in sorted(seasons_combined_data.keys()):
        season_records = len(seasons_combined_data[year])
        max_week = max(record['Week'] for record in seasons_combined_data[year])
        print(f"   {year}: {season_records} records, {max_week} weeks")
    
    return filename

def main():
    config_file = 'league_data.json'
    league_data = load_json(config_file)
    base_url = league_data.get('api').get('base_url')
    league_ids = league_data.get('league_ids', {})
    
    print("🏈 SLEEPER FANTASY FOOTBALL MULTI-YEAR ANALYZER")
    print("=" * 60)
    print(f"Available seasons: {', '.join(sorted(league_ids.keys()))}")
    
    # Fetch data for all available seasons
    all_season_data = []
    
    for year, league_id in sorted(league_ids.items()):
        season_data = fetch_season_data(int(year), league_id, base_url)
        if season_data:
            all_season_data.append(season_data)
    
    if not all_season_data:
        print("❌ No data could be fetched from any season.")
        sys.exit(1)
    
    print(f"\n✅ Successfully fetched data from {len(all_season_data)} seasons")
    
    # Combine all seasons
    combined_weekly_scores, combined_team_mapping = combine_multi_year_data(all_season_data)
    
    print(f"📊 Total combined records: {sum(len(week_data) for week_data in combined_weekly_scores.values())}")
    
    # Display summary for each season
    print("\n" + "="*80)
    print("MULTI-YEAR SEASON SUMMARIES")
    print("="*80)
    
    for season_data in all_season_data:
        year = season_data['year']
        weekly_scores = season_data['weekly_scores'] 
        weeks_fetched = season_data['weeks_fetched']
        
        print(f"\n--- {year} SEASON ({weeks_fetched} weeks) ---")
        
        team_stats = calculate_season_summary(weekly_scores)
        
        # Sort teams by total points for this season
        sorted_teams = sorted(team_stats.items(), 
                             key=lambda x: x[1]['total_points'], 
                             reverse=True)
        
        print(f"{'Rank':<4} {'Team':<25} {'Owner':<20} {'Total':<8} {'Avg':<6}")
        print("-" * 70)
        
        for i, (roster_id, stats) in enumerate(sorted_teams, 1):
            print(f"{i:<4} {stats['team_name'][:24]:<25} {stats['owner_name'][:19]:<20} "
                  f"{stats['total_points']:<8.1f} {stats['average_points']:<6.1f}")
        
        # Show highest/lowest for this season
        highest, lowest = find_highest_lowest_weeks(weekly_scores)
        if highest and lowest:
            print(f"🏆 High: {highest['team_name']} - Week {highest['week']} - {highest['points']:.1f} pts")
            print(f"💀 Low: {lowest['team_name']} - Week {lowest['week']} - {lowest['points']:.1f} pts")
        
        # Show latest rolling averages for this season
        rolling_data = calculate_rolling_averages(weekly_scores)
        if rolling_data:
            latest_week = max(weekly_scores.keys()) if weekly_scores else 0
            print(f"\n📈 Rolling Averages through Week {latest_week}:")
            
            # Get latest rolling averages for ranking
            latest_averages = []
            for roster_id, team_rolling in rolling_data.items():
                if latest_week in team_rolling['rolling_averages']:
                    avg_info = team_rolling['rolling_averages'][latest_week]
                    latest_averages.append({
                        'team_name': team_rolling['team_name'],
                        'rolling_avg': avg_info['average'],
                        'weeks': avg_info['weeks_included']
                    })
            
            # Sort by rolling average
            latest_averages.sort(key=lambda x: x['rolling_avg'], reverse=True)
            
            for i, team_avg in enumerate(latest_averages[:3], 1):  # Show top 3
                print(f"   {i}. {team_avg['team_name']}: {team_avg['rolling_avg']:.1f} avg ({team_avg['weeks']} weeks)")
        
        # Show detailed weekly scores for 2025 to help debug data accuracy
        if year == 2025:
            print(f"\n🔍 DETAILED 2025 WEEKLY SCORES (for data verification):")
            print(f"{'Team':<25} {'Owner':<20} {'W1':<8} {'W2':<8} {'W3':<8} {'Total':<8}")
            print("-" * 85)
            
            for roster_id, stats in sorted_teams:
                team_name = stats['team_name'][:24]
                owner_name = stats['owner_name'][:19]
                
                # Get weekly scores for this team
                week_scores = {}
                for week_score in stats['weekly_scores']:
                    week_scores[week_score['week']] = week_score['points']
                
                w1 = week_scores.get(1, 0)
                w2 = week_scores.get(2, 0) 
                w3 = week_scores.get(3, 0)
                total = stats['total_points']
                
                print(f"{team_name:<25} {owner_name:<20} {w1:<8.1f} {w2:<8.1f} {w3:<8.1f} {total:<8.1f}")
        
        print()  # Extra spacing between seasons
    
    # Export to Excel
    excel_file = export_multi_year_excel_with_rolling(combined_weekly_scores, combined_team_mapping)
    print(f"\n📁 Excel file created: {excel_file}")
    
    return {
        'all_season_data': all_season_data,
        'combined_weekly_scores': combined_weekly_scores,
        'combined_team_mapping': combined_team_mapping,
        'excel_file': excel_file
    }

if __name__ == "__main__":
    main()