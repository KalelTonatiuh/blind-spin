import os
import random
import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, request, jsonify, render_template

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# Persistent session with retry logic for flaky SSL on Railway
def make_session():
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.3,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

session = make_session()

MB_BASE      = "https://musicbrainz.org/ws/2"
CAA_BASE     = "https://coverartarchive.org"
DISCOGS_BASE = "https://api.discogs.com"

DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN", "")
DISCOGS_TOTAL = 15000000  # ~15M releases in Discogs
LASTFM_KEY    = os.environ.get("LASTFM_API_KEY", "")
LASTFM_BASE   = "https://ws.audioscrobbler.com/2.0"

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
    # Smarter query: require primary type, tag count > 0, exclude various/unknown artists
    # Use Lucene syntax to bias toward releases with real metadata
    query = (
        'primarytype:(Album OR EP) AND '
        'NOT artist:"Various Artists" AND '
        'NOT artist:"Unknown" AND '
        'firstreleasedate:[1950 TO 2025]'
    )
    url = f"{MB_BASE}/release-group?query={requests.utils.quote(query)}&limit=10&offset={offset}&fmt=json"
    try:
        r = session.get(url, headers=HEADERS, timeout=10, verify=False)
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
        r = session.get(url, headers=HEADERS, timeout=6, verify=False)
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
            r = session.get(url, headers=HEADERS, timeout=10, allow_redirects=True, verify=False)
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
        r = session.get(
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
    f_style    = request.args.get("style", "")
    f_year_from = request.args.get("year_from", "")
    f_year_to   = request.args.get("year_to", "")
    f_country  = request.args.get("country", "")
    f_format   = request.args.get("format", "")

    # Build year range — Discogs 'year' param accepts a single year or YYYY-YYYY range
    year_q = ""
    if f_year_from and f_year_to:
        year_q = f"{f_year_from}-{f_year_to}"
    elif f_year_from:
        year_q = f"{f_year_from}-2030"
    elif f_year_to:
        year_q = f"1900-{f_year_to}"

    has_filters = any([f_genre, f_style, year_q, f_country, f_format])

    # Page ceiling by filter specificity
    if year_q and f_genre:
        max_page = 50
    elif year_q:
        max_page = 200
    elif f_genre or f_style:
        max_page = 300
    else:
        max_page = 3000

    page = random.randint(1, max_page)

    search_url = f"{DISCOGS_BASE}/database/search"
    params = {
        "type":     "release",
        "per_page": 50,
        "page":     page,
    }

    if f_genre:   params["genre"]   = f_genre
    if f_style:   params["style"]   = f_style
    if year_q:    params["year"]    = year_q
    if f_country: params["country"] = f_country
    if f_format:  params["format"]  = f_format
    # Do NOT apply a default format filter — it kills results for older decades
    # where releases are tagged LP/Vinyl/12" rather than "album"

    try:
        r = session.get(search_url, headers=dg_headers, params=params, timeout=12, verify=False)
        # If we landed on an empty page, retry once with a lower page number
        if r.status_code == 404 or (r.status_code == 200 and not r.json().get("results")):
            params["page"] = random.randint(1, max(1, max_page // 4))
            r = session.get(search_url, headers=dg_headers, params=params, timeout=12, verify=False)
        if r.status_code == 429:
            return jsonify({"error": "discogs rate limited"}), 429
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    if not results:
        return jsonify({"error": "no results"}), 404

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
        "release_id": item.get("id", ""),
    })

# ── Discogs release detail (tracklist, notes) ─────────────────────────────────

@app.route("/api/discogs/release/<int:release_id>")
def discogs_release(release_id):
    if not DISCOGS_TOKEN:
        return jsonify({"error": "no token"}), 500

    dg_headers = {
        "User-Agent": "BlindSpin/1.0 (https://github.com/KalelTonatiuh/blind-spin)",
        "Authorization": f"Discogs token={DISCOGS_TOKEN}",
        "Accept": "application/json",
    }

    try:
        r = session.get(
            f"{DISCOGS_BASE}/releases/{release_id}",
            headers=dg_headers, timeout=10, verify=False
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    tracklist = []
    for t in data.get("tracklist", []):
        tracklist.append({
            "pos":      t.get("position", ""),
            "title":    t.get("title", ""),
            "duration": t.get("duration", ""),
        })

    notes = data.get("notes", "")[:400] if data.get("notes") else ""

    labels = data.get("labels", [])
    label  = labels[0].get("name", "") if labels else ""
    catno  = labels[0].get("catno", "") if labels else ""

    return jsonify({
        "tracklist": tracklist,
        "notes":     notes,
        "label":     label,
        "catno":     catno,
    })

# ── Wikipedia artist blurb ────────────────────────────────────────────────────

@app.route("/api/wiki/artist")
def wiki_artist():
    artist = request.args.get("artist", "").strip()
    if not artist:
        return jsonify({"blurb": None}), 200

    try:
        # Search for the artist page
        search_r = session.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action":   "query",
                "list":     "search",
                "srsearch": f'"{artist}" band OR musician OR singer OR artist OR discography',
                "srlimit":  5,
                "format":   "json",
            },
            headers={"User-Agent": "BlindSpin/1.0"},
            timeout=8, verify=False
        )
        search_r.raise_for_status()
        results = search_r.json().get("query", {}).get("search", [])
        if not results:
            return jsonify({"blurb": None})

        # Validate result title actually contains the artist name (case-insensitive)
        # This prevents "Canister (album)" or unrelated articles matching
        artist_lower = artist.lower()
        title = None
        for res in results:
            t = res["title"]
            snippet = res.get("snippet", "").lower()
            # Accept if: title starts with artist name, or snippet mentions them prominently
            if t.lower().startswith(artist_lower) or artist_lower in t.lower():
                title = t
                break
        if not title:
            return jsonify({"blurb": None})

        # Fetch extract
        extract_r = session.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action":      "query",
                "prop":        "extracts|categories",
                "exintro":     True,
                "explaintext": True,
                "exsentences": 3,
                "titles":      title,
                "format":      "json",
            },
            headers={"User-Agent": "BlindSpin/1.0"},
            timeout=8, verify=False
        )
        extract_r.raise_for_status()
        pages = extract_r.json().get("query", {}).get("pages", {})
        page  = next(iter(pages.values()), {})
        extract = page.get("extract", "").strip()

        # Reject if extract describes something clearly non-musical
        NON_MUSIC = ["is a film", "is a television", "is a novel", "is a video game",
                     "is a software", "is a company", "is a town", "is a city",
                     "is a chemical", "is a type of"]
        if any(phrase in extract.lower() for phrase in NON_MUSIC):
            return jsonify({"blurb": None})

        if not extract or len(extract) < 40:
            return jsonify({"blurb": None})

        return jsonify({"blurb": extract, "wiki_title": title})

    except Exception as e:
        return jsonify({"blurb": None})

