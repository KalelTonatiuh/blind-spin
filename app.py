import os
import random
import requests
import urllib3
from flask import Flask, request, jsonify, render_template

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
        r = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True, verify=False)
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
    """
    Scrapes a Bandcamp tag page and extracts the embedded JSON (window.BC_data / data-blob).
    No API key or session token needed — this is exactly what the browser sees.
    """
    genre_entry = random.choice(BC_GENRES)
    genre       = genre_entry["genre"]
    tag         = random.choice(genre_entry["subgenres"])
    page        = random.randint(1, 5)

    url = f"https://bandcamp.com/tag/{tag}?sort_field=date&page={page}"

    bc_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        r = requests.get(url, headers=bc_headers, timeout=15)
        if r.status_code != 200:
            return jsonify({"error": f"bc page returned {r.status_code}", "tag": tag}), 404
        html = r.text
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    # BC embeds all hub data as JSON in a <div data-client-items="..."> or inside a script
    import re, json, html as htmllib

    items = []

    # Try: <div class="discover-result" data-blob="...">
    m = re.search(r'data-blob="([^"]+)"', html)
    if m:
        try:
            blob = json.loads(htmllib.unescape(m.group(1)))
            items = blob.get("items", [])
        except Exception:
            pass

    # Try: embedded JSON in <script> — BC puts hub data as a JS variable
    if not items:
        m = re.search(r'var\s+(?:hub|BC_PAGE_DATA|pagedata)\s*=\s*(\{.+?\});\s*\n', html, re.DOTALL)
        if m:
            try:
                blob = json.loads(m.group(1))
                items = blob.get("items", blob.get("hub", {}).get("items", []))
            except Exception:
                pass

    # Try: data-client-items attribute
    if not items:
        m = re.search(r'data-client-items="([^"]+)"', html)
        if m:
            try:
                items = json.loads(htmllib.unescape(m.group(1)))
            except Exception:
                pass

    if not items:
        # Return a snippet of the HTML so we can see what's actually there
        snippet = re.sub(r'<[^>]+>', ' ', html[:2000]).strip()[:500]
        return jsonify({"error": "could not parse bc page", "tag": tag, "page": page, "snippet": snippet}), 404

    item = random.choice(items)

    artist = item.get("band_name") or item.get("artist") or ""
    title  = item.get("title", "")
    url_   = item.get("tralbum_url") or item.get("item_url") or ""
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
        "url":      url_,
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
