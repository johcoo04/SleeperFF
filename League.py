from sleeper_wrapper import League
import pandas as pd
import warnings

def getAllTeamScores(dataScores):
    team_stats = {}  # Initialize a dictionary to store scores by team
    team_totals = {}  # To keep track of total score per team
    team_counts = {}  # To keep track of the number of weeks per team
    
    # Iterate through each week and match
    for week, matches in dataScores.items():
        for match_id, teams in matches.items():
            for team, score in teams:
                if team not in team_stats:
                    team_stats[team] = []  # Initialize a list for each team
                    team_totals[team] = 0  # Initialize the total score for each team
                    team_counts[team] = 0  # Initialize the count of weeks for each team
                
                team_totals[team] += score
                team_counts[team] += 1
                average_score = team_totals[team] / team_counts[team]
                # Append the score for the team along with the week
                team_stats[team].append([week, score, average_score])
                
    return team_stats



def getTeamScores(dataScores, teamName):
    teamStats = {}
    # Iterate through each week and match
    for week, matches in dataScores.items():
        for teams in matches.items():
            for team, score in teams:
                if teamName in team:
                    if week not in teamStats:
                        teamStats[week] = []
                    teamStats[week].append((score))
    return teamStats

def export_to_excel(team_stats, filename='team_scores_by_team.xlsx'):
    # Prepare a list to hold the flattened data
    all_team_stats = []

    # Flatten the team_stats dictionary into rows for Excel
    for team, scores in team_stats.items():
        for week, score, average_score in scores:
            all_team_stats.append([team, week, score, average_score])  # Add the team, week, score, and average score

    # Create a DataFrame
    df = pd.DataFrame(all_team_stats, columns=['Team', 'Week', 'Score', 'Rolling Average'])

    # Export to Excel
    df.to_excel(filename, index=False)
    
    print(f"Data exported to {filename}")


def main():
    # Suppress all warnings
    warnings.filterwarnings("ignore")

    # creates the league object and stores its basic data
    league = League("1124846364065288192") #2024

    #league = League("1004576507286630400") # 2023
    seasonYear = 2024

    rosters = league.get_rosters()
    users = league.get_users()
    
    # Initialize an empty dictionary to store all scores for 17 weeks
    all_scores = {}

    # Loop through all weeks (1 - x where I set x) and store the scoreboard for each week in the dictionary
    for week in range(1, 15):
        matchups = league.get_matchups(week=week)
        scoreboards = league.get_scoreboards(rosters=rosters, matchups=matchups, users=users, score_type="pts_ppr", season=seasonYear, week=week)
        all_scores[week] = scoreboards  # Store the data for each week in the dictionary
    

    #Get team scores
    scores = getAllTeamScores(all_scores)

    # Export all team scores to Excel
    export_to_excel(scores, 'all_team_scores2024.xlsx')

    # Team to search for
    # DannyTeamName = "Jack’s iPhone"
    # ChaseTeamName = "SillyG00SE69"
    # CarlTeamName = "Ruggs Taxi Service"
    # JoeTeamName = "Mayor Cuomo’s Fury"
    # KyleTeamName2023 = "Gatorsby90"
    # KyleTeamName2024 = "Josephine's Retribution"
    # JackTeamName = "The Hurricoon"
    
    # Export team scores to Excel
    #export_to_excel(team_scores, 'jacks_iphone_scores.xlsx')

# This ensures the script runs the main function only when executed directly
if __name__ == "__main__":
    main()