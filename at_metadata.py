"""ArcTime - externe Metadaten-Anreicherung: TMDB-Cast, IGDB, Spiele-Registry, OMDB,
manuelle Cover-Auswahl, Tagebuch-Cover-Nachladen."""
import hashlib, html, json, os, re, shutil, socket, subprocess, sys, time, threading, urllib.parse, urllib.request, webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path

from at_config import API_KEY, CASTFILE, DCOVFILE, GCOVERS, GFILE, IGDB_CLIENT_ID, IGDB_CLIENT_SECRET, PEOPLE, POSTERS, TMDB_KEY, TVSFILE, _norm_title
from at_store import _diary_lock, save_diary

# ── Cast-Fotos (TMDB) ──────────────────────────────────────────────────────────
_cast_lock = threading.Lock()

def load_cast_cache() -> dict:
    if CASTFILE.exists():
        try:
            d = json.loads(CASTFILE.read_text('utf-8'))
            return d if isinstance(d, dict) else {}
        except Exception:
            pass
    return {}

def save_cast_cache(c: dict):
    CASTFILE.write_text(json.dumps(c, ensure_ascii=False, indent=2), 'utf-8')

def _cast_key(title: str, year: str, kind: str) -> str:
    return f"{kind}|{_norm_title(title)}|{(year or '')[:4]}"

def _dl_person_photo(profile_path: str, person_id) -> str | None:
    if not profile_path:
        return None
    fname = f"p{person_id}.jpg"
    dest = PEOPLE / fname
    if dest.exists():
        return fname
    try:
        url = f"https://image.tmdb.org/t/p/w185{profile_path}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            dest.write_bytes(r.read())
        return fname
    except Exception:
        return None

def _tmdb_fetch_cast(title: str, year: str, kind: str) -> list:
    """Sucht Titel auf TMDB, lädt Cast-Fotos herunter (einmalig), gibt Cast-Liste zurück."""
    media = 'tv' if kind == 'series' else 'movie'
    te = urllib.parse.quote(title)
    date_field = 'first_air_date_year' if media == 'tv' else 'year'

    tmdb_id = None
    for url in [
        f"https://api.themoviedb.org/3/search/{media}?api_key={TMDB_KEY}&query={te}"
        + (f"&{date_field}={year[:4]}" if year else ''),
        f"https://api.themoviedb.org/3/search/{media}?api_key={TMDB_KEY}&query={te}",
    ]:
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                res = json.loads(r.read()).get('results', [])
            if res:
                tmdb_id = res[0]['id']
                break
        except Exception:
            continue
    if not tmdb_id:
        return []

    try:
        curl = f"https://api.themoviedb.org/3/{media}/{tmdb_id}/credits?api_key={TMDB_KEY}"
        with urllib.request.urlopen(curl, timeout=10) as r:
            cast = json.loads(r.read()).get('cast', [])
    except Exception:
        return []

    out = []
    for c in cast[:14]:
        photo = _dl_person_photo(c.get('profile_path'), c.get('id'))
        out.append({
            'id':        c.get('id'),
            'name':      c.get('name', ''),
            'character': c.get('character', ''),
            'photo':     f"/people/{photo}" if photo else None,
        })
    return out

def load_tmdb_seasons_cache() -> dict:
    if TVSFILE.exists():
        try:
            d = json.loads(TVSFILE.read_text('utf-8'))
            return d if isinstance(d, dict) else {}
        except Exception:
            pass
    return {}

def save_tmdb_seasons_cache(c: dict):
    TVSFILE.write_text(json.dumps(c, ensure_ascii=False, indent=2), 'utf-8')

def _tmdb_fetch_season_count(tmdb_id: int) -> dict | None:
    """Fragt TMDB nach Staffelzahl + Laufstatus einer Serie (per TMDB-Show-ID)."""
    try:
        url = f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_KEY}"
        with urllib.request.urlopen(url, timeout=10) as r:
            d = json.loads(r.read())
    except Exception:
        return None
    seasons = [s for s in (d.get('seasons') or []) if s.get('season_number', 0) > 0]
    return {
        'total': d.get('number_of_seasons'),
        'ended': d.get('status') in ('Ended', 'Canceled'),
        'latestSeasonNumber': max((s.get('season_number', 0) for s in seasons), default=None),
    }

