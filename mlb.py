import requests
from datetime import date
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)

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


def _fetch_play_data(game_pk):
    """Return {inning: {pitcher_name: k_count}} for Cubs pitching (top half)."""
    url = f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    feed = resp.json()

    inning_data = defaultdict(lambda: defaultdict(int))
    for play in feed["liveData"]["plays"]["allPlays"]:
        if (
            play["about"]["halfInning"] == "top"
            and play["result"].get("eventType") == "strikeout"
        ):
            inning = play["about"]["inning"]
            pitcher = play["matchup"]["pitcher"]["fullName"]
            inning_data[inning][pitcher] += 1
    return {inn: dict(pitchers) for inn, pitchers in inning_data.items()}


def _fetch_inning_ks(game_pk):
    """Return {inning: total_strikeout_count} for Cubs pitching (top half)."""
    return {inn: sum(p.values()) for inn, p in _fetch_play_data(game_pk).items()}


def had_strikeout_side(game_pk):
    """Return True if any Cubs pitcher struck out 3+ batters in a single inning."""
    return any(k >= 3 for k in _fetch_inning_ks(game_pk).values())


def get_strikeout_side_details(game_pk):
    """Return (triggered, {inning: {pitcher: k_count}}) for innings with 3+ Ks."""
    play_data = _fetch_play_data(game_pk)
    triggering = {
        inn: pitchers
        for inn, pitchers in play_data.items()
        if sum(pitchers.values()) >= 3
    }
    return bool(triggering), triggering


def get_recent_home_games(n=20):
    """Return up to n most-recent completed Cubs home games, newest first."""
    today = date.today()
    start = date(today.year, 3, 1).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    url = (
        f"{MLB_API}/schedule?sportId=1&teamId={CUBS_TEAM_ID}"
        f"&startDate={start}&endDate={end}&gameType=R"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    games = []
    for game_date in data.get("dates", []):
        for game in game_date.get("games", []):
            if (
                game["teams"]["home"]["team"]["id"] == CUBS_TEAM_ID
                and game["status"]["abstractGameState"] == "Final"
            ):
                games.append((game_date["date"], game))

    return list(reversed(games[-n:]))


def get_strikeout_details(game_pk):
    """Return (triggered, inning_ks) where triggered is True if strikeout side occurred."""
    inning_ks = _fetch_inning_ks(game_pk)
    triggered = any(k >= 3 for k in inning_ks.values())
    return triggered, inning_ks
