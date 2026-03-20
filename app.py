import os
import random
import requests
import urllib3
from flask import Flask, request, jsonify, render_template

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

MB_BASE      = "https://musicbrainz.org/ws/2"
CAA_BASE     = "https://coverartarchive.org"
DISCOGS_BASE = "https://api.discogs.com"

DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN", "")
DISCOGS_TOTAL = 15000000  # ~15M releases in Discogs

HEADERS = {
    "User-Agent": "BlindSpin/1.0 (https://github.com/KalelTonatiuh/blind-spin; contact@example.com)",
    "Accept": "application/json",
}

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
        r = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        r.raise_for_status()
        return jsonify(r.json())
    except requests.exceptions.HTTPError as e:
        return jsonify({"error": str(e)}), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 502

@app.route("/api/mb/release-group/<mbid>")
def mb_release_group_detail(mbid):
    # Hardcode inc to avoid URL encoding issues with +
    url = f"{MB_BASE}/release-group/{mbid}?inc=tags%2Bgenres&fmt=json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=6, verify=False)
        r.raise_for_status()
        return jsonify(r.json())
    except requests.exceptions.HTTPError as e:
        return jsonify({"error": str(e)}), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 502

# ── CAA proxy ─────────────────────────────────────────────────────────────────

@app.route("/api/caa/release-group/<mbid>")
def caa_release_group(mbid):
    # Try HTTPS then HTTP (Railway has SSL issues with CAA)
    for scheme in ("https", "http"):
        url = f"{scheme}://coverartarchive.org/release-group/{mbid}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True, verify=False)
            if r.status_code == 404:
                return jsonify({"images": []}), 404
            if r.status_code == 200:
                return jsonify(r.json())
        except Exception:
            continue
    return jsonify({"images": []}), 404

# ── Discogs image fallback for MB results ─────────────────────────────────────

