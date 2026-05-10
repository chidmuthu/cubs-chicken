# Cubs Chick-fil-A Alert Bot

Sends an email alert when a Cubs pitcher strikes out the side (3 Ks in one inning) at a home game. Chick-fil-A offers a free reward after these games, but only for a short window — this bot catches it so you don't miss it.

People subscribe by entering their email on a small web form. The bot polls the MLB Stats API every 10 minutes during baseball season and emails all subscribers as soon as the game ends.

## How it works

- **MLB Stats API** (free, no key) — checks for completed Cubs home games and play-by-play strikeout data
- **Gmail SMTP** — sends outbound emails; no third-party service needed
- **SQLite** — stores subscribers and which games have already been processed
- **APScheduler** — runs the game check on a cron inside the same process as the web server
- **GCP e2-micro** — hosts everything on Google's Always Free tier (no trial, no expiry)

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

## Deployment on GCP e2-micro (free forever)

GCP's Always Free tier includes one e2-micro VM permanently in `us-central1`, `us-east1`, or `us-west1`. No trial period, no credit card charges as long as you stay within the free limits (this app uses a tiny fraction of them).

### 1. Create the VM

In the [GCP Console](https://console.cloud.google.com/compute/instances):

- **Machine type:** e2-micro
- **Region:** us-central1, us-east1, or us-west1 (required for free tier)
- **Boot disk:** Debian 12, 30 GB standard persistent disk
- **Firewall:** check "Allow HTTP traffic" and "Allow HTTPS traffic"

Or with the gcloud CLI:

```bash
gcloud compute instances create cubs-chicken \
  --machine-type=e2-micro \
  --zone=us-central1-a \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-standard \
  --tags=http-server,https-server
```

### 2. Set up the VM

SSH into the instance (via the console browser SSH button or `gcloud compute ssh cubs-chicken`), then:

```bash
sudo apt update && sudo apt install -y python3-pip python3-venv git

git clone <your-repo-url>
cd cubs-chicken

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Secrets

Create a `.env` file directly on the VM — it stays on the server and never goes in the repo:

```bash
cat > /home/<your-user>/cubs-chicken/.env << 'EOF'
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
DB_PATH=/home/<your-user>/cubs-chicken/cubs_chicken.db
PORT=8080
EOF
chmod 600 .env
```

### 4. Run as a systemd service

This keeps the app running and restarts it automatically if the VM reboots:

```bash
sudo nano /etc/systemd/system/cubs-chicken.service
```

Paste:

```ini
[Unit]
Description=Cubs Chick-fil-A Alert Bot
After=network.target

[Service]
User=<your-user>
WorkingDirectory=/home/<your-user>/cubs-chicken
EnvironmentFile=/home/<your-user>/cubs-chicken/.env
ExecStart=/home/<your-user>/cubs-chicken/venv/bin/python app.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Then enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable cubs-chicken
sudo systemctl start cubs-chicken

# Confirm it's running
sudo systemctl status cubs-chicken
```

### 5. Open port 8080

```bash
gcloud compute firewall-rules create allow-cubs-chicken \
  --allow=tcp:8080 \
  --target-tags=http-server
```

The web form is then available at `http://<VM-external-IP>:8080`. Find the external IP in the GCP Console under Compute Engine → VM instances.

### Updating the app

```bash
cd cubs-chicken
git pull
sudo systemctl restart cubs-chicken
```

### Subscribing yourself

Visit `http://<VM-external-IP>:8080` and enter your email, or add yourself directly:

```bash
sqlite3 cubs_chicken.db \
  "INSERT INTO subscribers (email, active) VALUES ('you@email.com', 1);"
```
