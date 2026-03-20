# Blind Spin

Random music release discovery. Pulls a truly random album/EP from MusicBrainz's 2.8M release catalog using a random offset — no cultural weighting, no chart bias.

## Stack

- **Flask** — serves the page and proxies MB/CAA requests (CORS bypass)
- **MusicBrainz API** — random release-group browse with random offset
- **CoverArtArchive** — album art lookup by MBID
- **Railway** — hosting

## Deploy to Railway

### 1. Push to GitHub

```bash
git init
git add .
git commit -m "init"
gh repo create blind-spin --public --push
```

### 2. Create Railway project

1. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub repo
2. Select your `blind-spin` repo
3. Railway auto-detects Python + Procfile — no config needed
4. Hit Deploy

Your app will be live at `https://blind-spin-production.up.railway.app` (or similar).

### 3. Custom domain (optional)

In Railway: Settings → Domains → Add custom domain.

## Local dev

```bash
pip install -r requirements.txt
python app.py
# open http://localhost:5000
```

## Project structure

```
app.py               Flask app — 3 routes: /, /api/mb/*, /api/caa/*
templates/
    index.html       Single-page UI — all CSS/JS inline
requirements.txt
Procfile             gunicorn for Railway
```

## API routes

| Route | Proxies |
|-------|---------|
| `GET /` | Serves the UI |
| `GET /api/mb/release-groups?offset=N` | `musicbrainz.org/ws/2/release-group?query=*&limit=5&offset=N` |
| `GET /api/mb/release-group/<mbid>` | `musicbrainz.org/ws/2/release-group/<mbid>?inc=tags+genres` |
| `GET /api/caa/release-group/<mbid>` | `coverartarchive.org/release-group/<mbid>` |
