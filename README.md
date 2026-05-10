# Cubs Chick-fil-A Alert Bot

Sends an email alert when a Cubs pitcher strikes out the side (3 Ks in one inning) at a home game. Chick-fil-A offers a free reward after these games, but only for a short window — this bot catches it so you don't miss it.

People subscribe by entering their email on a small web form. The bot polls the MLB Stats API every 10 minutes during baseball season and emails all subscribers as soon as the game ends.

## How it works

- **MLB Stats API** (free, no key) — checks for completed Cubs home games and play-by-play strikeout data
- **Gmail SMTP** — sends outbound emails; no third-party service needed
- **SQLite** — stores subscribers and which games have already been processed
- **APScheduler** — runs the game check on a cron inside the same process as the web server
- **Fly.io** — hosts everything for free

## Prerequisites

**Gmail App Password** (required to send email):
1. Enable 2-Step Verification on your Google account
2. Go to Google Account → Security → App passwords
3. Create one (name it anything, e.g. "cubs-bot")
4. Save the 16-character password — you'll need it below

## Local development

```bash
pip install -r requirements.txt

cp .env.example .env
# Fill in GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env
# Leave DB_PATH unset or set to a local path

source .env
python app.py
```

The app runs at `http://localhost:8080`. Subscribe via the web form, or insert a row directly into the SQLite file to add yourself without going through the form.

## Deployment on Fly.io

### First deploy

```bash
# Install the Fly CLI: https://fly.io/docs/hands-on/install-flyctl/
fly auth login

# Create the app (say "no" when asked to deploy immediately)
fly launch --name cubs-chicken

# Create a persistent volume for the SQLite database (1 GB, free tier)
fly volumes create cubs_chicken_data --region ord --size 1

fly deploy
```

### Secrets

Never put credentials in the repo. Set them via the Fly CLI — they're stored encrypted and injected as environment variables at runtime:

```bash
fly secrets set \
  GMAIL_ADDRESS=you@gmail.com \
  "GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx" \
  DB_PATH=/data/cubs_chicken.db
```

To view which secrets are set (values are never shown):
```bash
fly secrets list
```

To update a secret later:
```bash
fly secrets set GMAIL_APP_PASSWORD="new value"
```

### Subsequent deploys

```bash
fly deploy
```

### Subscribing yourself

After deploying, open the app URL printed by `fly deploy` and enter your email. Or add yourself directly via the Fly console if you'd rather skip the form:

```bash
fly ssh console
sqlite3 /data/cubs_chicken.db \
  "INSERT INTO subscribers (email, active) VALUES ('you@email.com', 1);"
```