@app.route("/api/discogs/cover")
def discogs_cover():
    """
    Given artist + title, search Discogs and return the first cover image URL.
    Used as fallback when CAA has no art for a MB release.
    """
    artist = request.args.get("artist", "")
    title  = request.args.get("title", "")
    if not artist or not title:
        return jsonify({"cover": None}), 200

    if not DISCOGS_TOKEN:
        return jsonify({"cover": None}), 200

    dg_headers = {
        "User-Agent": "BlindSpin/1.0 (https://github.com/KalelTonatiuh/blind-spin)",
        "Authorization": f"Discogs token={DISCOGS_TOKEN}",
        "Accept": "application/json",
    }

    try:
        r = requests.get(
            f"{DISCOGS_BASE}/database/search",
            headers=dg_headers,
            params={"q": f"{artist} {title}", "type": "release", "per_page": 3, "page": 1},
            timeout=10,
            verify=False,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        for item in results:
            img = item.get("cover_image") or item.get("thumb")
            if img and "spacer" not in img:
                return jsonify({"cover": img})
    except Exception:
        pass

    return jsonify({"cover": None})

# ── Discogs proxy ─────────────────────────────────────────────────────────────

@app.route("/api/discogs/random")
def discogs_random():
    """
    Picks a random release from Discogs using a random page offset.
    Accepts optional filter params: genre, year_from, year_to, country, format.
    """
    if not DISCOGS_TOKEN:
        return jsonify({"error": "DISCOGS_TOKEN not set"}), 500

    dg_headers = {
        "User-Agent": "BlindSpin/1.0 (https://github.com/KalelTonatiuh/blind-spin)",
        "Authorization": f"Discogs token={DISCOGS_TOKEN}",
        "Accept": "application/json",
    }

    # Read optional filters
    f_genre    = request.args.get("genre", "")
    f_year_from = request.args.get("year_from", "")
    f_year_to   = request.args.get("year_to", "")
    f_country  = request.args.get("country", "")
    f_format   = request.args.get("format", "")

    # Build year range query string for Discogs
    year_q = ""
    if f_year_from and f_year_to:
        year_q = f"{f_year_from}-{f_year_to}"
    elif f_year_from:
        year_q = f"{f_year_from}-2025"
    elif f_year_to:
        year_q = f"1900-{f_year_to}"

    # With filters, page range is smaller since result set is narrower
    has_filters = any([f_genre, year_q, f_country, f_format])
    page = random.randint(1, 200 if has_filters else 5000)

    search_url = f"{DISCOGS_BASE}/database/search"
    params = {
        "type":     "release",
        "per_page": 10,
        "page":     page,
    }

    # Apply filters
    if f_genre:   params["genre"]   = f_genre
    if year_q:    params["year"]    = year_q
    if f_country: params["country"] = f_country
    if f_format:  params["format"]  = f_format
    if not f_format:
        params["format"] = "album"  # default to albums/EPs when no format filter

    try:
        r = requests.get(search_url, headers=dg_headers, params=params, timeout=12, verify=False)
        if r.status_code == 404:
            params["page"] = random.randint(1, 50 if has_filters else 500)
            r = requests.get(search_url, headers=dg_headers, params=params, timeout=12, verify=False)
        if r.status_code == 429:
            return jsonify({"error": "discogs rate limited"}), 429
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    results = data.get("results", [])
    if not results:
        return jsonify({"error": "no results", "page": page}), 404

    item = random.choice(results)

    # Parse artist/title — Discogs title field is usually "Artist - Title"
    raw_title = item.get("title", "")
    if " - " in raw_title:
        artist, title = raw_title.split(" - ", 1)
    else:
        artist = ""
        title  = raw_title

    year   = str(item.get("year", ""))
    genres = item.get("genre", [])
    styles = item.get("style", [])
    tags   = list(dict.fromkeys(genres + styles))[:10]  # dedupe, genres first
    cover  = item.get("cover_image") or item.get("thumb") or ""
    url_   = f"https://www.discogs.com{item['uri']}" if item.get("uri") else ""
    fmt    = (item.get("format") or [""])[0] if item.get("format") else ""

    return jsonify({
        "source": "discogs",
        "artist": artist.strip(),
        "title":  title.strip(),
        "url":    url_,
        "cover":  cover,
        "tags":   tags,
        "year":   year,
        "type":   fmt or "album",
        "genre":  genres[0] if genres else "",
        "label":  (item.get("label") or [""])[0],
        "country": item.get("country", ""),
        "catno":  item.get("catno", ""),
    })

# ── Internet Archive proxy ────────────────────────────────────────────────────

IA_BASE = "https://archive.org"

# Music-focused IA collections to draw from
IA_COLLECTIONS = [
    "audio_music",
    "netlabels",
    "georgeblood",        # 78rpm digitizations
    "78rpm",
    "librivoxaudio",      # exclude but keep for fallback
    "opensource_audio",
    "etree",              # live concert recordings
    "audio_foreign",
    "rock",
    "folkmusic",
    "classicalmusic",
    "jazzandblues",
    "electronic",
]

# Collections to exclude (podcasts, audiobooks, radio)
IA_EXCLUDE = ["podcasts", "radio", "librivoxaudio", "spoken_word"]

@app.route("/api/ia/random")
def ia_random():
    """
    Pulls a random album/release from Internet Archive's audio collections.
    Uses a random page + row offset against the IA search API.
    """
    f_genre    = request.args.get("genre", "")
    f_year_from = request.args.get("year_from", "")
    f_year_to   = request.args.get("year_to", "")

    # Pick a random music collection to search within
    collection = random.choice(IA_COLLECTIONS[:8])  # focus on music-specific ones

    # Build query
    q_parts = [
        f"collection:{collection}",
        "mediatype:audio",
        "NOT mediatype:etree",  # exclude live recordings unless from etree collection
    ]
    if collection == "etree":
        q_parts = ["collection:etree", "mediatype:etree"]

    if f_genre:
        q_parts.append(f'subject:"{f_genre}"')

    year_clause = ""
    if f_year_from and f_year_to:
        year_clause = f" AND year:[{f_year_from} TO {f_year_to}]"
    elif f_year_from:
        year_clause = f" AND year:[{f_year_from} TO 2025]"
    elif f_year_to:
        year_clause = f" AND year:[1900 TO {f_year_to}]"

    query = " AND ".join(q_parts) + year_clause

    # Random page across results — IA has millions of audio items
    page = random.randint(1, 500)
    rows = 10

    params = {
        "q":      query,
        "fl[]":   ["identifier", "title", "creator", "subject", "year",
                   "description", "format", "collection", "downloads"],
        "sort[]": "random",   # IA supports random sort natively
        "rows":   rows,
        "page":   page,
        "output": "json",
    }

    try:
        r = requests.get(
            f"{IA_BASE}/advancedsearch.php",
            params=params,
            headers=HEADERS,
            timeout=12,
            verify=False,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    docs = data.get("response", {}).get("docs", [])
    if not docs:
        return jsonify({"error": "no results", "collection": collection, "page": page}), 404

    # Filter out spoken word / podcasts
    docs = [d for d in docs if not any(
        x in str(d.get("collection", "")).lower()
        for x in IA_EXCLUDE
    )]
    if not docs:
        return jsonify({"error": "all filtered", "collection": collection}), 404

    item = random.choice(docs)
    identifier = item.get("identifier", "")

    # Normalize creator field — can be string or list
    creator = item.get("creator", "")
    if isinstance(creator, list):
        creator = creator[0] if creator else ""

    title = item.get("title", "")

    # Subject field as tags
    subjects = item.get("subject", [])
    if isinstance(subjects, str):
        subjects = [subjects]
    tags = [s.strip() for s in subjects if s.strip()][:10]

    year = str(item.get("year", ""))[:4] if item.get("year") else ""

    # Cover: IA serves thumbnails at a predictable URL
    cover = f"https://archive.org/services/img/{identifier}" if identifier else ""

    url_ = f"https://archive.org/details/{identifier}" if identifier else ""

    return jsonify({
        "source":     "archive",
        "artist":     creator,
        "title":      title,
        "url":        url_,
        "cover":      cover,
        "tags":       tags,
        "year":       year,
        "type":       "album",
        "genre":      tags[0] if tags else "",
        "collection": collection,
        "identifier": identifier,
    })



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
