import sys
import requests
import json

def load_json(file_path):
    with open(file_path, 'r') as f:
        return json.load(f)

def rosters_response(league_id, base_url):
    print(f"Testing API response for league ID: {league_id}")
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
    print(f"Testing API response for league ID: {league_id}")
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
    print(f"Testing API response for league ID: {league_id}")
    for i in range(1,max_week + 1):
        try:
            print(f"Testing API response for week: {str(i)}")
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

def main():
    config_file = 'league_data.json'
    league_data = load_json(config_file)
    year = 2024
    league_id = league_data.get('league_ids').get(str(year))
    base_url = league_data.get('api').get('base_url')
    rosters = rosters_response(league_id, base_url)
    users = users_response(league_id, base_url)
    matchups = matchup_response(league_id, base_url)
    if not rosters or not users or not matchups:
        print("One or more API responses are empty. Please check the API endpoints.")
        sys.exit(1)  # Error Code return
    
    print("All API responses are successful. Proceeding with data processing.")

if __name__ == "__main__":
    main()
