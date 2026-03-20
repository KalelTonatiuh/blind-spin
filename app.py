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

# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