# ── Internet Archive proxy ────────────────────────────────────────────────────

IA_BASE = "https://archive.org"

# Modern music collections
IA_COLLECTIONS_MODERN = [
    "netlabels",
    "opensource_audio",
    "audio_music",
    "audio_foreign",
    "rock",
    "folkmusic",
    "classicalmusic",
    "jazzandblues",
    "electronic",
]

# Historical/archival collections (78rpm era — surfaced separately)
IA_COLLECTIONS_HISTORICAL = [
    "georgeblood",
    "78rpm",
    "audio_music",
]

# Collections to exclude (podcasts, audiobooks, radio)
IA_EXCLUDE = ["podcasts", "radio", "librivoxaudio", "spoken_word"]
IA_JUNK_ARTISTS = {"unknown", "various", "various artists", "", "n/a"}

@app.route("/api/ia/random")
def ia_random():
    """
    Pulls a random album/release from Internet Archive's audio collections.
    historical=1 param surfaces 78rpm/archival material specifically.
    """
    f_genre      = request.args.get("genre", "")
    f_year_from  = request.args.get("year_from", "")
    f_year_to    = request.args.get("year_to", "")
    historical   = request.args.get("historical", "0") == "1"

    pool = IA_COLLECTIONS_HISTORICAL if historical else IA_COLLECTIONS_MODERN
    collection = random.choice(pool)

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
        r = session.get(
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

    # Filter out items with placeholder/unreadable metadata
    JUNK_TITLES = {"none legible", "unknown", "untitled", "", "n/a", "na"}
    docs = [d for d in docs if
        str(d.get("title", "")).strip().lower() not in JUNK_TITLES and
        str(d.get("title", "")).strip() != ""
    ]
    if not docs:
        return jsonify({"error": "all junk metadata", "collection": collection}), 404

    item = random.choice(docs)
    identifier = item.get("identifier", "")

    # Normalize creator field — can be string or list
    creator = item.get("creator", "")
    if isinstance(creator, list):
        creator = creator[0] if creator else ""
    # Skip items with junk artist names
    if creator.strip().lower() in IA_JUNK_ARTISTS:
        return jsonify({"error": "junk artist", "collection": collection}), 404

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



# ── Last.fm proxy ────────────────────────────────────────────────────────────

LASTFM_TAGS = [
    "rock", "electronic", "jazz", "hip-hop", "classical", "folk", "metal",
    "ambient", "punk", "soul", "reggae", "blues", "indie", "experimental",
    "pop", "country", "r&b", "latin", "noise", "drone", "post-rock",
    "shoegaze", "psychedelic", "garage rock", "black metal", "doom metal",
    "idm", "techno", "house", "new wave", "post-punk",
]

@app.route("/api/lfm/random")
def lfm_random():
    if not LASTFM_KEY:
        return jsonify({"error": "LASTFM_API_KEY not set"}), 500

    f_genre = request.args.get("genre", "")
    tag = f_genre.lower() if f_genre else random.choice(LASTFM_TAGS)

    # First fetch page 1 to discover total pages, then pick a random valid page
    def fetch_page(page):
        return session.get(LASTFM_BASE, params={
            "method":  "tag.gettopalbums",
            "tag":     tag,
            "api_key": LASTFM_KEY,
            "format":  "json",
            "limit":   50,
            "page":    page,
        }, headers=HEADERS, timeout=10, verify=False)

    try:
        r = fetch_page(1)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    # Check total pages available
    attrs = data.get("albums", {}).get("@attr", {})
    total_pages = int(attrs.get("totalPages", 1))
    total_pages = min(total_pages, 20)  # cap at 20 to avoid very stale pages

    # If more than 1 page exists, randomly pick one (not page 1 to avoid top-chart bias)
    if total_pages > 1:
        page = random.randint(2, total_pages)
        try:
            r = fetch_page(page)
            r.raise_for_status()
            data = r.json()
        except Exception:
            pass  # fall back to page 1 data

    albums = data.get("albums", {}).get("album", [])
    if not albums:
        return jsonify({"error": "no albums", "tag": tag}), 404

    albums = [a for a in albums if
              a.get("name") and a["name"] not in ("[Unknown Album]", "") and
              a.get("artist", {}).get("name") not in ("", "[Unknown Artist]")]
    if not albums:
        return jsonify({"error": "all filtered", "tag": tag}), 404

    item   = random.choice(albums)
    artist = item.get("artist", {}).get("name", "")
    title  = item.get("name", "")
    url_   = item.get("url", "")

    images = item.get("image", [])
    cover  = next((img["#text"] for img in reversed(images) if img.get("#text")), "")

    tags_list = []
    year = ""
    try:
        info_r = session.get(LASTFM_BASE, params={
            "method":  "album.getinfo",
            "artist":  artist,
            "album":   title,
            "api_key": LASTFM_KEY,
            "format":  "json",
        }, headers=HEADERS, timeout=8, verify=False)
        info_r.raise_for_status()
        info = info_r.json().get("album", {})
        tags_list = [t["name"] for t in info.get("tags", {}).get("tag", []) if t.get("name")][:8]
        published = info.get("wiki", {}).get("published", "")
        if published:
            import re
            m = re.search(r'\b(19|20)\d{2}\b', published)
            if m:
                year = m.group(0)
    except Exception:
        pass

    return jsonify({
        "source":  "lastfm",
        "artist":  artist,
        "title":   title,
        "url":     url_,
        "cover":   cover,
        "tags":    tags_list,
        "year":    year,
        "type":    "album",
        "genre":   tag,
        "label":   "",
        "country": "",
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
