import requests

try:
    r = requests.get("http://localhost:5001/api/matches")
    print(f"Status Code: {r.status_code}")
    
    data = r.json()
    matches = data.get("matches", [])
    print(f"Matches retrieved: {len(matches)}")
    if matches:
        print(f"First match example: {matches[0]['matchId']} - {matches[0]['category']}")
except Exception as e:
    print(f"Error connecting to API: {e}")