def get_tmdb_season_count(tmdb_id: int) -> dict | None:
    key = str(int(tmdb_id))
    with _cast_lock:
        c = load_tmdb_seasons_cache()
        if key in c:
            return c[key]
    info = None
    try:
        info = _tmdb_fetch_season_count(tmdb_id)
    except Exception:
        info = None
    with _cast_lock:
        c = load_tmdb_seasons_cache()
        if info is not None:
            c[key] = info
            save_tmdb_seasons_cache(c)
    return info

def get_cast(title: str, year: str, kind: str) -> list:
    key = _cast_key(title, year, kind)
    with _cast_lock:
        cc = load_cast_cache()
        if key in cc:
            return cc[key]
    cast = []
    try:
        cast = _tmdb_fetch_cast(title, year, kind)
    except Exception:
        cast = []
    with _cast_lock:
        cc = load_cast_cache()
        cc[key] = cast
        save_cast_cache(cc)
    return cast


# ── IGDB (Spiele-Metadaten via Twitch OAuth) ──────────────────────────────────
_igdb_token = {'token': None, 'expires': 0}
_igdb_token_lock = threading.Lock()

def _igdb_get_token() -> str | None:
    """App-Access-Token per Client-Credentials-Flow holen (server-zu-server, kein Redirect)."""
    if not IGDB_CLIENT_ID or not IGDB_CLIENT_SECRET:
        return None
    with _igdb_token_lock:
        if _igdb_token['token'] and time.time() < _igdb_token['expires'] - 60:
            return _igdb_token['token']
        try:
            url = ("https://id.twitch.tv/oauth2/token"
                   f"?client_id={urllib.parse.quote(IGDB_CLIENT_ID)}"
                   f"&client_secret={urllib.parse.quote(IGDB_CLIENT_SECRET)}"
                   f"&grant_type=client_credentials")
            req = urllib.request.Request(url, method='POST')
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read())
            _igdb_token['token'] = d['access_token']
            _igdb_token['expires'] = time.time() + int(d.get('expires_in', 3600))
            return _igdb_token['token']
        except Exception as e:
            print(f"  IGDB-Token-Fehler: {e}")
            return None

