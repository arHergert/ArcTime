"""ArcTime - Import/Sync externer Tagebuch-Quellen: Backloggd, CSV (Letterboxd/Serializd),
Serializd-HTML, Letterboxd-RSS-Sync."""
import hashlib, html, json, os, re, shutil, socket, subprocess, sys, time, threading, urllib.parse, urllib.request, webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path

from at_config import _norm_title
from at_store import _diary_lock, load_config, load_diary, save_config, save_diary
from at_metadata import _dcov_key, _dl_poster, ensure_diary_poster, get_or_create_game

# ── Backloggd-Journal-Import (Rohtext-Paste) ──────────────────────────────────
_BL_MONTHS = {
    'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
    'july':7,'august':8,'september':9,'october':10,'november':11,'december':12,
    'januar':1,'februar':2,'märz':3,'maerz':3,'mai':5,'juni':6,'juli':7,
    'oktober':10,'dezember':12,
}
_BL_MONTH_RE   = re.compile(r'^([A-Za-zÄÖÜäöüß]+),?\s+(\d{4})$')
_BL_DAY_RE     = re.compile(r'^(\d{1,2})(?:st|nd|rd|th)?\.?$')
_BL_BRACKET_RE = re.compile(r'^\[(.+?)\]\((.+?)\)$')
_BL_STATUS     = {'started', 'finished', 'replayed', 'abandoned'}
_BL_FURNITURE  = {'* show', '* year', 'show', 'year', '*'}
_BL_STAR_RE    = re.compile(r'^([★☆]+|[\d.]+\s*(?:/\s*5|stars?|sterne)?)$', re.I)

