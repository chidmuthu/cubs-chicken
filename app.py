import os
import smtplib
import logging
from email.mime.text import MIMEText

from flask import Flask, request, render_template_string, redirect, url_for
from google.cloud import firestore

from mlb import get_strikeout_side_details, get_recent_home_games, get_strikeout_details

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
db = firestore.Client()

# ---------------------------------------------------------------------------
# Database (Firestore)
# ---------------------------------------------------------------------------

def add_subscriber(email):
    db.collection("subscribers").document(email).set({"active": True})


def remove_subscriber(email):
    db.collection("subscribers").document(email).update({"active": False})


def get_active_subscribers():
    docs = db.collection("subscribers").where("active", "==", True).stream()
    return [doc.id for doc in docs]


def is_active_subscriber(email):
    doc = db.collection("subscribers").document(email).get()
    return doc.exists and doc.to_dict().get("active", False)


def was_game_processed(game_pk):
    return db.collection("processed_games").document(str(game_pk)).get().exists


def mark_game_processed(game_pk, game_date="", opponent="", triggered=False, inning_details=None):
    db.collection("processed_games").document(str(game_pk)).set({
        "game_pk": game_pk,
        "game_date": game_date,
        "opponent": opponent,
        "strikeout_side": triggered,
        "inning_details": {str(inn): pitchers for inn, pitchers in (inning_details or {}).items()},
        "processed_at": firestore.SERVER_TIMESTAMP,
    })


# ---------------------------------------------------------------------------
# Email alerts (Gmail SMTP)
# ---------------------------------------------------------------------------

ALERT_SUBJECT = "Cubs Strikeout the Side!"
TEST_EMAIL = "chid.muthu@gmail.com"


def _ordinal(n):
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(
        n % 10 if n % 100 not in (11, 12, 13) else 0, "th"
    )
    return f"{n}{suffix}"


def build_alert_body(inning_details, opponent):
    detail_lines = []
    for inning in sorted(inning_details):
        pitchers = inning_details[inning]
        total_ks = sum(pitchers.values())
        inn_str = _ordinal(inning)
        if len(pitchers) == 1:
            pitcher = list(pitchers)[0]
            detail_lines.append(
                f"{pitcher} struck out {total_ks} in the {inn_str} inning vs. the {opponent}."
            )
        else:
            breakdown = ", ".join(f"{p} ({k}K)" for p, k in pitchers.items())
            detail_lines.append(
                f"Cubs pitchers combined for {total_ks} strikeouts in the {inn_str} inning "
                f"vs. the {opponent} ({breakdown})."
            )
    details = "\n".join(detail_lines)
    return (
        "A Cubs pitcher struck out the side tonight (3 strikeouts in one inning "
        "at a home game) — that triggers the Chick-fil-A free reward!\n\n"
        f"{details}\n\n"
        "Open the Chick-fil-A app NOW and claim your reward before it expires. "
        "The window is short, so don't wait."
    )


def send_email(to_addr, subject, body):
    msg = MIMEText(body)
    msg["From"] = os.environ["GMAIL_ADDRESS"]
    msg["To"] = to_addr
    msg["Subject"] = subject
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"])
        smtp.send_message(msg)


def send_alerts(inning_details, opponent):
    body = build_alert_body(inning_details, opponent)
    subscribers = get_active_subscribers()
    logger.info(f"Sending alerts to {len(subscribers)} subscriber(s): {subscribers}")
    for email in subscribers:
        try:
            send_email(email, ALERT_SUBJECT, body)
            logger.info(f"Alert sent to {email}")
        except Exception as e:
            logger.error(f"Failed to send to {email}: {e}")


def send_process_notification(game_pk, game_date, opponent, triggered, inning_details):
    status = "YES — STRIKEOUT SIDE TRIGGERED" if triggered else "No strikeout side"
    if inning_details:
        detail_lines = []
        for inning in sorted(inning_details):
            pitchers = inning_details[inning]
            total_ks = sum(pitchers.values())
            breakdown = ", ".join(f"{p}: {k}K" for p, k in pitchers.items())
            detail_lines.append(f"  Inning {inning}: {total_ks} Ks ({breakdown})")
        details = "\n".join(detail_lines)
    else:
        details = "  No triggering innings"
    body = (
        f"Game processed: {game_date} vs {opponent} (pk={game_pk})\n"
        f"Result: {status}\n\n"
        f"Inning details:\n{details}"
    )
    try:
        send_email(TEST_EMAIL, f"Cubs Bot — Processed: {game_date} vs {opponent}", body)
        logger.info(f"Process notification sent to {TEST_EMAIL}")
    except Exception as e:
        logger.error(f"Failed to send process notification: {e}")