def _igdb_query(endpoint: str, apicalypse_body: str) -> list:
    token = _igdb_get_token()
    if not token:
        return []
    req = urllib.request.Request(
        f"https://api.igdb.com/v4/{endpoint}",
        data=apicalypse_body.encode('utf-8'),
        method='POST',
        headers={
            'Client-ID': IGDB_CLIENT_ID,
            'Authorization': f'Bearer {token}',
            'Content-Type': 'text/plain',
        }
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def _igdb_cover_url(image_id: str, size: str = 't_cover_big') -> str:
    return f"https://images.igdb.com/igdb/image/upload/{size}/{image_id}.jpg"

def _dl_game_cover(image_id: str) -> str | None:
    if not image_id:
        return None
    fname = f"g{image_id}.jpg"
    dest = GCOVERS / fname
    if dest.exists():
        return fname
    try:
        req = urllib.request.Request(_igdb_cover_url(image_id), headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            dest.write_bytes(r.read())
        return fname
    except Exception:
        return None

def igdb_search_game(title: str) -> dict | None:
    """Bestes Suchergebnis für einen Spieltitel; None wenn kein Treffer/kein Zugriff."""
    esc_title = title.replace('"', '\\"')
    body = (f'search "{esc_title}"; '
            'fields name,cover.image_id,genres.name,platforms.name,'
            'first_release_date,summary; limit 5;')
    try:
        results = _igdb_query('games', body)
    except Exception:
        return None
    if not results:
        return None
    r = results[0]
    year = ''
    if r.get('first_release_date'):
        year = time.strftime('%Y', time.gmtime(r['first_release_date']))
    return {
        'igdb_id': r.get('id'),
        'title':   r.get('name', title),
        'year':    year,
        'genre':   ', '.join(g['name'] for g in r.get('genres', []) if 'name' in g),
        'platforms': [p['name'] for p in r.get('platforms', []) if 'name' in p],
        'cover_image_id': (r.get('cover') or {}).get('image_id'),
        'summary': r.get('summary', ''),
    }

def igdb_search_multi(title: str, limit: int = 8) -> list:
    """Mehrere Kandidaten für die manuelle Auswahl (Spiel-hinzufügen-Dialog)."""
    esc_title = title.replace('"', '\\"')
    body = (f'search "{esc_title}"; '
            f'fields name,cover.image_id,first_release_date,platforms.name; limit {limit};')
    try:
        results = _igdb_query('games', body)
    except Exception:
        return []
    out = []
    for r in results:
        year = time.strftime('%Y', time.gmtime(r['first_release_date'])) if r.get('first_release_date') else ''
        out.append({
            'igdb_id': r.get('id'),
            'title':   r.get('name', ''),
            'year':    year,
            'platforms': [p['name'] for p in r.get('platforms', []) if 'name' in p],
            'cover':   f"/gcover/{_dl_game_cover((r.get('cover') or {}).get('image_id'))}"
                       if (r.get('cover') or {}).get('image_id') else None,
        })
    return out


# ── Spiele-Registry (games.json) ──────────────────────────────────────────────
_games_lock = threading.Lock()

def load_games() -> dict:
    if GFILE.exists():
        try:
            d = json.loads(GFILE.read_text('utf-8'))
            return d if isinstance(d, dict) else {}
        except Exception:
            pass
    return {}

def save_games(g: dict):
    GFILE.write_text(json.dumps(g, ensure_ascii=False, indent=2), 'utf-8')

def _game_key(title: str, igdb_id: int | None = None) -> str:
    # Mit bekannter IGDB-ID ist der Key exakt und kollisionsfrei (z.B. bei
    # Remakes/Remasters mit ähnlichem Titel); ohne ID fällt es auf den
    # normalisierten Titel zurück (manuelles Hinzufügen ohne Treffer).
    if igdb_id:
        return f"game|id{int(igdb_id)}"
    return f"game|{_norm_title(title)}"

def get_or_create_game(title: str, platform_hint: str = '', igdb_id: int | None = None,
                        known_cover_image_id: str | None = None) -> dict:
    """Spiel in der Registry finden oder per IGDB anlegen (einmalig, danach aus Cache).
    known_cover_image_id: IGDB-Cover-Image-ID, die bereits aus einer externen Quelle
    (z.B. dem Backloggd-Journal-HTML) bekannt ist. Wird als Fallback genutzt, falls der
    IGDB-API-Aufruf selbst nichts liefert (Netzwerk/Token-Problem, Rate-Limit etc.),
    damit Titel und Cover trotzdem gesetzt werden, statt fälschlich 'nicht gefunden'."""
    key = _game_key(title, igdb_id)
    with _games_lock:
        games = load_games()
        if key in games:
            return games[key]

    entry = {'key': key, 'title': title, 'year': '', 'genre': '',
             'platforms': [platform_hint] if platform_hint else [],
             'cover': None, 'igdb_id': igdb_id, 'not_found': False}

    info = None
    try:
        if igdb_id:
            body = (f'fields name,cover.image_id,genres.name,platforms.name,first_release_date,summary; '
                     f'where id = {int(igdb_id)};')
            res = _igdb_query('games', body)
            if res:
                r = res[0]
                year = time.strftime('%Y', time.gmtime(r['first_release_date'])) if r.get('first_release_date') else ''
                info = {
                    'igdb_id': r.get('id'), 'title': r.get('name', title), 'year': year,
                    'genre': ', '.join(g['name'] for g in r.get('genres', []) if 'name' in g),
                    'platforms': [p['name'] for p in r.get('platforms', []) if 'name' in p],
                    'cover_image_id': (r.get('cover') or {}).get('image_id'),
                }
        else:
            info = igdb_search_game(title)
    except Exception:
        info = None

    if info:
        entry['title']   = info['title'] or title
        entry['year']    = info['year']
        entry['genre']   = info['genre']
        entry['igdb_id'] = info['igdb_id']
        if info.get('platforms'):
            entry['platforms'] = info['platforms']
        elif platform_hint:
            entry['platforms'] = [platform_hint]
        cover_image_id = info.get('cover_image_id') or known_cover_image_id
        cover_fname = _dl_game_cover(cover_image_id)
        entry['cover'] = f"/gcover/{cover_fname}" if cover_fname else None
    elif known_cover_image_id or igdb_id:
        # IGDB-API-Aufruf lieferte nichts (z.B. Netzwerk/Token-Problem), aber wir haben
        # bereits verlässliche Daten aus einer externen Quelle (Backloggd-HTML: Titel,
        # exakte IGDB-ID, Cover-Bild) -> die nutzen statt fälschlich 'nicht gefunden'.
        cover_fname = _dl_game_cover(known_cover_image_id) if known_cover_image_id else None
        entry['cover'] = f"/gcover/{cover_fname}" if cover_fname else None
    else:
        entry['not_found'] = True

    with _games_lock:
        games = load_games()
        games[key] = entry
        save_games(games)
    return entry

def build_games_api() -> list:
    games = load_games()
    out = []
    for key, e in games.items():
        out.append({
            't': e.get('title', ''), 'y': e.get('year', ''), 'key': key,
            'poster': e.get('cover'), 'genre': e.get('genre', ''),
            'platforms': e.get('platforms', []),
            'not_found': e.get('not_found', False),
        })
    return out


# ── OMDB ───────────────────────────────────────────────────────────────────────
class OmdbUnavailable(Exception):
    """OMDB-Anfrage konnte nicht sauber beantwortet werden (Rate-Limit erreicht,
    ungültiger API-Key, Netzwerk-/Serverfehler o.ä.) - bewusst unterschieden von
    einem echten 'Titel nicht gefunden', damit ein vorübergehender API-Ausfall
    nicht fälschlich dauerhaft als not_found im Cache landet (sonst wird der
    Eintrag nie wieder automatisch neu versucht und das Cover bleibt für immer
    leer, obwohl der Titel real existiert)."""
    pass


# TMDB-Genre-ID für Dokumentarfilme. Suchanfragen wie "Akira Kurosawa - Yojimbo"
# (Ordner-/Dateiname mit Regisseursnamen) matchen bei TMDB sonst leicht auf ein
# Making-of/Dokumentation über den Regisseur statt auf den eigentlichen
# Spielfilm (z.B. "Akira Kurosawa: It Is Wonderful to Create"). Ein Spielfilm
# ist praktisch immer der gewünschte Treffer, daher werden Dokus in der
# Ergebnisliste konsequent nach hinten einsortiert statt strikt nach
# TMDB-Relevanz zu gehen.
_TMDB_DOC_GENRE = 99

def _norm_title_loose(s: str) -> str:
    """Normalisiert einen Titel für Ähnlichkeitsvergleiche, MIT Leerzeichen
    zwischen den Wörtern (anders als das modulweite _norm_title(), das alle
    Nicht-Alnum-Zeichen ausnahmslos entfernt und daher für Tier-Vergleiche wie
    "startswith"/"contains" ungeeignet ist). So zählen 'I Swear', 'I swear'
    und 'I, Swear!' als gleich, aber 'I Swear' bleibt von 'ISwear' unterscheidbar
    für Wortgrenzen-Vergleiche."""
    s = (s or '').lower()
    s = re.sub(r"[^a-z0-9]+", ' ', s)
    return s.strip()


def _rank_tmdb_results(results: list, media: str, query: str = None) -> list:
    """Sortiert TMDB-Suchtreffer so, dass ein exakter Titel-Treffer immer vor
    einem bloß ähnlich beginnenden/enthaltenden Treffer landet - TMDBs eigene
    Relevanz-Sortierung gewichtet Popularität teils höher als Titel-Übereinstimmung
    und würde sonst z.B. bei "I Swear" (2025) einen völlig anderen, populäreren
    Titel wie "I Swear to You ..." an erste Stelle setzen. Dokumentationen/
    Making-ofs werden weiterhin konsequent nach hinten einsortiert, ein
    exakter Titel-Treffer schlägt das aber nicht - ein Spielfilm mit exakt
    passendem Titel ist praktisch nie eine Doku."""
    if not results:
        return results
    name_field = 'name' if media == 'tv' else 'title'
    nq = _norm_title_loose(query) if query else None

    def tier(r):
        if nq is None:
            return 0
        nt = _norm_title_loose(r.get(name_field, ''))
        if nt == nq:
            return 0
        if nt.startswith(nq) or nq.startswith(nt):
            return 1
        if nq in nt:
            return 2
        return 3

    def is_doc(r):
        return media == 'movie' and _TMDB_DOC_GENRE in (r.get('genre_ids') or [])

    indexed = list(enumerate(results))
    indexed.sort(key=lambda ir: (is_doc(ir[1]), tier(ir[1]), ir[0]))
    return [r for _, r in indexed]


def _tmdb_search_id(title: str, year: str, kind: str) -> int | None:
    media = 'tv' if kind == 'series' else 'movie'
    te = urllib.parse.quote(title)
    date_field = 'first_air_date_year' if media == 'tv' else 'year'
    for url in [
        f"https://api.themoviedb.org/3/search/{media}?api_key={TMDB_KEY}&query={te}"
        + (f"&{date_field}={year[:4]}" if year else ''),
        f"https://api.themoviedb.org/3/search/{media}?api_key={TMDB_KEY}&query={te}",
    ]:
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                res = json.loads(r.read()).get('results', [])
            if res:
                return _rank_tmdb_results(res, media, query=title)[0]['id']
        except Exception:
            continue
    return None


def _tmdb_fetch_full(title: str, year: str, kind: str, tmdb_id: int | None = None) -> dict | None:
    """Vollständige Ersatz-/Zusatzdaten von TMDB - dient sowohl als Fallback,
    wenn OMDB nicht erreichbar ist oder einen Titel nicht kennt, als auch als
    dauerhafte Zweitquelle (tmdb_id/Bewertung), damit ein einmal abgefragter
    Titel auch bei künftigem OMDB-Ausfall vollständig lokal nutzbar bleibt.
    Mit vorgegebener tmdb_id (z.B. aus einer manuellen Titel-Korrektur, bei der
    der Nutzer einen konkreten Suchtreffer ausgewählt hat) wird die Fuzzy-Suche
    übersprungen - eindeutig statt geraten."""
    media = 'tv' if kind == 'series' else 'movie'
    if not tmdb_id:
        tmdb_id = _tmdb_search_id(title, year, kind)
    if not tmdb_id:
        return None
    try:
        url = f"https://api.themoviedb.org/3/{media}/{tmdb_id}?api_key={TMDB_KEY}"
        with urllib.request.urlopen(url, timeout=10) as r:
            d = json.loads(r.read())
    except Exception:
        return None

    nn = lambda v: v if v else ''
    genre = ', '.join(g['name'] for g in (d.get('genres') or []) if g.get('name'))
    if media == 'tv':
        title_actual = nn(d.get('name'))
        date = d.get('first_air_date') or ''
        runtime_list = d.get('episode_run_time') or []
        runtime = str(runtime_list[0]) if runtime_list else ''
        total_seasons = d.get('number_of_seasons')
    else:
        title_actual = nn(d.get('title'))
        date = d.get('release_date') or ''
        runtime = str(d.get('runtime')) if d.get('runtime') else ''
        total_seasons = None

    poster_path = d.get('poster_path')
    return {
        'title_actual':  title_actual,
        'year_actual':   date[:4] if date else '',
        'genre':         genre,
        'plot':          nn(d.get('overview')),
        'runtime':       runtime,
        'total_seasons': str(total_seasons) if total_seasons else '',
        'poster_url':    f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else '',
        'poster_local':  None,
        'tmdb_id':       tmdb_id,
        'tmdb_rating':   d.get('vote_average'),
        'tmdb_votes':    d.get('vote_count'),
        'tagline':       nn(d.get('tagline')),
        'data_source':   'tmdb',
    }


def _tmdb_fallback_fetch(e: dict) -> dict | None:
    """Wrapper um _tmdb_fetch_full, der niemals eine Exception nach außen
    durchlässt - ein TMDB-Fehler beim Fallback darf den Refresh-Durchlauf
    nicht abbrechen, das Ergebnis ist einfach 'kein Ersatz gefunden'."""
    try:
        return _tmdb_fetch_full(e['t'], e['y'], e['kind'])
    except Exception:
        return None


def _merge_tmdb_fallback(cache: dict, e: dict, fb: dict, omdb_pending: bool) -> None:
    """Mergt TMDB-Ersatzdaten in den Cache-Eintrag (inkl. Poster-Download) -
    gemeinsame Merge-Logik für alle Stellen, an denen TMDB als Fallback für
    OMDB einspringt."""
    if fb.get('poster_url'):
        fb['poster_local'] = _dl_poster(fb['poster_url'], e['key'])
    else:
        fb.pop('poster_local', None)
    cache[e['key']] = {**cache[e['key']], **e, **fb, 'omdb_pending': omdb_pending, 'fetched': time.time()}


def _omdb_fetch(title: str, year, kind: str) -> dict | None:
    te   = urllib.parse.quote(title)
    tp   = '&type=series' if kind == 'series' else '&type=movie'
    base = f"https://www.omdbapi.com/?plot=full&apikey={API_KEY}"
    got_real_response = False
    last_error = None
    for url in [
        f"{base}&t={te}&y={year}{tp}" if year else None,
        f"{base}&t={te}{tp}",
        f"{base}&t={te}&y={year}"     if year else None,
        f"{base}&t={te}",
    ]:
        if not url:
            continue
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                d = json.loads(r.read())
        except Exception as e:
            last_error = e
            continue
        if d.get('Response') == 'True':
            # OMDBs t=-Suche liefert intern selbst eine Fuzzy-Suche - bei
            # exotischen/neuen Titeln kommt gelegentlich ein völlig anderer,
            # nur ähnlich klingender Titel zurück (z.B. "I Swear" -> "I Swear
            # to You ..."). Ein Treffer wird nur akzeptiert, wenn der
            # zurückgegebene Titel dem gesuchten Titel tatsächlich entspricht
            # (exakt oder einer ist Präfix des anderen) - sonst gilt er als
            # nicht gefunden und die Suche geht mit der nächsten URL-Variante
            # bzw. anschließend dem TMDB-Fallback weiter.
            got_title = d.get('Title') or ''
            nq, ng = _norm_title_loose(title), _norm_title_loose(got_title)
            if nq and ng and nq != ng and not nq.startswith(ng) and not ng.startswith(nq):
                got_real_response = True
                continue
            nn = lambda v: v if v and v != 'N/A' else ''
            return {
                'title_actual':  nn(d.get('Title')),
                'year_actual':   nn(d.get('Year')),
                'rated':         nn(d.get('Rated')),
                'runtime':       nn(d.get('Runtime')),
                'genre':         nn(d.get('Genre')),
                'director':      nn(d.get('Director')),
                'actors':        nn(d.get('Actors')),
                'plot':          nn(d.get('Plot')),
                'country':       nn(d.get('Country')),
                'language':      nn(d.get('Language')),
                'awards':        nn(d.get('Awards')),
                'imdb_id':       nn(d.get('imdbID')),
                'imdb_rating':   float(d['imdbRating']) if nn(d.get('imdbRating')) else None,
                'imdb_votes':    nn(d.get('imdbVotes')),
                'total_seasons': nn(d.get('totalSeasons')),
                'poster_url':    nn(d.get('Poster')),
                'poster_local':  None,
            }
        err = (d.get('Error') or '').strip()
        if 'not found' in err.lower():
            # OMDB hat geantwortet und diesen Titel eindeutig nicht - ein echtes
            # "nicht gefunden", ruhig weiter mit den übrigen URL-Varianten
            got_real_response = True
            continue
        # Jede andere Fehlermeldung (z.B. "Request limit reached!", "Invalid API
        # key!") ist KEIN verlässliches "nicht gefunden" - Key/Verbindung merken
        last_error = RuntimeError(err or 'OMDB antwortete ohne verwertbares Ergebnis')

    if not got_real_response:
        raise OmdbUnavailable(_describe_omdb_error(last_error))
    return None


def _describe_omdb_error(err: Exception | None) -> str:
    """Baut aus einer rohen Exception eine für Endnutzer verständliche Meldung -
    insbesondere HTTP 401 (ungültiger/abgelaufener API-Key) klar von einem
    vorübergehenden Rate-Limit oder Netzwerkfehler unterscheiden, da beim Key
    ein einfaches erneutes Versuchen nie von selbst funktionieren wird."""
    if err is None:
        return 'Keine gültige OMDB-Antwort erhalten'
    if isinstance(err, urllib.error.HTTPError):
        if err.code == 401:
            return ('OMDB-API-Key ungültig/abgelaufen (HTTP 401) - auf omdbapi.com kostenlos '
                    'einen neuen Key holen und in mediathek.py bei API_KEY eintragen')
        if err.code == 429:
            return 'OMDB-Tageslimit erreicht (HTTP 429) - versucht es automatisch später erneut'
        return f'OMDB-Serverfehler (HTTP {err.code})'
    if isinstance(err, urllib.error.URLError):
        return f'OMDB nicht erreichbar (Netzwerkfehler: {err.reason})'
    return str(err) or 'Unbekannter OMDB-Fehler'


def _poster_filename_for_key(key: str) -> str:
    # Deterministisch aus dem Key abgeleitet - identisch für dieselbe
    # Datei/denselben Titel über beliebig viele Cache-Neuaufbauten hinweg.
    return hashlib.md5(key.encode()).hexdigest() + '.jpg'


def _existing_poster_for_key(key: str) -> str | None:
    """Prüft, ob für diesen Key bereits ein Poster lokal auf der Platte liegt
    (z.B. von vor einem Cache-Reset). So lassen sich schon heruntergeladene
    Cover sofort weiterverwenden, auch wenn OMDB gerade nicht erreichbar ist
    und gar keine neue Anfrage möglich wäre - kein erneuter Download nötig."""
    fname = _poster_filename_for_key(key)
    return fname if (POSTERS / fname).exists() else None


def _dl_poster(url: str, key: str) -> str | None:
    if not url:
        return None
    fname = _poster_filename_for_key(key)
    dest = POSTERS / fname
    if dest.exists():
        return fname
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            dest.write_bytes(r.read())
        return fname
    except Exception:
        return None

def _dl_poster_force(url: str, key: str) -> str | None:
    """Wie _dl_poster, lädt aber immer neu herunter (überschreibt), für die
    manuelle Cover-Auswahl."""
    if not url:
        return None
    fname = hashlib.md5(key.encode()).hexdigest() + '.jpg'
    dest = POSTERS / fname
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            dest.write_bytes(r.read())
        return fname
    except Exception:
        return None


# ── Manuelle Cover-Auswahl (TMDB-Kandidaten, ähnlich Letterboxd) ──────────────
def tmdb_poster_candidates(title: str, year: str, kind: str) -> list:
    """Sammelt mehrere Cover-Kandidaten: alternative Poster-Varianten des besten
    TMDB-Treffers plus die Poster der nächsten Titel-Suchergebnisse (falls die
    automatische Zuordnung den falschen Titel getroffen hat)."""
    media = 'tv' if kind == 'series' else 'movie'
    te = urllib.parse.quote(title)
    date_field = 'first_air_date_year' if media == 'tv' else 'year'

    results = []
    for url in [
        f"https://api.themoviedb.org/3/search/{media}?api_key={TMDB_KEY}&query={te}"
        + (f"&{date_field}={year[:4]}" if year else ''),
        f"https://api.themoviedb.org/3/search/{media}?api_key={TMDB_KEY}&query={te}",
    ]:
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                results = json.loads(r.read()).get('results', [])
            if results:
                break
        except Exception:
            continue
    if not results:
        return []
    results = _rank_tmdb_results(results, media, query=title)

    out, seen = [], set()

    def add(path, label):
        if not path or path in seen:
            return
        seen.add(path)
        out.append({
            'url':   f"https://image.tmdb.org/t/p/w342{path}",
            'label': label,
        })

    best = results[0]
    name_field = 'name' if media == 'tv' else 'title'
    date_val = (best.get('first_air_date') or best.get('release_date') or '')[:4]
    try:
        img_url = f"https://api.themoviedb.org/3/{media}/{best['id']}/images?api_key={TMDB_KEY}"
        with urllib.request.urlopen(img_url, timeout=10) as r:
            imgs = json.loads(r.read()).get('posters', [])
        for im in imgs[:8]:
            add(im.get('file_path'), f"{best.get(name_field,'')} {date_val}".strip())
    except Exception:
        pass

    # Falls die Auto-Zuordnung den falschen Titel traf: weitere Suchtreffer
    for r in results[:6]:
        yv = (r.get('first_air_date') or r.get('release_date') or '')[:4]
        add(r.get('poster_path'), f"{r.get(name_field,'')} {yv}".strip())

    return out


def title_search_candidates(query: str, kind: str, limit: int = 8) -> list:
    """Freitext-Suche bei TMDB für die manuelle Titel-Korrektur: gibt für jeden
    Treffer tmdb_id/Titel/Jahr/Poster/Kurzbeschreibung zurück, damit der Nutzer
    bei einer falschen Auto-Erkennung (z.B. "Spartacus Remastered - US" ->
    "US") gezielt den richtigen Treffer auswählen kann, statt auf eine erneute
    Fuzzy-Suche mit demselben (kaputten) Titel zu hoffen."""
    media = 'tv' if kind == 'series' else 'movie'
    name_field = 'name' if media == 'tv' else 'title'
    date_field = 'first_air_date' if media == 'tv' else 'release_date'
    url = f"https://api.themoviedb.org/3/search/{media}?api_key={TMDB_KEY}&query={urllib.parse.quote(query)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            results = json.loads(r.read()).get('results', [])
    except Exception:
        return []
    results = _rank_tmdb_results(results, media, query=query)

    out = []
    for r in results[:limit]:
        yv = (r.get(date_field) or '')[:4]
        out.append({
            'tmdb_id': r.get('id'),
            'title':   r.get(name_field, ''),
            'year':    yv,
            'poster':  f"https://image.tmdb.org/t/p/w185{r['poster_path']}" if r.get('poster_path') else None,
            'overview': (r.get('overview') or '')[:160],
        })
    return out


# ── Tagebuch-Cover-Nachladen (für Einträge ohne lokalen Bibliotheks-Treffer) ───
# Fetch-once-cache-forever: pro Titel/Jahr/Art höchstens ein API-Aufruf + Download,
# danach für immer aus diary_covers.json (Filme/Serien) bzw. games.json (Games).
_dcov_lock = threading.Lock()

def load_diary_covers() -> dict:
    if DCOVFILE.exists():
        try:
            d = json.loads(DCOVFILE.read_text('utf-8'))
            return d if isinstance(d, dict) else {}
        except Exception:
            pass
    return {}

def save_diary_covers(d: dict):
    DCOVFILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), 'utf-8')

