import requests
from datetime import date
from collections import defaultdict

MLB_API = "https://statsapi.mlb.com/api/v1"
CUBS_TEAM_ID = 112


def get_todays_home_games():
    today = date.today().strftime("%Y-%m-%d")
    url = f"{MLB_API}/schedule?sportId=1&teamId={CUBS_TEAM_ID}&date={today}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    return [
        game
        for game_date in data.get("dates", [])
        for game in game_date.get("games", [])
        if game["teams"]["home"]["team"]["id"] == CUBS_TEAM_ID
    ]


def is_game_final(game):
    return game["status"]["abstractGameState"] == "Final"


def had_strikeout_side(game_pk):
    """Return True if any Cubs pitcher struck out 3+ batters in a single inning."""
    url = f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    feed = resp.json()

    # Cubs are home, so they pitch in the "top" half (away team bats)
    inning_ks = defaultdict(int)
    for play in feed["liveData"]["plays"]["allPlays"]:
        if (
            play["about"]["halfInning"] == "top"
            and play["result"].get("eventType") == "strikeout"
        ):
            inning_ks[play["about"]["inning"]] += 1

    return any(k >= 3 for k in inning_ks.values())