# ---------------------------------------------------------------------------
# Game checker (called by Cloud Scheduler via POST /check)
# ---------------------------------------------------------------------------

def check_game():
    try:
        recent = get_recent_home_games(1)
    except Exception as e:
        logger.error(f"MLB API error fetching recent home games: {e}")
        return False

    if not recent:
        logger.info("No completed Cubs home games found")
        return False

    game_date, game = recent[0]
    game_pk = game["gamePk"]
    opponent = game["teams"]["away"]["team"]["name"]
    logger.info(f"Most recent completed home game: {game_date} vs {opponent} (pk={game_pk})")

    if was_game_processed(game_pk):
        logger.info(f"Game {game_pk} already processed, skipping")
        return False

    logger.info(f"Game {game_pk} not yet processed — fetching strikeout data")
    triggered = False
    inning_details = {}
    try:
        triggered, inning_details = get_strikeout_side_details(game_pk)
        logger.info(f"Game {game_pk}: strikeout_side={triggered}, inning_details={inning_details}")
        if triggered:
            logger.info(f"Strikeout side confirmed — sending alerts to subscribers")
            send_alerts(inning_details, opponent)
        else:
            logger.info(f"Game {game_pk} vs {opponent}: no strikeout side, no alerts sent")
    except Exception as e:
        logger.error(f"Error fetching strikeout data for game {game_pk}: {e}")
    finally:
        mark_game_processed(game_pk, game_date, opponent, triggered, inning_details)
        send_process_notification(game_pk, game_date, opponent, triggered, inning_details)

    return True


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
  <p>When a Cubs pitcher strikes out the side Chick-fil-A offers a free reward in their app.</p>
  <p>Subscribe and you'll get an email the moment the game ends. Open the
     Chick-fil-A app right away and claim the reward before it expires!</p>
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


@app.route("/subscribe", methods=["GET", "POST"])
def subscribe():
    if request.method == "GET":
        return redirect(url_for("index"))
    email = request.form.get("email", "").strip().lower()
    if not email:
        return render_template_string(SUBSCRIBE_PAGE, message="Please enter a valid email.", css="err")
    if is_active_subscriber(email):
        return render_template_string(SUBSCRIBE_PAGE, message=f"{email} is already subscribed.", css="ok")
    add_subscriber(email)
    return render_template_string(SUBSCRIBE_PAGE, message=f"{email} is subscribed!", css="ok")


@app.route("/unsubscribe", methods=["GET", "POST"])
def unsubscribe():
    if request.method == "GET":
        return render_template_string(UNSUBSCRIBE_PAGE, message=None)
    email = request.form.get("email", "").strip().lower()
    if not email:
        return render_template_string(UNSUBSCRIBE_PAGE, message="Please enter a valid email.")
    remove_subscriber(email)
    return render_template_string(UNSUBSCRIBE_PAGE, message=f"{email} has been unsubscribed.")


@app.route("/test", methods=["POST"])
def test_check():
    """Send a diagnostic email summarizing the last 20 Cubs home games."""
    try:
        recent = get_recent_home_games(20)
    except Exception as e:
        logger.error(f"MLB API error during /test: {e}")
        return f"MLB API error: {e}", 500

    lines = [f"Cubs strikeout-side check — last {len(recent)} completed home games\n"]
    for game_date, game in recent:
        game_pk = game["gamePk"]
        away_team = game["teams"]["away"]["team"]["name"]
        try:
            triggered, inning_ks = get_strikeout_details(game_pk)
        except Exception as e:
            lines.append(f"{game_date} vs {away_team} (pk={game_pk}): ERROR — {e}\n")
            continue

        status = "YES — STRIKEOUT SIDE" if triggered else "no"
        ks_summary = ", ".join(
            f"inn {inn}: {k}K" for inn, k in sorted(inning_ks.items())
        ) or "0 strikeouts"
        lines.append(f"{game_date} vs {away_team} (pk={game_pk}): {status}\n  {ks_summary}\n")

    body = "\n".join(lines)
    try:
        send_email(TEST_EMAIL, "Cubs Bot — Test Results", body)
        logger.info(f"Test email sent to {TEST_EMAIL}")
    except Exception as e:
        logger.error(f"Failed to send test email: {e}")
        return f"Email error: {e}", 500

    return f"Test email sent to {TEST_EMAIL}", 200


@app.route("/check", methods=["POST"])
def check():
    """Called by Cloud Scheduler every 10 minutes during baseball season."""
    processed = check_game()
    return ("", 200) if processed else ("", 204)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