def _dcov_key(kind: str, title: str, year: str) -> str:
    return f"{kind}|{_norm_title(title)}|{(year or '')[:4]}"

def ensure_diary_poster(entry: dict) -> bool:
    """Versucht für einen Tagebuch-Eintrag ohne Cover eins nachzuladen.
    Gibt True zurück, wenn der Eintrag verändert wurde (Poster und/oder Genre)."""
    if entry.get('poster'):
        return False
    kind = entry.get('kind')
    changed = False

    if kind == 'game':
        key = entry.get('key')
        if not key:
            return False
        games = load_games()
        g = games.get(key)
        if g and g.get('cover'):
            entry['poster'] = g['cover']
            changed = True
        if g and not entry.get('genre') and g.get('genre'):
            entry['genre'] = g['genre']
            changed = True
        return changed

    if kind not in ('movie', 'season'):
        return False

    omdb_kind = 'series' if kind == 'season' else 'movie'
    title = entry.get('title', '')
    year  = entry.get('year', '')
    if not title:
        return False
    dkey = _dcov_key(omdb_kind, title, year)

    with _dcov_lock:
        covers = load_diary_covers()
        c = covers.get(dkey)
        if c is None:
            data = None
            try:
                data = _omdb_fetch(title, year, omdb_kind)
            except Exception:
                data = None
            if data and data.get('poster_url'):
                fname = _dl_poster(data['poster_url'], f"diary|{dkey}")
                c = {'poster_local': fname, 'genre': data.get('genre', '')}
            else:
                c = {'poster_local': None, 'genre': ''}
            covers[dkey] = c
            save_diary_covers(covers)

    if c.get('poster_local'):
        entry['poster'] = f"/poster/{c['poster_local']}"
        changed = True
    if not entry.get('genre') and c.get('genre'):
        entry['genre'] = c['genre']
        changed = True
    return changed

def backfill_diary_posters(entries: list, limit: int | None = None) -> dict:
    """Lädt fehlende Cover für Tagebuch-Einträge nach, höchstens `limit` Versuche
    pro Aufruf (damit ein einzelner API-Request nie durch viele langsame
    Netzwerk-Calls blockiert). Wird NIE automatisch bei einem normalen
    Tagebuch-Laden ausgeführt, sondern nur über den dedizierten
    /api/diary/backfill-covers-Endpunkt, den das Frontend im Hintergrund
    wiederholt aufruft, ohne den Seitenaufbau zu blockieren."""
    candidates = [e for e in entries if not e.get('poster')]
    to_process = candidates if limit is None else candidates[:limit]
    processed = updated = 0
    for e in to_process:
        processed += 1
        if ensure_diary_poster(e):
            updated += 1
    if updated:
        with _diary_lock:
            save_diary(entries)
    remaining = max(len(candidates) - processed, 0)
    return {'entries': entries, 'processed': processed, 'updated': updated, 'remaining': remaining}


