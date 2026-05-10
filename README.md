# Cubs Chick-fil-A Alert Bot

Sends an email alert when a Cubs pitcher strikes out the side (3 Ks in one inning) at a home game. Chick-fil-A offers a free reward after these games, but only for a short window — this bot catches it so you don't miss it.

People subscribe by entering their email on a small web form. The bot polls the MLB Stats API every 10 minutes during baseball season and emails all subscribers as soon as the game ends.

## How it works

- **MLB Stats API** (free, no key) — checks for completed Cubs home games and play-by-play strikeout data
- **Gmail SMTP** — sends outbound alerts; no third-party service needed
- **Firestore** — stores subscribers and which games have already been processed
- **Cloud Scheduler** — calls `POST /check` every 10 minutes during baseball season
- **Cloud Run** — hosts the web form and game checker; scales to zero, no VM to manage

Everything runs on GCP's Always Free tier.

## Prerequisites

**Gmail App Password** (required to send email):
1. Enable 2-Step Verification on your Google account
2. Go to Google Account → Security → App passwords
3. Create one (name it anything, e.g. "cubs-bot")
4. Save the 16-character password — you'll need it below

## Local development

```bash
pip install -r requirements.txt

# Authenticate with GCP so Firestore works locally
gcloud auth application-default login

export GMAIL_ADDRESS=you@gmail.com
export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"

python app.py
```

The app runs at `http://localhost:8080`.

## Deployment on GCP (free forever)

All services used — Cloud Run, Firestore, Cloud Scheduler, Secret Manager — are within GCP's Always Free tier at this scale.

### 1. One-time project setup

```bash
# Set your project
gcloud config set project YOUR_PROJECT_ID

# Enable required APIs
gcloud services enable \
  run.googleapis.com \
  firestore.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com

# Create Firestore database (only needed once per project)
gcloud firestore databases create --location=nam5
```

### 2. Store secrets

Secrets live in Secret Manager — never in the repo or in plaintext config.

```bash
echo -n "you@gmail.com" | \
  gcloud secrets create gmail-address --data-file=-

echo -n "xxxx xxxx xxxx xxxx" | \
  gcloud secrets create gmail-app-password --data-file=-
```

### 3. Grant Secret Manager access to Cloud Run

Cloud Run runs as the default compute service account, which needs explicit permission to read secrets. Get your project number first:

```bash
gcloud projects describe YOUR_PROJECT_ID --format="value(projectNumber)"
```

Then grant the role:

```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:YOUR_PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### 4. Deploy

```bash
gcloud run deploy cubs-chicken \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets=GMAIL_ADDRESS=gmail-address:latest,GMAIL_APP_PASSWORD=gmail-app-password:latest
```

`--source .` builds and deploys in one step — no Docker required. The command prints the service URL when done (e.g. `https://cubs-chicken-xxx-uc.a.run.app`).

### 5. Create the Cloud Scheduler job

```bash
SERVICE_URL=$(gcloud run services describe cubs-chicken \
  --region us-central1 \
  --format="value(status.url)")

gcloud scheduler jobs create http cubs-chicken-checker \
  --schedule="*/10 * * 4-10 *" \
  --uri="${SERVICE_URL}/check" \
  --http-method=POST \
  --location=us-central1
```

This fires every 10 minutes during April–October. The handler exits immediately if there's no home game or the game isn't final yet.

### Subscribing yourself

Visit the service URL and enter your email. Or add yourself directly via the Firestore console at `console.cloud.google.com/firestore` — create a document in the `subscribers` collection with your email as the document ID and `{"active": true}`.

### Testing the MLB logic

`POST /test` emails `chid.muthu@gmail.com` a summary of the last 20 completed Cubs home games — whether each one triggered the strikeout-side condition and the per-inning strikeout counts. Nothing is written to Firestore. Useful for verifying the MLB API integration after a code change:

```bash
curl -X POST "${SERVICE_URL}/test"
```

---

## Redeploying after code changes

Redeploy with the same source command — Cloud Run keeps all existing configuration (secrets, env vars) so you don't need to pass them again:

```bash
gcloud run deploy cubs-chicken --source . --region us-central1
```

The command builds a new container image, pushes it to Artifact Registry, and updates the Cloud Run service in place. Existing subscribers and processed games in Firestore are unaffected. The command prints the service URL when done.

**What requires a redeploy:**
- Any change to `app.py`, `mlb.py`, or other Python files
- Adding or removing packages in `requirements.txt`

**What does NOT require a redeploy:**
- Rotating secrets (see "Updating secrets" below) — changes take effect on the next request automatically

## Updating secrets

To rotate the Gmail App Password:

```bash
echo -n "new app password" | \
  gcloud secrets versions add gmail-app-password --data-file=-
```

Secret Manager updates take effect on the next Cloud Run request — no redeploy needed.
