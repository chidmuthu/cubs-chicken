import os
import smtplib
import sqlite3
import logging
from email.mime.text import MIMEText

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
                email  TEXT PRIMARY KEY,
                active INTEGER NOT NULL DEFAULT 1
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS processed_games (
                game_pk TEXT PRIMARY KEY
            )
        """)


def add_subscriber(email):
    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO subscribers (email, active) VALUES (?, 1)", (email,)
        )


def remove_subscriber(email):
    with get_db() as db:
        db.execute("UPDATE subscribers SET active = 0 WHERE email = ?", (email,))


def get_active_subscribers():
    with get_db() as db:
        rows = db.execute(
            "SELECT email FROM subscribers WHERE active = 1"
        ).fetchall()
    return [row["email"] for row in rows]


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
# Email alerts (Gmail SMTP)
# ---------------------------------------------------------------------------

ALERT_SUBJECT = "Cubs Chick-fil-A Alert"
ALERT_BODY = (
    "Cubs struck out the side tonight! Open your Chick-fil-A app NOW "
    "to claim your free reward before it expires!"
)


def send_email(to_addr, subject, body):
    msg = MIMEText(body)
    msg["From"] = os.environ["GMAIL_ADDRESS"]
    msg["To"] = to_addr
    msg["Subject"] = subject
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"])
        smtp.send_message(msg)


def send_alerts():
    for email in get_active_subscribers():
        try:
            send_email(email, ALERT_SUBJECT, ALERT_BODY)
            logger.info(f"Alert sent to {email}")
        except Exception as e:
            logger.error(f"Failed to send to {email}: {e}")


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
    input[type=email] {
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
  <p>Get an email when a Cubs pitcher strikes out the side at a home game so you
     can claim your free Chick-fil-A reward in time.</p>
  <form method="POST" action="/subscribe">
    <input type="email" name="email" placeholder="your@email.com" required>
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
    input[type=email] {
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
    <input type="email" name="email" placeholder="your@email.com" required>
    <button type="submit">Unsubscribe</button>
  </form>
  {% if message %}
  <div class="msg">{{ message }}</div>
  {% endif %}
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(SUBSCRIBE_PAGE, message=None, css=None)


@app.route("/subscribe", methods=["POST"])
def subscribe():
    email = request.form.get("email", "").strip().lower()
    if not email:
        return render_template_string(SUBSCRIBE_PAGE, message="Invalid email.", css="err")
    add_subscriber(email)
    return render_template_string(SUBSCRIBE_PAGE, message="You're subscribed!", css="ok")


@app.route("/unsubscribe", methods=["GET", "POST"])
def unsubscribe():
    if request.method == "GET":
        return render_template_string(UNSUBSCRIBE_PAGE, message=None)
    email = request.form.get("email", "").strip().lower()
    if email:
        remove_subscriber(email)
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
