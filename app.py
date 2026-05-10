import os
import re
import smtplib
import sqlite3
import logging
from email.mime.text import MIMEText

import requests
from flask import Flask, request, render_template_string
from apscheduler.schedulers.background import BackgroundScheduler

from mlb import get_todays_home_games, is_game_final, had_strikeout_side

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Database (SQLite)
# ---------------------------------------------------------------------------

DB_PATH = os.environ.get("DB_PATH", "cubs_chicken.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS subscribers (
                phone   TEXT PRIMARY KEY,
                gateway TEXT NOT NULL,
                active  INTEGER NOT NULL DEFAULT 1
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS processed_games (
                game_pk TEXT PRIMARY KEY
            )
        """)


def add_subscriber(phone, gateway):
    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO subscribers (phone, gateway, active) VALUES (?, ?, 1)",
            (phone, gateway),
        )


def remove_subscriber(phone):
    with get_db() as db:
        db.execute("UPDATE subscribers SET active = 0 WHERE phone = ?", (phone,))


def get_active_subscribers():
    with get_db() as db:
        rows = db.execute(
            "SELECT phone, gateway FROM subscribers WHERE active = 1"
        ).fetchall()
    return [(row["phone"], row["gateway"]) for row in rows]


def was_game_processed(game_pk):
    with get_db() as db:
        return db.execute(
            "SELECT 1 FROM processed_games WHERE game_pk = ?", (str(game_pk),)
        ).fetchone() is not None


def mark_game_processed(game_pk):
    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO processed_games (game_pk) VALUES (?)", (str(game_pk),)
        )


# ---------------------------------------------------------------------------
# Carrier lookup -> email gateway
# ---------------------------------------------------------------------------

# Substrings matched against the carrier name returned by the lookup API
CARRIER_GATEWAYS = {
    "t-mobile":   "tmomail.net",
    "metro":      "tmomail.net",      # Metro by T-Mobile
    "at&t":       "txt.att.net",
    "att":        "txt.att.net",
    "cricket":    "sms.cricketwireless.net",
    "verizon":    "vtext.com",
    "sprint":     "messaging.sprintpcs.com",
    "boost":      "sms.myboostmobile.com",
    "dish":       "sms.myboostmobile.com",
    "us cellular": "email.uscc.net",
    "uscellular":  "email.uscc.net",
}


def lookup_gateway(digits):
    """
    Calls freecarrierlookup.com to find the carrier, then returns the
    email-to-SMS gateway address (e.g. '9165056940@tmomail.net').

    API docs: https://freecarrierlookup.com  (free account required)
    Expected response: {"success": true, "carrier": "T-Mobile USA", "linetype": "mobile"}
    """
    resp = requests.get(
        "https://freecarrierlookup.com/api_getcarrier.php",
        params={"apikey": os.environ["CARRIER_LOOKUP_API_KEY"], "phoneno": digits},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    carrier_name = data.get("carrier", "").lower()
    for keyword, domain in CARRIER_GATEWAYS.items():
        if keyword in carrier_name:
            return f"{digits}@{domain}"

    raise ValueError(f"Unsupported carrier: {data.get('carrier')!r}")


# ---------------------------------------------------------------------------
# Outbound SMS via carrier email gateways (Gmail SMTP)
# ---------------------------------------------------------------------------

ALERT_BODY = (
    "Cubs struck out the side tonight! Open your Chick-fil-A app NOW "
    "to claim your free reward before it expires!"
)


def send_sms_email(to_gateway, body):
    msg = MIMEText(body)
    msg["From"] = os.environ["GMAIL_ADDRESS"]
    msg["To"] = to_gateway
    msg["Subject"] = ""
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"])
        smtp.send_message(msg)


def send_alerts():
    for phone, gateway in get_active_subscribers():
        try:
            send_sms_email(gateway, ALERT_BODY)
            logger.info(f"Alert sent to {phone}")
        except Exception as e:
            logger.error(f"Failed to send to {phone}: {e}")


# ---------------------------------------------------------------------------
# Game checker (runs on schedule via APScheduler)
# ---------------------------------------------------------------------------


def check_game():
    try:
        games = get_todays_home_games()
    except Exception as e:
        logger.error(f"MLB API error: {e}")
        return

    for game in games:
        game_pk = game["gamePk"]
        if not is_game_final(game) or was_game_processed(game_pk):
            continue
        try:
            if had_strikeout_side(game_pk):
                logger.info(f"Strikeout side in game {game_pk} — sending alerts")
                send_alerts()
            else:
                logger.info(f"Game {game_pk} final, no strikeout side")
        except Exception as e:
            logger.error(f"Error processing game {game_pk}: {e}")
        finally:
            mark_game_processed(game_pk)


# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------

SUBSCRIBE_PAGE = """<!DOCTYPE html>
<html>
<head>
  <title>Cubs Chick-fil-A Alerts</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: sans-serif; max-width: 420px; margin: 60px auto; padding: 0 20px; color: #222; }
    h2   { color: #0E3386; }
    input[type=tel] {
      width: 100%; padding: 10px; font-size: 16px;
      margin: 8px 0 12px; box-sizing: border-box;
      border: 1px solid #ccc; border-radius: 4px;
    }
    button {
      width: 100%; padding: 12px; background: #0E3386; color: #fff;
      border: none; font-size: 16px; border-radius: 4px; cursor: pointer;
    }
    .msg { margin-top: 14px; padding: 12px; border-radius: 4px; }
    .ok  { background: #d4edda; color: #155724; }
    .err { background: #f8d7da; color: #721c24; }
    footer { margin-top: 40px; font-size: 13px; }
  </style>
</head>
<body>
  <h2>Cubs Chick-fil-A Alerts</h2>
  <p>Get a text when a Cubs pitcher strikes out the side at a home game so you
     can claim your free Chick-fil-A reward in time.</p>
  <form method="POST" action="/subscribe">
    <input type="tel" name="phone" placeholder="US phone number" required>
    <button type="submit">Subscribe</button>
  </form>
  {% if message %}
  <div class="msg {{ css }}">{{ message }}</div>
  {% endif %}
  <footer><a href="/unsubscribe">Unsubscribe</a></footer>
</body>
</html>"""

UNSUBSCRIBE_PAGE = """<!DOCTYPE html>
<html>
<head>
  <title>Unsubscribe — Cubs Alerts</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: sans-serif; max-width: 420px; margin: 60px auto; padding: 0 20px; }
    input[type=tel] {
      width: 100%; padding: 10px; font-size: 16px;
      margin: 8px 0 12px; box-sizing: border-box;
      border: 1px solid #ccc; border-radius: 4px;
    }
    button {
      width: 100%; padding: 12px; background: #CC3433; color: #fff;
      border: none; font-size: 16px; border-radius: 4px; cursor: pointer;
    }
    .msg { margin-top: 14px; padding: 12px; border-radius: 4px; background: #d4edda; color: #155724; }
  </style>
</head>
<body>
  <h2>Unsubscribe</h2>
  <form method="POST">
    <input type="tel" name="phone" placeholder="Your phone number" required>
    <button type="submit">Unsubscribe</button>
  </form>
  {% if message %}
  <div class="msg">{{ message }}</div>
  {% endif %}
</body>
</html>"""


def normalize_phone(raw):
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


@app.route("/")
def index():
    return render_template_string(SUBSCRIBE_PAGE, message=None, css=None)


@app.route("/subscribe", methods=["POST"])
def subscribe():
    digits = normalize_phone(request.form.get("phone", ""))
    if not digits:
        return render_template_string(SUBSCRIBE_PAGE, message="Invalid phone number.", css="err")
    try:
        gateway = lookup_gateway(digits)
    except ValueError as e:
        return render_template_string(SUBSCRIBE_PAGE, message=str(e), css="err")
    except Exception:
        return render_template_string(
            SUBSCRIBE_PAGE, message="Carrier lookup failed — try again.", css="err"
        )
    add_subscriber(digits, gateway)
    return render_template_string(SUBSCRIBE_PAGE, message="You're subscribed!", css="ok")


@app.route("/unsubscribe", methods=["GET", "POST"])
def unsubscribe():
    if request.method == "GET":
        return render_template_string(UNSUBSCRIBE_PAGE, message=None)
    digits = normalize_phone(request.form.get("phone", ""))
    if digits:
        remove_subscriber(digits)
    return render_template_string(UNSUBSCRIBE_PAGE, message="You've been unsubscribed.")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

init_db()

scheduler = BackgroundScheduler(daemon=True)
# Runs every 10 minutes during baseball season (April–October)
scheduler.add_job(check_game, "cron", minute="*/10", month="4-10")
scheduler.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