def _bl_norm(t: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', (t or '').lower())

def _parse_backloggd_events(text: str) -> list:
    """Zeilenbasierter State-Machine-Parser für den eingefügten Backloggd-Journaltext.
    Gibt eine flache Liste von Events zurück: {title, platform, date, status}."""
    lines = [l.strip() for l in text.splitlines()]
    lines = [l for l in lines if l]

    cur_year = cur_month = cur_day = None
    pending_title = pending_platform = None
    awaiting_platform = False
    events = []

    for line in lines:
        low = line.lower()

        if low in _BL_FURNITURE:
            continue

        m = _BL_MONTH_RE.match(line)
        if m and m.group(1).lower() in _BL_MONTHS:
            cur_month = _BL_MONTHS[m.group(1).lower()]
            cur_year  = int(m.group(2))
            cur_day = None
            pending_title = pending_platform = None
            awaiting_platform = False
            continue

        dm = _BL_DAY_RE.match(line)
        if dm and cur_month:
            cur_day = int(dm.group(1))
            pending_title = pending_platform = None
            awaiting_platform = False
            continue

        bm = _BL_BRACKET_RE.match(line)
        if bm:
            content = bm.group(1).strip()
            if not awaiting_platform:
                pending_title = content
                pending_platform = None
                awaiting_platform = True
            else:
                pending_platform = content
                awaiting_platform = False
            continue

        if line == '-':
            if awaiting_platform:
                pending_platform = ''
                awaiting_platform = False
            continue

        if _BL_STAR_RE.match(line) and not low in _BL_STATUS:
            # Sternebewertung neben einem Eintrag – wird derzeit nicht weiter ausgewertet
            continue

        if low in _BL_STATUS:
            if pending_title and cur_year and cur_month and cur_day:
                events.append({
                    'title':    pending_title,
                    'platform': pending_platform or '',
                    'date':     f"{cur_year:04d}-{cur_month:02d}-{cur_day:02d}",
                    'status':   low,
                })
            # Tag bleibt bestehen (mehrere Einträge pro Tag möglich), Titel wird zurückgesetzt
            pending_title = pending_platform = None
            awaiting_platform = False
            continue

        # unbekannte / statuslose Zeile (Session-Log ohne Started/Finished) -> ignorieren

    return events


# ── Backloggd-Journal-Import (HTML-Quelltext) ─────────────────────────────────
# Robusterer Parser für den Fall, dass der Nutzer statt des sichtbaren Texts den
# HTML-Quelltext der Journal-Seite einfügt (z.B. per "Element untersuchen" /
# outerHTML kopiert). Dieser Weg ist deutlich zuverlässiger als der Text-Export,
# da IGDB-ID, Cover-Bild und Bewertung direkt in Attributen/URLs stehen statt
# aus Formatierung erraten werden zu müssen.
_BL_HTML_MONTHS = {
    'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
    'july':7,'august':8,'september':9,'october':10,'november':11,'december':12,
}
_BL_ENTRY_SPLIT_RE = re.compile(r'(?=<div class="row journal_entry")')
_BL_MONTHYEAR_RE    = re.compile(r'<h4>\s*([A-Za-z]+),\s*(\d{4})\s*</h4>')
_BL_DAYNUM_RE       = re.compile(r'<h4 class="date-day[^"]*">\s*(\d{1,2})\s*</h4>')
_BL_GAMEID_RE       = re.compile(r'game_id="(\d+)"')
_BL_COVERIMG_RE     = re.compile(r'/t_cover_big/([A-Za-z0-9]+)\.\w+')
_BL_TITLE_RE        = re.compile(r'<a href="/games/[^"]+/">([^<]+)</a>')
_BL_PLATFORM_RE     = re.compile(r'class="[^"]*\bjournal-platform\b[^"]*"[^>]*>([^<]+)</a>')
_BL_STATUS_RE       = re.compile(r'journal-time">([^<]*)</p>')
_BL_RATING_RE       = re.compile(r'\brating:(\d+)/')


def _looks_like_backloggd_html(text: str) -> bool:
    return bool(re.search(r'<div[^>]*class="[^"]*journal_entry', text))


def _parse_backloggd_html(raw_html: str) -> list:
    """Zerlegt den rohen Journal-HTML-Quelltext in einzelne journal_entry-Blöcke
    und extrahiert je Eintrag: game_id, Titel, Cover-Bild-ID, Datum, Status,
    Plattform, Bewertung (1-10) und Replay-Kennzeichnung."""
    chunks = [c for c in _BL_ENTRY_SPLIT_RE.split(raw_html) if 'journal_entry' in c]
    events = []
    cur_month = cur_year = None
    for chunk in chunks:
        mm = _BL_MONTHYEAR_RE.search(chunk)
        if mm and mm.group(1).lower() in _BL_HTML_MONTHS:
            cur_month = _BL_HTML_MONTHS[mm.group(1).lower()]
            cur_year  = int(mm.group(2))
        if not cur_month or not cur_year:
            continue  # noch kein Monat bekannt -> Eintrag kann nicht datiert werden

        dm = _BL_DAYNUM_RE.search(chunk)
        if not dm:
            continue
        day = int(dm.group(1))

        gid_m   = _BL_GAMEID_RE.search(chunk)
        title_m = _BL_TITLE_RE.search(chunk)
        if not gid_m or not title_m:
            continue

        status_matches = _BL_STATUS_RE.findall(chunk)
        status = next((s.strip() for s in status_matches if s.strip()), '')
        if status.lower() not in ('started', 'finished'):
            continue  # Session-Log ohne Statuswechsel -> für Pairing irrelevant

        cover_m  = _BL_COVERIMG_RE.search(chunk)
        plat_m   = _BL_PLATFORM_RE.search(chunk)
        rating_m = _BL_RATING_RE.search(chunk)

        events.append({
            'game_id':         int(gid_m.group(1)),
            'title':           html.unescape(title_m.group(1)).strip(),
            'cover_image_id':  cover_m.group(1) if cover_m else None,
            'platform':        html.unescape(plat_m.group(1)).strip() if plat_m else '',
            'date':            f"{cur_year:04d}-{cur_month:02d}-{day:02d}",
            'status':          status.lower(),
            'rating':          int(rating_m.group(1)) if rating_m else None,
            'replay':          'replay-indicator' in chunk,
        })
    return events


def _dedupe_backloggd_html_events(events: list) -> list:
    """Fasst mehrfach vorkommende Einträge (gleiches Spiel/Datum/Status, z.B. durch
    Backloggd-Doppel-Logs bei Replays am selben Tag) zu einem zusammen und behält
    dabei Replay-Flag und Bewertung, falls eine der Varianten sie trägt."""
    merged, order = {}, []
    for e in events:
        k = (e['game_id'], e['date'], e['status'])
        if k not in merged:
            merged[k] = dict(e)
            order.append(k)
        else:
            if e.get('replay'):
                merged[k]['replay'] = True
            if e.get('rating') and not merged[k].get('rating'):
                merged[k]['rating'] = e['rating']
    return [merged[k] for k in order]


def _pair_backloggd_html_events(events: list) -> list:
    """Gruppiert HTML-Events je IGDB-game_id chronologisch und paart Started/Finished
    zu Playthroughs. Analog zu _pair_backloggd_events, aber game_id-basiert (exakt,
    statt Titel-Normalisierung) und mit echten Replay-/Rating-Flags aus dem HTML."""
    events = _dedupe_backloggd_html_events(events)
    by_game = {}
    for e in events:
        by_game.setdefault(e['game_id'], []).append(e)

    playthroughs = []
    for gid, evs in by_game.items():
        evs.sort(key=lambda e: (e['date'], 0 if e['status'] == 'started' else 1))
        open_pt = None
        for e in evs:
            if e['status'] == 'started':
                if open_pt:
                    playthroughs.append(open_pt)
                open_pt = {
                    'game_id': gid, 'title': e['title'], 'platform': e['platform'],
                    'cover_image_id': e.get('cover_image_id'),
                    'started': e['date'], 'finished': None,
                    'is_replay': e.get('replay', False),
                    'rating': e.get('rating'),
                }
            else:  # finished
                if open_pt:
                    open_pt['finished'] = e['date']
                    if e['platform']:
                        open_pt['platform'] = e['platform']
                    if e.get('replay'):
                        open_pt['is_replay'] = True
                    if e.get('rating'):
                        open_pt['rating'] = e['rating']
                    if not open_pt.get('cover_image_id'):
                        open_pt['cover_image_id'] = e.get('cover_image_id')
                    playthroughs.append(open_pt)
                    open_pt = None
                else:
                    playthroughs.append({
                        'game_id': gid, 'title': e['title'], 'platform': e['platform'],
                        'cover_image_id': e.get('cover_image_id'),
                        'started': None, 'finished': e['date'],
                        'is_replay': e.get('replay', False),
                        'rating': e.get('rating'),
                    })
        if open_pt:
            playthroughs.append(open_pt)

    playthroughs.sort(key=lambda p: p['finished'] or p['started'] or '')
    return playthroughs


def _pair_backloggd_events(events: list) -> list:
    """Gruppiert Events je Titel chronologisch und paart Started/Finished zu Playthroughs.
    Gibt eine Liste von Playthroughs zurück (chronologisch, älteste zuerst)."""
    by_title = {}
    for e in events:
        by_title.setdefault(_bl_norm(e['title']), []).append(e)

    playthroughs = []
    for norm, evs in by_title.items():
        # chronologisch sortieren; bei Gleichstand Started vor Finished (deckt Same-Day-Pairs ab)
        evs.sort(key=lambda e: (e['date'], 0 if e['status'] == 'started' else 1))
        open_pt = None
        finished_count = 0
        for e in evs:
            if e['status'] == 'started':
                if open_pt:
                    # vorheriger Start nie beendet -> als "läuft noch" abschließen, neuen öffnen
                    playthroughs.append(open_pt)
                open_pt = {
                    'title': e['title'], 'platform': e['platform'],
                    'started': e['date'], 'finished': None,
                    'is_replay': finished_count > 0,
                }
            elif e['status'] in ('finished', 'replayed'):
                if open_pt:
                    open_pt['finished'] = e['date']
                    if e['platform']:
                        open_pt['platform'] = e['platform']
                    playthroughs.append(open_pt)
                    open_pt = None
                else:
                    playthroughs.append({
                        'title': e['title'], 'platform': e['platform'],
                        'started': None, 'finished': e['date'],
                        'is_replay': finished_count > 0,
                    })
                finished_count += 1
            elif e['status'] == 'abandoned':
                if open_pt:
                    # Abbruchdatum als "finished" übernehmen (statt zu verwerfen) -
                    # so bleibt nachvollziehbar, wann ein Spiel abgebrochen wurde,
                    # nicht nur dass es abgebrochen wurde.
                    open_pt['finished'] = e['date']
                    open_pt['abandoned'] = True
                    playthroughs.append(open_pt)
                    open_pt = None
        if open_pt:
            playthroughs.append(open_pt)

    playthroughs.sort(key=lambda p: p['finished'] or p['started'] or '')
    return playthroughs


def import_backloggd_journal(text: str) -> dict:
    """Parst eingefügten Backloggd-Journal-Inhalt, legt Spiele in der Registry an
    und erzeugt Tagebuch-Einträge (fetch-once-cache-forever, dedupliziert).
    Unterstützt zwei Eingabeformate:
    - HTML-Quelltext der Journal-Seite (empfohlen: per "Element untersuchen" /
      outerHTML kopiert) -> exakte IGDB-IDs, Cover und Bewertungen aus dem Markup.
    - reiner Text-Export der Journal-Seite (Fallback, weniger zuverlässig, da
      Titel/Status per Heuristik aus der Zeilenstruktur erraten werden müssen)."""
    is_html = _looks_like_backloggd_html(text)
    if is_html:
        events = _parse_backloggd_html(text)
        playthroughs = _pair_backloggd_html_events(events)
    else:
        events = _parse_backloggd_events(text)
        playthroughs = _pair_backloggd_events(events)

    with _diary_lock:
        entries = load_diary()
        existing = {(e.get('key'), e.get('startDate'), e.get('finishDate'))
                    for e in entries if e.get('kind') == 'game'}

    added, skipped_dupe, games_touched = 0, 0, set()
    for pt in playthroughs:
        game = get_or_create_game(
            pt['title'],
            platform_hint=pt.get('platform', ''),
            igdb_id=pt.get('game_id'),
            known_cover_image_id=pt.get('cover_image_id'),
        )
        games_touched.add(game['key'])
        sig = (game['key'], pt['started'], pt['finished'])
        if sig in existing:
            skipped_dupe += 1
            continue

        # Backloggd-Bewertung ist 1-10, die App nutzt eine 0.5-5-Sterne-Skala.
        bl_rating = pt.get('rating')
        rating = (bl_rating / 2) if bl_rating else None

        with _diary_lock:
            entries = load_diary()
            entries.append({
                'id':        hashlib.md5(f"{time.time()}{pt['title']}{pt['started']}{pt['finished']}".encode()).hexdigest()[:12],
                'key':       game['key'],
                'kind':      'game',
                'title':     game.get('title') or pt['title'],
                'year':      game.get('year', ''),
                'season':    None,
                'poster':    game.get('cover'),
                'genre':     game.get('genre', ''),
                'rating':    rating,
                'date':      pt['finished'] or pt['started'] or time.strftime('%Y-%m-%d'),
                'startDate': pt['started'],
                'finishDate': pt['finished'],
                'platform':  pt.get('platform', ''),
                'comment':   '',
                'rewatch':   bool(pt.get('is_replay')),
                'noms':      [],
                'nomPeople': {},
                'mini':      False,
                'statusOverride': ('dropped' if pt.get('abandoned')
                                    else ('playing' if not pt['finished'] else None)),
                'imported':  True,
                'created':   time.time(),
            })
            save_diary(entries)
        existing.add(sig)
        added += 1

    return {
        'format': 'html' if is_html else 'text',
        'events_found': len(events),
        'playthroughs_found': len(playthroughs),
        'diary_added': added,
        'diary_skipped_duplicate': skipped_dupe,
        'games_touched': len(games_touched),
    }


# ── CSV-Import (Letterboxd / Serializd) ───────────────────────────────────────

# Spalten-Priorität: höher gewinnt, wenn mehrere Spalten aufs gleiche Feld zeigen.
# Wichtig bei Letterboxd: "Watched Date" (Sehdatum) schlägt "Date" (Eintragsdatum).
_COLPRIO = {
    'watched date': 10, 'watcheddate': 10, 'date watched': 10, 'watched on': 10,
    'date': 1, 'logdate': 1, 'log date': 1, 'dateadded': 0, 'date added': 0,
    'gesehen am': 10, 'sehdatum': 10, 'datum': 1,
}

# Spalten-Aliase (kleingeschrieben) → internes Feld
_COLMAP = {
    'name': 'title', 'title': 'title', 'film': 'title', 'movie': 'title',
    'show': 'title', 'show name': 'title', 'showname': 'title',
    'series': 'title', 'series name': 'title', 'series title': 'title',
    'show title': 'title',
    'year': 'year', 'release year': 'year', 'releaseyear': 'year',
    'watched date': 'date', 'watcheddate': 'date', 'date watched': 'date',
    'date': 'date', 'logdate': 'date', 'log date': 'date',
    'watched on': 'date', 'dateadded': 'date', 'date added': 'date',
    'rating': 'rating', 'your rating': 'rating', 'score': 'rating',
    'user rating': 'rating',
    'rewatch': 'rewatch', 'is rewatch': 'rewatch',
    'review': 'comment', 'comment': 'comment', 'notes': 'comment',
    'note': 'comment', 'tags': 'tags',
    'season': 'season', 'season number': 'season', 'seasonnumber': 'season',
    'season name': 'season_name',
    # Deutsche Spaltennamen
    'titel': 'title', 'name des films': 'title', 'serie': 'title',
    'jahr': 'year', 'erscheinungsjahr': 'year',
    'datum': 'date', 'gesehen am': 'date', 'sehdatum': 'date',
    'bewertung': 'rating', 'wertung': 'rating', 'sterne': 'rating',
    'kommentar': 'comment', 'notiz': 'comment', 'rezension': 'comment',
    'staffel': 'season', 'staffelnummer': 'season',
}

def _parse_rating(raw: str):
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # Sternsymbole (Letterboxd HTML-Export nutzt ★ / ½)
    if '★' in s or '½' in s:
        return s.count('★') + (0.5 if '½' in s else 0)
    try:
        v = float(s.replace(',', '.'))
    except ValueError:
        return None
    if v > 5:          # 10er-Skala (z.B. Serializd/IMDb)
        v = v / 2
    v = round(v * 2) / 2
    return max(0.5, min(5.0, v)) if v > 0 else None

def _parse_date(raw: str) -> str:
    s = (raw or '').strip()
    if not s:
        return ''
    s = s.split('T')[0].split(' ')[0]
    m = re.match(r'^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$', s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r'^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$', s)   # DD.MM.YYYY
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    for fmt in ('%B %d, %Y', '%b %d, %Y', '%d %B %Y'):
        try:
            return time.strftime('%Y-%m-%d', time.strptime(s, fmt))
        except ValueError:
            pass
    return ''

def _parse_season(raw, name_raw='') -> int | None:
    for v in (raw, name_raw):
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        m = re.search(r'(?:season|staffel|s)\s*(\d{1,3})', s, re.IGNORECASE)
        if m:
            return int(m.group(1))
        if s.isdigit():
            return int(s)
    return None

def _build_lib_index(cache: dict) -> dict:
    """Index (kind, normalisierter Titel) -> [(Jahr, Cache-Key, Cache-Eintrag), ...]"""
    lib = {}
    for k, e in cache.items():
        if e.get('not_found'):
            continue
        t = _norm_title(e.get('title_actual') or e.get('t', ''))
        if not t:
            continue
        kind = 'series' if k.startswith('series|') else 'movie'
        yr = str(e.get('year_actual') or e.get('y') or '')[:4]
        lib.setdefault((kind, t), []).append((yr, k, e))
    return lib

def _match_lib(lib: dict, kind: str, title: str, year: str):
    cands = lib.get((kind, _norm_title(title)))
    if not cands:
        return None, None
    if year:
        for yr, k, e in cands:
            if yr == str(year)[:4]:
                return k, e
    return cands[0][1], cands[0][2]


def import_csv(text: str, cache: dict) -> dict:
    """Importiert Letterboxd- oder Serializd-CSV. Erkennt Spalten automatisch."""
    import csv as _csv, io as _io

    text = text.lstrip('﻿')
    sample = text[:4000]
    try:
        dialect = _csv.Sniffer().sniff(sample, delimiters=',;\t')
    except Exception:
        dialect = _csv.excel
    rows = list(_csv.DictReader(_io.StringIO(text), dialect=dialect))
    if not rows:
        return {'ok': False, 'error': 'CSV enthält keine Zeilen'}

    # Spalten zuordnen (bei Mehrfachtreffern gewinnt die höhere Priorität)
    colmap, colprio = {}, {}
    for raw_col in (rows[0].keys()):
        if raw_col is None:
            continue
        low = raw_col.strip().lower()
        key = _COLMAP.get(low)
        if not key:
            continue
        prio = _COLPRIO.get(low, 5)
        if key not in colmap or prio > colprio[key]:
            colmap[key], colprio[key] = raw_col, prio
    if 'title' not in colmap:
        return {'ok': False, 'error': 'Keine Titelspalte gefunden. '
                                      f'Gefundene Spalten: {", ".join(str(c) for c in rows[0].keys())}'}

    has_season = 'season' in colmap or 'season_name' in colmap
    lib = _build_lib_index(cache)
    match = lambda kind, title, year: _match_lib(lib, kind, title, year)

    with _diary_lock:
        entries = load_diary()
        # Dublettenschutz
        seen = {(_norm_title(e.get('title')), e.get('date'), e.get('season'))
                for e in entries}

        added = skipped = matched = 0
        for r in rows:
            g = lambda f: (r.get(colmap[f]) or '').strip() if f in colmap else ''
            title = g('title')
            if not title:
                continue

            season = None
            if has_season:
                season = _parse_season(
                    r.get(colmap.get('season', ''), ''),
                    r.get(colmap.get('season_name', ''), '') or title
                )
            kind = 'season' if (has_season and season is not None) else 'movie'
            if kind == 'season':
                # "Breaking Bad: Season 3" → Titel bereinigen
                title = re.sub(r'\s*[:\-–]\s*(season|staffel)\s*\d+\s*$', '',
                               title, flags=re.IGNORECASE).strip()

            date = _parse_date(g('date'))
            sig = (_norm_title(title), date, season)
            if sig in seen:
                skipped += 1
                continue
            seen.add(sig)

            year = g('year')[:4]
            key, lib_e = match('series' if kind == 'season' else 'movie', title, year)
            if key:
                matched += 1

            rewatch = g('rewatch').strip().lower() in ('yes', 'true', '1', 'ja', 'y')
            comment = g('comment')
            tags = g('tags')
            if tags and not comment:
                comment = ''  # Tags nicht als Kommentar missbrauchen

            entries.append({
                'id':      hashlib.md5(f"{time.time()}{title}{date}{season}{added}".encode()).hexdigest()[:12],
                'key':     key or '',
                'kind':    kind,
                'title':   (lib_e.get('title_actual') if lib_e else None) or title,
                'year':    year or (str(lib_e.get('year_actual') or '')[:4] if lib_e else ''),
                'season':  season,
                'poster':  f"/poster/{lib_e['poster_local']}" if lib_e and lib_e.get('poster_local') else None,
                'genre':   (lib_e or {}).get('genre', ''),
                'rating':  _parse_rating(g('rating')),
                'date':    date,
                'comment': comment,
                'rewatch': rewatch,
                'tags':    tags,
                'noms':    [],
                'nomPeople': {},
                'imported': True,
                'created': time.time(),
            })
            added += 1

        save_diary(entries)

    return {'ok': True, 'added': added, 'skipped': skipped,
            'matched': matched, 'total': len(rows), 'entries': entries}


# ── Serializd-Import (HTML-Quelltext der Tagebuch-Seite) ──────────────────────
# Alternative zum CSV-Export: der Nutzer fügt den HTML-Quelltext der eigenen
# Serializd-"Diary"-Seite ein (z.B. per "Element untersuchen" -> outerHTML
# kopiert). Liefert zuverlässigere Daten als CSV, da TMDB-Show-ID und
# Cover-URL direkt im Markup stehen statt geraten werden zu müssen.
_SZ_MONTHS = {
    'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
    'july':7,'august':8,'september':9,'october':10,'november':11,'december':12,
}
_SZ_MONTHYEAR_RE  = re.compile(r'<h1>\s*([A-Za-z]+)\s+(\d{4})\s*</h1>')
_SZ_ENTRY_START_RE = re.compile(r'<div class="jsx-\w+ diary-log">')
_SZ_SHOWID_RE     = re.compile(r'/show/[^"]*-(\d+)"')
_SZ_POSTER_RE     = re.compile(r'src="(https://image\.tmdb\.org/[^"]+)"')
_SZ_TITLE_RE      = re.compile(r'<h2[^>]*>([^<]+)</h2>')
_SZ_SEASONTXT_RE  = re.compile(r'margin-top:\s*4px;"[^>]*>([^<]*)</div>')
_SZ_DATETXT_RE    = re.compile(r'color:\s*rgb\(179,\s*182,\s*180\);"[^>]*>([A-Za-z]+\s+\d{1,2})</div>')
_SZ_STAR_RE       = re.compile(r'rgb\(0,\s*169,\s*158\)')


def _looks_like_serializd_html(text: str) -> bool:
    return bool(re.search(r'class="jsx-\w+ diary-log"', text))


def _sz_parse_season(text: str, title: str) -> tuple:
    """Gibt (Staffel, ist_miniserie) zurück. Serializd zeigt 'Season N',
    'Miniseries'/'Limited Series' oder (Anzeige-Eigenheit) manchmal einfach
    nochmal den Titel - dann als reguläre Staffel 1 behandeln."""
    text = (text or '').strip()
    m = re.search(r'season\s*(\d+)', text, re.IGNORECASE)
    if m:
        return int(m.group(1)), False
    if re.search(r'mini-?series|limited\s*series', text, re.IGNORECASE):
        return 1, True
    return 1, False


def _parse_serializd_html(raw_html: str) -> list:
    """Zerlegt den Serializd-Diary-HTML-Quelltext in einzelne Einträge:
    {title, tmdb_id, poster_url, season, mini, date, rating}."""
    months = [(m.start(), _SZ_MONTHS[m.group(1).lower()], int(m.group(2)))
              for m in _SZ_MONTHYEAR_RE.finditer(raw_html) if m.group(1).lower() in _SZ_MONTHS]
    starts = [m.start() for m in _SZ_ENTRY_START_RE.finditer(raw_html)]

    events = []
    mi = 0
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(raw_html)
        chunk = raw_html[start:end]

        while mi + 1 < len(months) and months[mi + 1][0] <= start:
            mi += 1
        year = months[mi][2] if months and months[mi][0] <= start else None
        if not year:
            continue  # noch kein Monats-/Jahres-Header gesehen -> nicht datierbar

        title_m = _SZ_TITLE_RE.search(chunk)
        date_m  = _SZ_DATETXT_RE.search(chunk)
        if not title_m or not date_m:
            continue
        title = html.unescape(title_m.group(1)).strip()

        dm = re.match(r'([A-Za-z]+)\s+(\d{1,2})', date_m.group(1).strip())
        if not dm or dm.group(1).lower() not in _SZ_MONTHS:
            continue
        month = _SZ_MONTHS[dm.group(1).lower()]
        day = int(dm.group(2))

        season_m = _SZ_SEASONTXT_RE.search(chunk)
        season, is_mini = _sz_parse_season(season_m.group(1) if season_m else '', title)

        showid_m = _SZ_SHOWID_RE.search(chunk)
        poster_m = _SZ_POSTER_RE.search(chunk)
        rating_stars = len(_SZ_STAR_RE.findall(chunk))

        events.append({
            'title':      title,
            'tmdb_id':    int(showid_m.group(1)) if showid_m else None,
            'poster_url': poster_m.group(1) if poster_m else None,
            'season':     season,
            'mini':       is_mini,
            'date':       f"{year:04d}-{month:02d}-{day:02d}",
            'rating':     rating_stars if rating_stars > 0 else None,
        })
    return events


def import_serializd_html(raw_html: str, cache: dict) -> dict:
    """Importiert eine als HTML-Quelltext eingefügte Serializd-Tagebuchseite.
    Nutzt dieselbe Bibliotheks-Matching-/Dubletten-Logik wie import_csv, ergänzt
    um TMDB-Show-ID und eine direkte Cover-URL für zuverlässigere Zuordnung auch
    ohne Bibliotheks-Treffer (Cover-Download reuses den Diary-Cover-Key, siehe
    ensure_diary_poster, damit kein doppelter Poster-Cache-Namespace entsteht)."""
    events = _parse_serializd_html(raw_html)
    if not events:
        return {'ok': False, 'error': 'Keine Tagebuch-Einträge im eingefügten HTML gefunden'}

    lib = _build_lib_index(cache)

    with _diary_lock:
        entries = load_diary()
        seen = {(_norm_title(e.get('title')), e.get('date'), e.get('season'))
                for e in entries}

        added = skipped = matched = 0
        for ev in events:
            title, season, date = ev['title'], ev['season'], ev['date']
            sig = (_norm_title(title), date, season)
            if sig in seen:
                skipped += 1
                continue
            seen.add(sig)

            key, lib_e = _match_lib(lib, 'series', title, '')
            if key:
                matched += 1

            poster = f"/poster/{lib_e['poster_local']}" if lib_e and lib_e.get('poster_local') else None
            if not poster and ev.get('poster_url'):
                dkey = _dcov_key('series', title, '')
                fname = _dl_poster(ev['poster_url'], f"diary|{dkey}")
                poster = f"/poster/{fname}" if fname else None

            entries.append({
                'id':        hashlib.md5(f"{time.time()}{title}{date}{season}{added}".encode()).hexdigest()[:12],
                'key':       key or '',
                'kind':      'season',
                'title':     (lib_e.get('title_actual') if lib_e else None) or title,
                'year':      str(lib_e.get('year_actual') or '')[:4] if lib_e else '',
                'season':    season,
                'poster':    poster,
                'genre':     (lib_e or {}).get('genre', ''),
                'rating':    ev.get('rating'),
                'date':      date,
                'comment':   '',
                'rewatch':   False,
                'mini':      ev.get('mini', False),
                'noms':      [],
                'nomPeople': {},
                'tmdb_id':   ev.get('tmdb_id'),
                'imported':  True,
                'created':   time.time(),
            })
            added += 1

        save_diary(entries)

    return {'ok': True, 'added': added, 'skipped': skipped,
            'matched': matched, 'total': len(events), 'entries': entries}


# ── Letterboxd-Sync (öffentlicher RSS-Feed) ───────────────────────────────────
# Letterboxd bietet keine offene Schreib-API, aber jedes öffentliche Profil hat
# einen RSS-Feed mit den zuletzt geloggten Filmen (Titel, Jahr, Datum, Bewertung,
# Rewatch-Flag, Review-Text, TMDB-ID). Der Feed zeigt nur die letzten ~50
# Einträge - reicht für "bei jedem Start neue Einträge nachziehen" völlig aus.
_LB_NS = {'letterboxd': 'https://letterboxd.com', 'tmdb': 'https://themoviedb.org'}

def _fetch_letterboxd_diary(username: str) -> list:
    import xml.etree.ElementTree as ET

    url = f"https://letterboxd.com/{urllib.parse.quote(username.strip())}/rss/"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (ArcTime-Sync)'})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = r.read()

    root = ET.fromstring(data)
    items = root.findall('.//item')
    out = []

    for it in items:
        watched = (it.findtext('letterboxd:watchedDate', default='', namespaces=_LB_NS) or '').strip()
        if not watched:
            continue  # kein Tagebucheintrag (z.B. Liste/Kommentar ohne Sehdatum)

        title = (it.findtext('letterboxd:filmTitle', default='', namespaces=_LB_NS) or '').strip()
        year  = (it.findtext('letterboxd:filmYear',  default='', namespaces=_LB_NS) or '').strip()
        if not title:
            raw = it.findtext('title', default='') or ''
            m = re.match(r'^(.*?),\s*(\d{4})', raw)
            title = (m.group(1) if m else raw).strip()
            year  = year or (m.group(2) if m else '')

        rating_raw = (it.findtext('letterboxd:memberRating', default='', namespaces=_LB_NS) or '').strip()
        rewatch_raw = (it.findtext('letterboxd:rewatch', default='', namespaces=_LB_NS) or '').strip()
        tmdb_id = (it.findtext('tmdb:movieId', default='', namespaces=_LB_NS) or '').strip()

        desc = it.findtext('description', default='') or ''
        comment = re.sub(r'<[^>]+>', ' ', desc)
        comment = re.sub(r'\s+', ' ', comment).strip()
        # Letterboxd hängt der description immer den Boilerplate-Satz
        # "Watched on <Wochentag> <Monat> <Tag>, <Jahr>." voran (auch wenn keine
        # eigene Review existiert) - keine echte Nutzer-Notiz, also entfernen.
        comment = re.sub(
            r'^Watched on [A-Za-zÄÖÜäöü]+ [A-Za-zÄÖÜäöü]+ \d{1,2},\s*\d{4}\.\s*',
            '', comment
        ).strip()

        out.append({
            'title':   title,
            'year':    year[:4],
            'date':    watched,
            'rating':  _parse_rating(rating_raw) if rating_raw else None,
            'rewatch': rewatch_raw.lower() == 'yes',
            'comment': comment,
            'tmdb_id': tmdb_id,
            'link':    (it.findtext('link', default='') or '').strip(),
        })

    # Fallback, falls die Namespace-URIs sich mal ändern sollten: Rohtext-Regex
    if not out and items:
        raw_text = data.decode('utf-8', errors='ignore')
        for block in re.findall(r'<item>(.*?)</item>', raw_text, re.DOTALL):
            wd = re.search(r'<letterboxd:watchedDate>([^<]+)</letterboxd:watchedDate>', block)
            if not wd:
                continue
            ft = re.search(r'<letterboxd:filmTitle>([^<]+)</letterboxd:filmTitle>', block)
            fy = re.search(r'<letterboxd:filmYear>([^<]+)</letterboxd:filmYear>', block)
            mr = re.search(r'<letterboxd:memberRating>([^<]+)</letterboxd:memberRating>', block)
            rw = re.search(r'<letterboxd:rewatch>([^<]+)</letterboxd:rewatch>', block)
            out.append({
                'title':   (ft.group(1) if ft else '').strip(),
                'year':    (fy.group(1) if fy else '')[:4],
                'date':    wd.group(1).strip(),
                'rating':  _parse_rating(mr.group(1)) if mr else None,
                'rewatch': bool(rw and rw.group(1).strip().lower() == 'yes'),
                'comment': '',
                'tmdb_id': '',
                'link':    '',
            })

    return out


def sync_letterboxd(cache: dict, username: str) -> dict:
    if not username or not username.strip():
        return {'ok': False, 'error': 'Kein Letterboxd-Benutzername hinterlegt'}

    try:
        items = _fetch_letterboxd_diary(username)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {'ok': False, 'error': f'Letterboxd-Profil "{username}" nicht gefunden'}
        return {'ok': False, 'error': f'Letterboxd antwortet mit Fehler {e.code}'}
    except Exception as e:
        return {'ok': False, 'error': f'Letterboxd nicht erreichbar: {e}'}

    if not items:
        return {'ok': True, 'added': 0, 'skipped': 0, 'matched': 0, 'total': 0,
                'entries': load_diary()}

    lib = _build_lib_index(cache)

    with _diary_lock:
        entries = load_diary()
        seen = {(_norm_title(e.get('title')), e.get('date'), e.get('season'))
                for e in entries}

        added = skipped = matched = 0
        for it in items:
            sig = (_norm_title(it['title']), it['date'], None)
            if sig in seen:
                skipped += 1
                continue
            seen.add(sig)

            key, lib_e = _match_lib(lib, 'movie', it['title'], it['year'])
            if key:
                matched += 1

            entries.append({
                'id':      hashlib.md5(f"{time.time()}{it['title']}{it['date']}{added}".encode()).hexdigest()[:12],
                'key':     key or '',
                'kind':    'movie',
                'title':   (lib_e.get('title_actual') if lib_e else None) or it['title'],
                'year':    it['year'] or (str(lib_e.get('year_actual') or '')[:4] if lib_e else ''),
                'season':  None,
                'poster':  f"/poster/{lib_e['poster_local']}" if lib_e and lib_e.get('poster_local') else None,
                'genre':   (lib_e or {}).get('genre', ''),
                'rating':  it['rating'],
                'date':    it['date'],
                'comment': it['comment'],
                'rewatch': it['rewatch'],
                'tags':    '',
                'noms':    [],
                'nomPeople': {},
                'letterboxd':    True,
                'letterboxdUrl': it['link'],
                'created': time.time(),
            })
            added += 1

        save_diary(entries)

    cfg = load_config()
    cfg['lbUsername'] = username
    cfg['lbLastSync'] = time.time()
    save_config(cfg)

    return {'ok': True, 'added': added, 'skipped': skipped, 'matched': matched,
            'total': len(items), 'entries': entries}


