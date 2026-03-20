import os
import random
import requests
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

MB_BASE  = "https://musicbrainz.org/ws/2"
CAA_BASE = "https://coverartarchive.org"
BC_API   = "https://bandcamp.com/api/hub/2/dig_deeper"
BC_TAG_API = "https://bandcamp.com/api/tag/1/releases"

HEADERS = {
    "User-Agent": "BlindSpin/1.0 (https://github.com/KalelTonatiuh/blind-spin; contact@example.com)",
    "Accept": "application/json",
}

# Bandcamp genre/subgenre pool for dig_deeper
BC_GENRES = [
    {"genre": "electronic", "subgenres": ["ambient", "techno", "house", "experimental", "drone", "noise", "industrial", "synth", "idm", "dub"]},
    {"genre": "rock",       "subgenres": ["indie", "punk", "metal", "alternative", "post-rock", "shoegaze", "psychedelic", "garage", "emo", "folk-punk"]},
    {"genre": "metal",      "subgenres": ["black-metal", "death-metal", "doom", "sludge", "grindcore", "post-metal", "thrash", "heavy-metal"]},
    {"genre": "folk",       "subgenres": ["singer-songwriter", "acoustic", "americana", "country", "bluegrass", "celtic"]},
    {"genre": "jazz",       "subgenres": ["jazz", "avant-garde", "free-jazz", "soul-jazz"]},
    {"genre": "classical",  "subgenres": ["contemporary-classical", "minimalism", "orchestral", "chamber"]},
    {"genre": "hip-hop-rap","subgenres": ["hip-hop", "rap", "lo-fi", "instrumental-hip-hop"]},
    {"genre": "r-b-soul",   "subgenres": ["soul", "r-b", "funk", "gospel"]},
    {"genre": "pop",        "subgenres": ["indie-pop", "dream-pop", "synth-pop", "art-pop", "lo-fi-pop"]},
    {"genre": "world",      "subgenres": ["latin", "reggae", "afrobeat", "cumbia", "ska", "dub"]},
]

# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

# ── MB proxy ──────────────────────────────────────────────────────────────────

@app.route("/api/mb/release-groups")
def mb_release_groups():
    offset = request.args.get("offset", 0, type=int)
    url = f"{MB_BASE}/release-group?query=*&limit=5&offset={offset}&fmt=json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        return jsonify(r.json())
    except requests.exceptions.HTTPError as e:
        return jsonify({"error": str(e)}), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 502

@app.route("/api/mb/release-group/<mbid>")
def mb_release_group_detail(mbid):
    inc = request.args.get("inc", "tags+genres")
    url = f"{MB_BASE}/release-group/{mbid}?inc={inc}&fmt=json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        return jsonify(r.json())
    except requests.exceptions.HTTPError as e:
        return jsonify({"error": str(e)}), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 502

# ── CAA proxy ─────────────────────────────────────────────────────────────────

@app.route("/api/caa/release-group/<mbid>")
def caa_release_group(mbid):
    url = f"{CAA_BASE}/release-group/{mbid}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
        if r.status_code == 404:
            return jsonify({"images": []}), 404
        r.raise_for_status()
        return jsonify(r.json())
    except requests.exceptions.HTTPError as e:
        return jsonify({"error": str(e)}), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 502

# ── Bandcamp proxy ────────────────────────────────────────────────────────────

@app.route("/api/bc/random")
def bc_random():
    genre_entry = random.choice(BC_GENRES)
    genre       = genre_entry["genre"]
    tag         = random.choice(genre_entry["subgenres"])
    page        = random.randint(1, 8)

    bc_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Referer": f"https://bandcamp.com/tag/{tag}",
        "Origin": "https://bandcamp.com",
    }

    payload = {
        "tag_norm_name": tag,
        "page":          page,
        "sort_field":    "date",
        "format":        "music",
        "include_result_types": ["a", "t"],  # albums and tracks
    }

    try:
        r = requests.post(BC_TAG_API, json=payload, headers=bc_headers, timeout=12)
        data = r.json()
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    # BC returns 200 even on errors — check for error payload
    if "error" in data and "items" not in data and "releases" not in data:
        return jsonify({
            "error": "bc rejected request",
            "bc_error": data.get("error_message", data.get("error", "unknown")),
            "tag": tag, "page": page
        }), 404

    items = data.get("items") or data.get("releases") or data.get("results") or []
    if not items:
        return jsonify({"error": "no items", "tag": tag, "page": page, "keys": list(data.keys())}), 404

    item = random.choice(items)

    artist = item.get("band_name") or item.get("artist") or ""
    title  = item.get("title", "")
    url    = item.get("tralbum_url") or item.get("item_url") or ""
    art    = item.get("art_url") or ""
    if art and "_10." in art:
        art = art.replace("_10.", "_16.")

    tags = []
    for t in item.get("tags", []):
        if isinstance(t, str):
            tags.append(t)
        elif isinstance(t, dict):
            tags.append(t.get("norm_name") or t.get("name") or "")
    tags = [t for t in tags if t]

    release_date = item.get("release_date") or ""
    year = str(release_date)[:4] if release_date else ""

    return jsonify({
        "source":   "bandcamp",
        "artist":   artist,
        "title":    title,
        "url":      url,
        "cover":    art,
        "tags":     tags[:10],
        "year":     year,
        "type":     item.get("type", "album"),
        "genre":    genre,
        "subgenre": tag,
    })

# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
