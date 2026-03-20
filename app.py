import os
import requests
from flask import Flask, request, jsonify, render_template, abort

app = Flask(__name__)

MB_BASE  = "https://musicbrainz.org/ws/2"
CAA_BASE = "https://coverartarchive.org"

# MusicBrainz requires a descriptive User-Agent or it blocks you
HEADERS = {
    "User-Agent": "BlindSpin/1.0 (https://github.com/user/blind-spin; contact@example.com)",
    "Accept": "application/json",
}

# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

# ── MB proxy ──────────────────────────────────────────────────────────────────

@app.route("/api/mb/release-groups")
def mb_release_groups():
    """
    Proxy: GET /api/mb/release-groups?offset=N
    Calls MB: /ws/2/release-group?query=*&limit=5&offset=N&fmt=json
    """
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
    """
    Proxy: GET /api/mb/release-group/<mbid>?inc=tags+genres
    """
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
    """
    Proxy: GET /api/caa/release-group/<mbid>
    CoverArtArchive returns 307 redirect; requests follows it automatically.
    """
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

# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
