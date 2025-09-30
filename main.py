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
    print(f"� Total records: {total_records}")
    print(f"📋 Seasons included: {', '.join(sorted(seasons_combined_data.keys()))}")
    
    # Show summary by season
    for year in sorted(seasons_combined_data.keys()):
        season_records = len(seasons_combined_data[year])
        max_week = max(record['Week'] for record in seasons_combined_data[year])
        print(f"   {year}: {season_records} records, {max_week} weeks")
    
    return filename
    """Export weekly scores to Excel format"""
    try:
        import pandas as pd
    except ImportError:
        print("pandas is required for Excel export. Installing...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pandas", "openpyxl"])
        import pandas as pd
    
    print("Preparing data for Excel export...")
    
    # Prepare data for Excel
    excel_data = []
    
    for week, week_data in sorted(weekly_scores.items()):
        for roster_id, team_data in week_data.items():
            excel_data.append({
                'Week': week,
                'Team_Name': team_data['team_name'],
                'Owner_Name': team_data['owner_name'],
                'Points': round(team_data['points'], 2)
            })
    
    # Create DataFrame
    df = pd.DataFrame(excel_data)
    
    # Export to Excel
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"fantasy_weekly_scores_{timestamp}.xlsx"
    
    # Create Excel writer object
    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        # Write main data
        df.to_excel(writer, sheet_name='Weekly Scores', index=False)
        
        # Get the workbook and worksheet
        workbook = writer.book
        worksheet = writer.sheets['Weekly Scores']
        
        # Auto-adjust column widths
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
    
    print(f"✅ Data exported to Excel: {filename}")
    print(f"📊 Total records: {len(excel_data)}")
    
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
