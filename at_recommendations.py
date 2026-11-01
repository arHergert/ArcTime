"""ArcTime - Empfehlungs-Engine.

Mehrere Algorithmen über einem gemeinsamen Kandidaten-Pool:

  Pool    TMDB-Empfehlungen (Collaborative Filtering über alle TMDB-Nutzer)
          bzw. IGDB-"similar games" zu jedem Titel aus dem Tagebuch, dazu die
          Werke von Regisseuren/Serienschöpfern/Studios/Darstellern, die du
          mehrfach gut bewertet hast.
  Profil  TF-IDF-gewichtete Merkmale (Genres, Keywords, Regie, Cast,
          Entwickler) deiner Tagebuch-Titel. Jeder Titel zählt mit seiner
          Bewertung, zentriert um deinen eigenen Schnitt (unter Schnitt =
          negatives Signal), optional mit exponentiellem Zeitabfall
          (Halbwertszeit). ✓/✗ auf Empfehlungskarten fließt als zusätzliches
          positives/negatives Signal ein.

  Modi    recent    Aktueller Geschmack (Halbwertszeit 90 Tage)
          alltime   Allzeit-Geschmack (kein Zeitabfall)
          because   "Weil du X mochtest" - gruppiert nach zuletzt gemochten Titeln
          people    Regie & Cast - weitere Werke deiner Lieblings-Macher
          gems      Geheimtipps - hoch bewertet, aber wenig bekannt
          surprise  Überrasch mich - Geschmack + Diversität (MMR-Reranking)

Alles von TMDB/IGDB wird einmal abgerufen und dauerhaft in recengine.json
gecacht (Hintergrund-Backfill in kleinen Runden). Das Ranking selbst ist
reines Python und läuft bei jedem Aufruf frisch, dadurch fließen neue
Tagebuch-Einträge und Feedback sofort ein.
"""
import json, math, random, re, threading, time, urllib.error, urllib.parse, urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from at_config import (IGDB_CLIENT_ID, IGDB_CLIENT_SECRET, REC_ENGINE_FILE, REC_FEEDBACK_FILE,
                       REC_HIDDEN_FILE, TMDB_KEY, _norm_title)
from at_metadata import _igdb_cover_url, _igdb_query, _rank_tmdb_results

MODES = ('recent', 'alltime', 'because', 'people', 'gems', 'surprise')

RECENT_HALF_LIFE_DAYS = 90     # "Aktueller Geschmack": nach 90 Tagen zählt ein Eintrag nur noch halb
PER_KIND_LIMIT = 80            # so viele Empfehlungen pro Art (Film/Serie/Spiel) je Modus
FULL_CANDIDATE_CAP = 900       # für so viele Top-Kandidaten werden Keywords/Credits nachgeladen
BACKFILL_WORKERS = 8


# ── Legacy (alte kuratierte Liste) ─────────────────────────────────────────────
def _rec_key(kind: str, title: str, year: str) -> str:
    return f"{kind}|{_norm_title(title)}|{(year or '')[:4]}"

def load_rec_hidden() -> list:
    """Ausgeblendete Titel aus der früheren kuratierten Liste - bleiben ausgeblendet."""
    try:
        d = json.loads(REC_HIDDEN_FILE.read_text('utf-8'))
        return d if isinstance(d, list) else []
    except Exception:
        return []


# ── Feedback (✓ / Qualität / ✗) ────────────────────────────────────────────────
_fb_lock = threading.Lock()
# Signal fürs Geschmacksprofil: 'quality' = Thema/Stil passt (leicht positiv),
# senkt aber über die Qualitätsschwelle (siehe _Ctx.quality_factor) schwächer
# bewertete Titel ab.
FEEDBACK_SIGNAL = {'like': 0.7, 'quality': 0.3, 'dislike': -0.8}

def load_feedback() -> dict:
    try:
        d = json.loads(REC_FEEDBACK_FILE.read_text('utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def set_feedback(item_id: str, verdict: str) -> bool:
    """verdict: 'like' (✓ gute Empfehlung, angefangen), 'quality' (Thema passt,
    aber qualitativ nicht gut genug) oder 'dislike' (✗ nicht mein
    Geschmack). Beides blendet den Titel dauerhaft aus; zusätzlich fließt er als
    positives bzw. negatives Signal ins Geschmacksprofil ein."""
    if verdict not in FEEDBACK_SIGNAL or not item_id:
        return False
    with _lock:
        it = _load()['items'].get(item_id) or {}
    with _fb_lock:
        fb = load_feedback()
        fb[item_id] = {'verdict': verdict, 'ts': time.time(), 'title': it.get('title', ''),
                       'kind': it.get('kind', item_id.split(':')[0]), 'year': it.get('year', ''),
                       'rating': it.get('rating')}
        REC_FEEDBACK_FILE.write_text(json.dumps(fb, ensure_ascii=False, indent=1), 'utf-8')
    return True


# ── Persistenz (recengine.json) ────────────────────────────────────────────────
_lock = threading.RLock()        # schützt _db (lesen/mergen/speichern)
_bf_lock = threading.Lock()      # nur ein Backfill gleichzeitig
_db = None
_version = 0                     # steigt bei jeder Änderung -> Vektor-Cache invalidieren

def _load() -> dict:
    global _db
    if _db is None:
        try:
            _db = json.loads(REC_ENGINE_FILE.read_text('utf-8'))
        except Exception:
            _db = {}
        _db.setdefault('items', {})    # id -> Titel-Daten ("movie:123", "series:1399", "game:1942")
        _db.setdefault('seeds', {})    # Tagebuch-Seed-Key -> id ('' = bei TMDB/IGDB nicht gefunden)
        _db.setdefault('people', {})   # Personen-Key ("d:525") -> {'ts', 'items': [ids]}
    return _db

def _save():
    tmp = REC_ENGINE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(_db, ensure_ascii=False, separators=(',', ':')), 'utf-8')
    tmp.replace(REC_ENGINE_FILE)

def _merge_item(it: dict):
    """Neue Daten einpflegen, ohne vorhandene Vollinfos durch Teilinfos (aus
    Empfehlungs-/Credit-Listen) zu überschreiben."""
    items = _db['items']
    old = items.get(it['id'])
    if old is None:
        items[it['id']] = it
    elif old.get('full') and not it.get('full'):
        for k, v in it.items():
            if k not in old or old[k] in (None, '', []):
                old[k] = v
    else:
        recs = old.get('recs')
        old.update(it)
        if 'recs' not in it and recs is not None:
            old['recs'] = recs


# ── TMDB / IGDB ────────────────────────────────────────────────────────────────
class _Offline(Exception):
    """Netzwerk-/Serverfehler - wird NICHT als "nicht gefunden" gecacht."""

def _tmdb(path: str, **params) -> dict:
    if not TMDB_KEY:
        raise _Offline('kein TMDB-Key')
    url = f"https://api.themoviedb.org/3/{path}?" + urllib.parse.urlencode({'api_key': TMDB_KEY, **params})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=12) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {}
            if e.code == 429:
                time.sleep(1.0 + attempt)
                continue
            raise _Offline(f'TMDB HTTP {e.code}')
        except Exception as e:
            if attempt == 2:
                raise _Offline(str(e))
            time.sleep(0.4)
    raise _Offline('TMDB Rate-Limit')

def _igdb_ok() -> bool:
    return bool(IGDB_CLIENT_ID and IGDB_CLIENT_SECRET)

def _igdb(body: str) -> list:
    try:
        return _igdb_query('games', body) or []
    except Exception as e:
        raise _Offline(f'IGDB: {e}')

_TMDB_GENRES = {
    28: 'Action', 12: 'Adventure', 16: 'Animation', 35: 'Comedy', 80: 'Crime', 99: 'Documentary',
    18: 'Drama', 10751: 'Family', 14: 'Fantasy', 36: 'History', 27: 'Horror', 10402: 'Music',
    9648: 'Mystery', 10749: 'Romance', 878: 'Science Fiction', 10770: 'TV Movie', 53: 'Thriller',
    10752: 'War', 37: 'Western', 10759: 'Action & Adventure', 10762: 'Kids', 10763: 'News',
    10764: 'Reality', 10765: 'Sci-Fi & Fantasy', 10766: 'Soap', 10767: 'Talk', 10768: 'War & Politics',
}
# Serien-Sammelgenres auf die Film-Genres abbilden, damit Filme und Serien
# denselben Merkmalsraum teilen.
_GENRE_SPLIT = {
    'Action & Adventure': ('Action', 'Adventure'), 'Sci-Fi & Fantasy': ('Science Fiction', 'Fantasy'),
    'War & Politics': ('War',), 'Sci-Fi': ('Science Fiction',),
}
_SERIES_EXCLUDE_GENRES = {'Kids', 'News', 'Reality', 'Soap', 'Talk'}
# IGDB-Themes, die inhaltlich Film-Genres entsprechen
_THEME_MAP = {'science fiction': 'science fiction', 'historical': 'history', 'warfare': 'war'}

IGDB_FIELDS = ('fields name,first_release_date,summary,cover.image_id,genres.name,themes.name,'
               'keywords.name,involved_companies.company.name,involved_companies.developer,'
               'platforms.abbreviation,total_rating,total_rating_count,similar_games;')

def _split_genres(names) -> list:
    out = []
    for n in names:
        for g in _GENRE_SPLIT.get(n, (n,)):
            if g not in out:
                out.append(g)
    return out

def _tmdb_img(path) -> str:
    return f"https://image.tmdb.org/t/p/w342{path}" if path else ''

def _item_from_tmdb_list(r: dict, media: str) -> dict:
    """Teil-Datensatz aus einer Liste (Empfehlungen/Credits): Genres, Beschreibung,
    Bewertung, Poster - aber noch ohne Keywords/Credits."""
    kind = 'series' if media == 'tv' else 'movie'
    d = (r.get('first_air_date') if media == 'tv' else r.get('release_date')) or ''
    title = (r.get('name') or r.get('original_name')) if media == 'tv' else (r.get('title') or r.get('original_title'))
    names = r.get('genre_names') or [_TMDB_GENRES[g] for g in (r.get('genre_ids') or []) if g in _TMDB_GENRES]
    return {
        'id': f"{kind}:{r['id']}", 'kind': kind, 'tmdb_id': r['id'], 'title': title or '',
        'date': d, 'year': d[:4], 'overview': r.get('overview') or '',
        'poster': _tmdb_img(r.get('poster_path')), 'genres': _split_genres(names),
        'raw_genres': names,
        'rating': r.get('vote_average'), 'votes': r.get('vote_count') or 0,
        'lang': r.get('original_language') or '', 'full': False,
        'popularity': r.get('popularity') or 0,
    }

def _item_from_tmdb_full(d: dict, media: str) -> dict:
    it = _item_from_tmdb_list({**d, 'genre_names': [g['name'] for g in d.get('genres') or [] if g.get('name')]}, media)
    kw = d.get('keywords') or {}
    it['keywords'] = [k['name'] for k in (kw.get('keywords') or kw.get('results') or []) if k.get('name')][:25]
    cr = d.get('credits') or {}
    it['cast'] = [[c['id'], c.get('name', '')] for c in (cr.get('cast') or [])[:6]]
    if media == 'movie':
        it['directors'] = [[c['id'], c.get('name', '')] for c in cr.get('crew') or [] if c.get('job') == 'Director'][:3]
        it['runtime'] = d.get('runtime') or ''
    else:
        it['creators'] = [[c['id'], c.get('name', '')] for c in d.get('created_by') or []][:3]
        rt = d.get('episode_run_time') or []
        it['runtime'] = rt[0] if rt else ''
        it['seasons'] = d.get('number_of_seasons') or ''
    it['imdb_id'] = d.get('imdb_id') or (d.get('external_ids') or {}).get('imdb_id') or ''
    it['full'] = True
    return it

def _item_from_igdb(g: dict) -> dict:
    ts = g.get('first_release_date')
    d = time.strftime('%Y-%m-%d', time.gmtime(ts)) if ts else ''
    cover = (g.get('cover') or {}).get('image_id') if isinstance(g.get('cover'), dict) else None
    devs = [[ic['company']['id'], ic['company'].get('name', '')]
            for ic in g.get('involved_companies') or []
            if isinstance(ic, dict) and ic.get('developer') and isinstance(ic.get('company'), dict)]
    rating = g.get('total_rating')
    it = {
        'id': f"game:{g['id']}", 'kind': 'game', 'igdb_id': g['id'], 'title': g.get('name', ''),
        'date': d, 'year': d[:4], 'overview': g.get('summary') or '',
        'poster': _igdb_cover_url(cover) if cover else '',
        'genres': [x['name'] for x in g.get('genres') or [] if isinstance(x, dict) and 'name' in x],
        'themes': [x['name'] for x in g.get('themes') or [] if isinstance(x, dict) and 'name' in x],
        'keywords': [x['name'] for x in g.get('keywords') or [] if isinstance(x, dict) and 'name' in x][:25],
        'companies': devs[:3],
        'rating': round(rating / 10, 2) if rating else None,     # IGDB 0-100 -> 0-10 wie TMDB
        'votes': g.get('total_rating_count') or 0,
        'platforms': [p.get('abbreviation') or p.get('name') for p in g.get('platforms') or []
                      if isinstance(p, dict) and (p.get('abbreviation') or p.get('name'))],
        'full': True,
    }
    if g.get('similar_games') is not None:
        it['recs'] = [f"game:{x}" for x in g['similar_games'] if isinstance(x, int)]
    return it


# ── Seeds (= Tagebuch-Titel + Feedback) ────────────────────────────────────────
def _entry_date(e: dict) -> date | None:
    for k in ('date', 'finishDate', 'startDate'):
        v = e.get(k)
        if v:
            try:
                return date.fromisoformat(str(v)[:10])
            except ValueError:
                pass
    if e.get('created'):
        try:
            return date.fromtimestamp(float(e['created']))
        except Exception:
            pass
    return None

def _entry_signal(e: dict, mean: float) -> float:
    """Wie sehr zeigt dieser Eintrag, dass dir so etwas gefällt? -1 .. +1,
    zentriert um deinen eigenen Bewertungsschnitt."""
    r = e.get('rating')
    if isinstance(r, (int, float)) and r > 0:
        s = max(-1.0, min(1.0, (r - mean) / 1.25))
    else:
        s = 0.2                                   # gesehen/gespielt, ohne Wertung: leichtes Interesse
    if e.get('statusOverride') == 'dropped':
        s = min(s, 0.0) - 0.5                     # abgebrochen
    if e.get('rewatch'):
        s += 0.2
    return max(-1.0, min(1.0, s))

def _seed_key(e: dict) -> str | None:
    kind = e.get('kind')
    t = _norm_title(e.get('title', ''))
    if not t:
        return None
    if kind == 'movie':
        return f"movie|{t}|{(e.get('year') or '')[:4]}"
    if kind == 'season':
        return f"series|{t}"
    if kind == 'game':
        m = re.match(r'game\|id(\d+)$', e.get('key') or '')
        return f"game|id{m.group(1)}" if m else f"game|{t}"
    return None

def _collect_seeds(diary: list, feedback: dict) -> dict:
    ratings = [e['rating'] for e in diary if isinstance(e.get('rating'), (int, float)) and e['rating'] > 0]
    mean = sum(ratings) / len(ratings) if ratings else 3.5
    seeds = {}
    for e in diary:
        sk = _seed_key(e)
        if not sk:
            continue
        s = seeds.setdefault(sk, {
            'kind': 'series' if e['kind'] == 'season' else e['kind'], 'title': e.get('title', ''),
            'year': (e.get('year') or '')[:4], 'tmdb_id': None, 'igdb_id': None, 'entries': []})
        if e['kind'] == 'season' and e.get('tmdb_id'):
            s['tmdb_id'] = e['tmdb_id']
        if e['kind'] == 'game':
            m = re.match(r'game\|id(\d+)$', e.get('key') or '')
            if m:
                s['igdb_id'] = int(m.group(1))
        s['entries'].append((_entry_signal(e, mean), _entry_date(e)))
    for iid, f in feedback.items():
        d = date.fromtimestamp(f.get('ts') or time.time())
        seeds[f"fb|{iid}"] = {'kind': f.get('kind', ''), 'title': f.get('title', ''), 'year': f.get('year', ''),
                              'item_id': iid, 'feedback': f.get('verdict'),
                              'entries': [(FEEDBACK_SIGNAL.get(f.get('verdict'), 0.0), d)]}
    return seeds

def _seed_weight(s: dict, half_life: float | None, today: date) -> float:
    sigs = [x for x, _ in s['entries']]
    dates = [d for _, d in s['entries'] if d]
    sig = sum(sigs) / len(sigs)
    rec = 1.0
    if half_life:
        age = (today - max(dates)).days if dates else 3650
        rec = 0.5 ** (max(0, age) / half_life)
    return sig * rec * (1 + 0.15 * min(len(sigs) - 1, 3))

def _seed_item(sk: str, s: dict, db: dict) -> str | None:
    if 'item_id' in s:
        return s['item_id'] if s['item_id'] in db['items'] else None
    iid = db['seeds'].get(sk)
    return iid if iid and iid in db['items'] else None


# ── Merkmale / TF-IDF ──────────────────────────────────────────────────────────
_TYPE_W = {'g': 0.5, 'k': 1.0, 'd': 1.5, 'cr': 1.3, 'co': 1.2, 'c': 0.45}
# TMDB-Keywords ohne Aussage über den Geschmack
_DLC_RE = re.compile(r'\b(dlc|costume|outfit|soundtrack|season pass|bundle|pack|skin|'
                     r'expansion pass|deluxe edition|upgrade|artbook|art book)\b', re.I)
_KW_IGNORE = {'duringcreditsstinger', 'aftercreditsstinger', 'woman director', 'short film'}
# zählen fürs Profil, sind als Begründung aber zu allgemein
_KW_GENERIC = {'based on novel or book', 'based on true story', 'based on comic', 'biography',
               'sequel', 'remake', 'based on play or musical', 'based on short story',
               'based on memoir or autobiography', 'friendship', 'love', 'murder', 'family',
               'based on video game', 'based on young adult novel', 'miniseries'}

def _raw_feats(it: dict, extra: dict) -> set:
    f = {'g:' + g.lower() for g in it.get('genres') or []}
    for t in it.get('themes') or []:
        t = t.lower()
        f.add('g:' + _THEME_MAP.get(t, t))
    f |= {'k:' + k.lower() for k in it.get('keywords') or [] if k.lower() not in _KW_IGNORE}
    for role, field in (('d', 'directors'), ('cr', 'creators'), ('co', 'companies'), ('c', 'cast')):
        f |= {f"{role}:{pid}" for pid, _ in it.get(field) or []}
    f |= extra.get(it['id'], set())
    return f

_vec_cache = {'v': -1}

def _vectors(db: dict):
    """L2-normierte TF-IDF-Vektoren aller Titel (gecacht, bis sich der Pool ändert)."""
    if _vec_cache['v'] == _version:
        return _vec_cache
    extra = defaultdict(set)
    for pkey, p in db['people'].items():
        for iid in p.get('items') or []:
            extra[iid].add(pkey)
    feats = {iid: _raw_feats(it, extra) for iid, it in db['items'].items()}
    df = Counter(f for fs in feats.values() for f in fs)
    n = len(feats) or 1
    idf = {f: math.log((n + 1) / (c + 1)) + 1 for f, c in df.items()}
    vecs = {}
    for iid, fs in feats.items():
        v = {f: _TYPE_W.get(f.split(':', 1)[0], 1.0) * idf[f] for f in fs}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs[iid] = {f: x / norm for f, x in v.items()}
    names = {}
    for it in db['items'].values():
        for role, field in (('d', 'directors'), ('cr', 'creators'), ('co', 'companies'), ('c', 'cast')):
            for pid, name in it.get(field) or []:
                names[f"{role}:{pid}"] = name
    for pkey, p in db['people'].items():
        if p.get('name'):
            names[pkey] = p['name']
    _vec_cache.update(v=_version, vecs=vecs, names=names)
    return _vec_cache

def _dot(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(f, 0.0) for f, x in a.items())

def _domain(kind: str) -> str:
    return 'game' if kind == 'game' else 'video'

def _pf(pos: int) -> float:
    """Gewicht einer Empfehlung nach Listenplatz (Platz 1 zählt am meisten)."""
    return 1.0 / (1.0 + 0.08 * pos)

def _quality(it: dict) -> float:
    """Bayes-geglättete Bewertung 0..1 (wenige Stimmen -> Richtung Durchschnitt)."""
    r, v = it.get('rating'), it.get('votes') or 0
    if not r:
        return 0.3
    c, m = (7.2, 8) if it['kind'] == 'game' else (6.8, 150)
    q = (v / (v + m)) * r + (m / (v + m)) * c
    return max(0.0, min(1.0, (q - 5.5) / 3.0))


# ── Kontext für ein Ranking ────────────────────────────────────────────────────
def _have_sets(state: dict, games: dict, diary: list, db: dict, seeds: dict):
    ids, titles = set(), set()
    for v in state.values():
        grp = 'series' if v.get('kind') == 'series' else 'movie'
        if v.get('tmdb_id'):
            ids.add(f"{grp}:{v['tmdb_id']}")
        y = str(v.get('year_actual') or v.get('y') or '')[:4]
        for t in (v.get('title_actual'), v.get('t')):
            if t:
                titles.add((grp, _norm_title(t), y if grp == 'movie' else ''))
    for g in games.values():
        if g.get('igdb_id'):
            ids.add(f"game:{g['igdb_id']}")
        titles.add(('game', _norm_title(g.get('title', '')), ''))
    for e in diary:
        grp = {'movie': 'movie', 'season': 'series', 'game': 'game'}.get(e.get('kind'))
        if not grp:
            continue
        titles.add((grp, _norm_title(e.get('title', '')), (e.get('year') or '')[:4] if grp == 'movie' else ''))
        if grp == 'series' and e.get('tmdb_id'):
            ids.add(f"series:{e['tmdb_id']}")
    for h in load_rec_hidden():                  # alte kuratierte "ausgeblendet"-Liste
        parts = h.split('|')
        if len(parts) >= 2:
            titles.add((parts[0], parts[1], parts[2] if parts[0] == 'movie' and len(parts) > 2 else ''))
    for sk, s in seeds.items():
        if 'item_id' in s:                       # ✓/✗-Feedback: nie wieder empfehlen
            ids.add(s['item_id'])
        iid = _seed_item(sk, s, db)
        if iid:
            ids.add(iid)
    return ids, titles

def _title_known(it: dict, titles: set) -> bool:
    nt = _norm_title(it.get('title', ''))
    if it['kind'] != 'movie':
        return (it['kind'], nt, '') in titles
    y = it.get('year') or ''
    if not y:
        return ('movie', nt, '') in titles
    try:
        yi = int(y)
        return any(('movie', nt, str(yy)) in titles for yy in (yi - 1, yi, yi + 1)) or ('movie', nt, '') in titles
    except ValueError:
        return ('movie', nt, y) in titles

MIN_REC_YEAR = '1980'

def _eligible(it: dict, have_ids: set, titles: set, today_iso: str) -> bool:
    if it.get('gone') or it['id'] in have_ids:
        return False
    d = it.get('date') or ''
    if not d or d > today_iso:                   # unveröffentlicht / ohne Datum
        return False
    if d[:4] < MIN_REC_YEAR:                     # nichts vor 1980 empfehlen
        return False
    k = it['kind']
    if k == 'movie' and (it.get('votes') or 0) < 25:
        return False
    if k == 'series':
        if (it.get('votes') or 0) < 15:
            return False
        if set(it.get('raw_genres') or []) & _SERIES_EXCLUDE_GENRES:
            return False
    if not it.get('title'):
        return False
    return not _title_known(it, titles)

class _Ctx:
    """Alles, was die Modi gemeinsam brauchen - einmal pro Anfrage berechnet."""
    def __init__(self, state, games, diary, db):
        self.db = db
        self.items = db['items']
        self.today = date.today()
        self.seeds = _collect_seeds(diary, load_feedback())
        self.have_ids, self.titles = _have_sets(state, games, diary, db, self.seeds)
        vc = _vectors(db)
        self.vecs, self.names = vc['vecs'], vc['names']
        # Seed-Titel (Tagebuch-Schreibweise) und Seed-Item-Zuordnung
        self.seed_items = {}
        self.seed_title = {}
        self.seed_last = {}
        sigs = defaultdict(list)
        for sk, s in self.seeds.items():
            iid = _seed_item(sk, s, db)
            if iid:
                self.seed_items[sk] = iid
                self.seed_title.setdefault(iid, s['title'] or self.items[iid].get('title', ''))
                sigs[iid] += [x for x, _ in s['entries']]
                ds = [d for _, d in s['entries'] if d]
                if ds:
                    self.seed_last[iid] = max(max(ds), self.seed_last.get(iid, date.min))
        self.seed_sig = {iid: sum(v) / len(v) for iid, v in sigs.items()}
        today_iso = self.today.isoformat()
        self.cands = [iid for iid, it in self.items.items() if _eligible(it, self.have_ids, self.titles, today_iso)]
        # Rückwärts-Index: Kandidat -> [(Seed-Item, Listenplatz)]
        self.rec_rev = defaultdict(list)
        for iid in set(self.seed_items.values()):
            for pos, cid in enumerate(self.items[iid].get('recs') or []):
                self.rec_rev[cid].append((iid, pos))
        # Kurzfilme raus (z.B. frühe Kurzfilme eines Lieblingsregisseurs). Filme,
        # die nur aus einer Werkliste stammen, erst nach dem Detail-Abruf zeigen -
        # vorher ist die Laufzeit unbekannt.
        def _feature_film(cid):
            it = self.items[cid]
            if it['kind'] != 'movie':
                return True
            if not it.get('full'):
                return cid in self.rec_rev
            rt = it.get('runtime')
            kws = {k.lower() for k in it.get('keywords') or []}
            return not ((isinstance(rt, (int, float)) and 0 < rt < 40) or 'short film' in kws)
        self.cands = [c for c in self.cands if _feature_film(c)]
        # Spiele: DLCs, Kostüme, Soundtracks, Bundles und Editionen bereits
        # bekannter Spiele ausfiltern (IGDB "similar games" enthält die teils).
        known_games = sorted({t[1] for t in self.titles if t[0] == 'game' and len(t[1]) >= 4}, key=len)
        def _main_game(cid):
            it = self.items[cid]
            if it['kind'] != 'game':
                return True
            t = it.get('title', '')
            if _DLC_RE.search(t):
                return False
            nt = _norm_title(t)
            return not any(nt.startswith(k) and nt != k for k in known_games)
        self.cands = [c for c in self.cands if _main_game(c)]
        self._weights = {}
        self._inv = {}
        # Qualitätsschwelle je Bereich aus "Thema passt, Qualität nicht"-Feedback:
        # Mittel der Bewertungen dieser Titel. Kandidaten darunter werden
        # deutlich abgewertet, knapp darüber noch leicht.
        qr = defaultdict(list)
        for iid, f in load_feedback().items():
            if f.get('verdict') != 'quality':
                continue
            r = (self.items.get(iid) or {}).get('rating') or f.get('rating')
            if r:
                qr[_domain(f.get('kind') or iid.split(':')[0])].append(float(r))
        self.qfloor = {d: sum(v) / len(v) for d, v in qr.items()}

    def quality_factor(self, cid: str) -> float:
        it = self.items[cid]
        floor = self.qfloor.get(_domain(it['kind']))
        r = it.get('rating')
        if floor is None or not r:
            return 1.0
        if r <= floor:
            return 0.5
        return min(1.0, 0.5 + 0.5 * (r - floor) / 0.5)   # volle Wertung ab Schwelle + 0.5

    def inverted(self, half_life, domain: str):
        """Merkmal -> [(Seed-Item, Gewicht x Merkmalswert)] nur für gemochte Seeds -
        macht die Begründungen schnell (statt jeden Kandidaten mit jedem Seed zu vergleichen)."""
        if (half_life, domain) not in self._inv:
            inv = defaultdict(list)
            for sid, wt in self.weights(half_life).items():
                if wt > 0 and _domain(self.items[sid]['kind']) == domain:
                    for f, y in self.vecs.get(sid, {}).items():
                        inv[f].append((sid, wt * y))
            self._inv[(half_life, domain)] = inv
        return self._inv[(half_life, domain)]

    def weights(self, half_life):
        """Seed-Item -> Gewicht (Bewertungssignal x Zeitabfall)."""
        if half_life not in self._weights:
            w = defaultdict(float)
            for sk, iid in self.seed_items.items():
                w[iid] += _seed_weight(self.seeds[sk], half_life, self.today)
            self._weights[half_life] = dict(w)
        return self._weights[half_life]

    def taste(self, half_life) -> dict:
        """Kandidat -> Score 0..1 aus Inhaltsähnlichkeit (TF-IDF-Profil),
        Co-Empfehlung (TMDB/IGDB-Empfehlungen deiner Seeds) und Qualität."""
        w = self.weights(half_life)
        prof = {'video': defaultdict(float), 'game': defaultdict(float)}
        for iid, wt in w.items():
            if wt == 0:
                continue
            p = prof[_domain(self.items[iid]['kind'])]
            for f, x in self.vecs.get(iid, {}).items():
                p[f] += wt * x
        pnorm = {d: math.sqrt(sum(x * x for x in p.values())) or 1.0 for d, p in prof.items()}
        content, corec = {}, {}
        for cid in self.cands:
            it = self.items[cid]
            dom = _domain(it['kind'])
            c = _dot(self.vecs.get(cid, {}), prof[dom]) / pnorm[dom]
            if not it.get('full'):
                c *= 0.6                          # nur Genres bekannt -> unsicherer
            content[cid] = c
            corec[cid] = sum(w.get(s, 0.0) * _pf(pos) for s, pos in self.rec_rev.get(cid, ()))
        out = {}
        for kind in ('movie', 'series', 'game'):
            ids = [c for c in self.cands if self.items[c]['kind'] == kind]
            if not ids:
                continue
            mc = max((content[c] for c in ids), default=0) or 1.0
            mr = max((corec[c] for c in ids), default=0) or 1.0
            for c in ids:
                cn = max(0.0, content[c]) / mc if mc > 0 else 0.0
                rn = corec[c] / mr if mr > 0 else 0.0
                out[c] = max(0.0, 0.5 * cn + 0.4 * rn + 0.1 * _quality(self.items[c])) * self.quality_factor(c)
        return out

    def explain(self, cid: str, half_life, prefix: str = '', skip: str | None = None) -> str:
        """Begründung: die Seeds, die am meisten zum Score beitragen, plus die
        stärksten gemeinsamen Merkmale (Regie, Studio, Themen)."""
        w = self.weights(half_life)
        inv = self.inverted(half_life, _domain(self.items[cid]['kind']))
        vc = self.vecs.get(cid, {})
        contrib = defaultdict(float)
        shared = defaultdict(float)
        for f, x in vc.items():
            for sid, wy in inv.get(f, ()):
                contrib[sid] += x * wy
                if not f.startswith('g:'):
                    shared[f] += x * wy
        for sid, pos in self.rec_rev.get(cid, ()):
            if w.get(sid, 0) > 0:
                contrib[sid] += w[sid] * _pf(pos) * 0.5
        contrib.pop(skip, None)
        top = [s for s, v in sorted(contrib.items(), key=lambda kv: -kv[1])[:2] if v > 0]
        parts = []
        if prefix:
            parts.append(prefix)
        if top:
            names = ' und '.join(f"„{self.seed_title.get(s) or self.items[s]['title']}“" for s in top)
            parts.append(f"Auch ähnlich zu {names}" if skip else f"Weil du {names} mochtest")
        feat = _describe_features([f for f, _ in sorted(shared.items(), key=lambda kv: -kv[1])
                                   if f[2:] not in _KW_GENERIC and 'award' not in f][:4], self.names)
        if feat:
            parts.append(feat)
        return ' · '.join(parts) or 'Passt zu deinem Geschmacksprofil'


_ROLE_LBL = {'d': 'Regie', 'cr': 'Serienschöpfer', 'co': 'Entwickler', 'c': 'Mit'}

def _describe_features(feats: list, names: dict) -> str:
    people = [f for f in feats if not f.startswith('k:')]
    kws = [f[2:] for f in feats if f.startswith('k:') and f[2:] not in _KW_GENERIC and 'award' not in f]
    if people and people[0] in names:
        f = people[0]
        return f"{_ROLE_LBL.get(f.split(':')[0], '')}: {names[f]}" if not f.startswith('c:') else f"Mit {names[f]}"
    if kws:
        return 'Themen: ' + ', '.join(kws[:2])
    return ''


# ── Ausgabe ────────────────────────────────────────────────────────────────────
def _out(it: dict, score: float, hook: str, group: str | None = None) -> dict:
    return {
        'key': it['id'], 'kind': it['kind'], 'title': it.get('title', ''), 'year': it.get('year', ''),
        'poster': it.get('poster') or None, 'imdb_id': it.get('imdb_id') or '',
        'genre': ', '.join(it.get('genres') or []),
        'plot': it.get('overview', ''), 'runtime': it.get('runtime', ''),
        'rating': it.get('rating'), 'votes': it.get('votes'),
        'total_seasons': it.get('seasons', ''), 'platforms': it.get('platforms') or [],
        'match': max(1, min(99, round(score * 100))), 'hook': hook, 'group': group,
    }

def _top_per_kind(ctx: _Ctx, scores: dict, limit: int = PER_KIND_LIMIT) -> list:
    out = []
    for kind in ('movie', 'series', 'game'):
        ids = sorted((c for c in scores if ctx.items[c]['kind'] == kind), key=lambda c: -scores[c])
        out += ids[:limit]
    return out

def _mode_taste(ctx: _Ctx, half_life) -> list:
    sc = ctx.taste(half_life)
    return [_out(ctx.items[c], sc[c], ctx.explain(c, half_life)) for c in _top_per_kind(ctx, sc)]

def _mode_because(ctx: _Ctx) -> list:
    sc = ctx.taste(None)
    liked = [(last, iid) for iid, last in ctx.seed_last.items()
             if ctx.seed_sig.get(iid, 0) >= 0.3 and ctx.items[iid].get('recs')]
    liked.sort(reverse=True)
    used, out, groups = set(), [], 0
    for last, sid in liked:
        if groups >= 14:
            break
        cand = []
        for pos, cid in enumerate(ctx.items[sid]['recs']):
            if cid in sc and cid not in used:
                cand.append((0.6 * _pf(pos) + 0.4 * sc[cid], cid))
        cand.sort(reverse=True)
        cand = cand[:6]
        if len(cand) < 2:
            continue
        groups += 1
        title = ctx.seed_title.get(sid) or ctx.items[sid]['title']
        label = f"Weil du „{title}“ mochtest"
        for s, cid in cand:
            used.add(cid)
            out.append(_out(ctx.items[cid], s, ctx.explain(cid, None, skip=sid), group=label))
    return out

def _people_weights(ctx: _Ctx) -> list:
    """Macher, von denen du mehrere Werke gut fandest: [(key, gewicht, anzahl)]."""
    pw, pc = defaultdict(float), Counter()
    for iid, wt in ctx.weights(None).items():
        if wt <= 0:
            continue
        it = ctx.items[iid]
        for role, field, n in (('d', 'directors', 3), ('cr', 'creators', 3), ('co', 'companies', 3), ('c', 'cast', 4)):
            for pid, _ in (it.get(field) or [])[:n]:
                k = f"{role}:{pid}"
                pw[k] += wt
                pc[k] += 1
    # Kontingente pro Rolle, damit z.B. viele Schauspieler nicht Regie und
    # Studios verdrängen (Spiele-Gewichte sind wegen weniger Einträge kleiner).
    quota = {'d': 7, 'cr': 4, 'co': 4, 'c': 5}
    by_role = defaultdict(list)
    for k, w in pw.items():
        role = k.split(':')[0]
        if (role == 'c' and pc[k] >= 3 and w >= 1.0) or (role != 'c' and pc[k] >= 2 and w >= 0.5):
            by_role[role].append((k, w, pc[k]))
    out = []
    for role, lst in by_role.items():
        out += sorted(lst, key=lambda t: -t[1])[:quota[role]]
    out.sort(key=lambda t: -t[1] * (0.6 if t[0].startswith('c:') else 1.0))
    return out

def _mode_people(ctx: _Ctx) -> list:
    sc = ctx.taste(None)
    people = _people_weights(ctx)
    if not people:
        return []
    feats = {c: set(ctx.vecs.get(c, {})) for c in sc}
    used, out = set(), []
    for k, w, n in people:
        role = k.split(':')[0]
        name = ctx.names.get(k, '?')
        noun = {'d': 'Filme', 'cr': 'Serien', 'co': 'Spiele', 'c': 'Titel'}[role]
        label = {'d': f"Regie: {name}", 'cr': f"Serien von {name}", 'co': f"Studio: {name}",
                 'c': f"Mit {name}"}[role] + f" – {n} {noun} von dir gut bewertet"
        cand = sorted((c for c in sc if c not in used and k in feats[c]), key=lambda c: -sc[c])[:8]
        if not cand:
            continue
        for c in cand:
            used.add(c)
            out.append(_out(ctx.items[c], 0.55 + 0.45 * sc[c], ctx.explain(c, None), group=label))
    return out

def _mode_gems(ctx: _Ctx) -> list:
    sc = ctx.taste(None)
    gems = {}
    for c, s in sc.items():
        it = ctx.items[c]
        r, v = it.get('rating') or 0, it.get('votes') or 0
        ok = ((it['kind'] == 'movie' and r >= 7.2 and 25 <= v <= 1200) or
              (it['kind'] == 'series' and r >= 7.5 and 15 <= v <= 600) or
              (it['kind'] == 'game' and r >= 7.8 and 3 <= v <= 60))
        if ok and s > 0.15:
            gems[c] = 0.65 * s + 0.35 * _quality(it)
    out = []
    for c in _top_per_kind(ctx, gems):
        it = ctx.items[c]
        pre = f"Geheimtipp: ★ {it['rating']:.1f} bei nur {it['votes']} Stimmen"
        out.append(_out(it, gems[c], ctx.explain(c, None, prefix=pre)))
    return out

def _mode_surprise(ctx: _Ctx, lam: float = 0.55, per_kind: int = 40) -> list:
    """Maximal Marginal Relevance: jeder nächste Platz geht an den Titel, der
    relevant ist, sich aber am stärksten von den schon gewählten unterscheidet.
    Leichter Zufall sorgt für eine neue Mischung bei jedem Aufruf."""
    sc = ctx.taste(None)
    rnd = random.Random()
    out = []
    for kind in ('movie', 'series', 'game'):
        pool = sorted((c for c in sc if ctx.items[c]['kind'] == kind and sc[c] > 0.2), key=lambda c: -sc[c])[:400]
        rel = {c: sc[c] * (0.8 + 0.4 * rnd.random()) for c in pool}
        maxsim = {c: 0.0 for c in pool}
        chosen = []
        while pool and len(chosen) < per_kind:
            best = max(pool, key=lambda c: lam * rel[c] - (1 - lam) * maxsim[c])
            pool.remove(best)
            chosen.append(best)
            vb = ctx.vecs.get(best, {})
            for c in pool:
                s = _dot(ctx.vecs.get(c, {}), vb)
                if s > maxsim[c]:
                    maxsim[c] = s
        out += [_out(ctx.items[c], sc[c], ctx.explain(c, None)) for c in chosen]
    return out

def build_recommendations(state: dict, games: dict, diary: list, mode: str = 'recent') -> dict:
    if mode not in MODES:
        mode = 'recent'
    with _lock:
        db = _load()
        ctx = _Ctx(state, games, diary, db)
        if mode == 'recent':
            items = _mode_taste(ctx, RECENT_HALF_LIFE_DAYS)
        elif mode == 'alltime':
            items = _mode_taste(ctx, None)
        elif mode == 'because':
            items = _mode_because(ctx)
        elif mode == 'people':
            items = _mode_people(ctx)
        elif mode == 'gems':
            items = _mode_gems(ctx)
        else:
            items = _mode_surprise(ctx)
        status = _status(ctx)
    return {'mode': mode, 'items': items, 'status': status}


# ── Hintergrund-Backfill ───────────────────────────────────────────────────────
def _search_tmdb(title: str, year: str, media: str) -> int | None:
    field = 'first_air_date_year' if media == 'tv' else 'year'
    res = []
    if year:
        res = _tmdb(f'search/{media}', query=title, **{field: year[:4]}).get('results') or []
    if not res:
        res = _tmdb(f'search/{media}', query=title).get('results') or []
    return _rank_tmdb_results(res, media, query=title)[0]['id'] if res else None

def _task_video_seed(sk: str, s: dict):
    media = 'tv' if s['kind'] == 'series' else 'movie'
    tid = s.get('tmdb_id') or _search_tmdb(s['title'], s['year'], media)
    if not tid:
        return ('seed', sk, None, [])
    d = _tmdb(f'{media}/{tid}', append_to_response='keywords,credits,recommendations,external_ids')
    if not d or not d.get('id'):
        return ('seed', sk, None, [])
    it = _item_from_tmdb_full(d, media)
    recs = []
    for r in (d.get('recommendations') or {}).get('results') or []:
        m = r.get('media_type') or media
        if m in ('movie', 'tv') and r.get('id'):
            recs.append(_item_from_tmdb_list(r, m))
    it['recs'] = [r['id'] for r in recs]
    return ('seed', sk, it, recs)

def _task_game_seeds(batch: list):
    """batch: [(seed_key, seed)] - ein IGDB-Aufruf für alle Seeds mit ID, dann
    ein zweiter für deren "similar games" (gleich mit allen Details)."""
    by_id = {s['igdb_id']: sk for sk, s in batch if s.get('igdb_id')}
    results = []
    found = {}
    if by_id:
        ids = ','.join(str(i) for i in by_id)
        for g in _igdb(f"{IGDB_FIELDS} where id = ({ids}); limit 100;"):
            found[g['id']] = _item_from_igdb(g)
    for sk, s in batch:
        if s.get('igdb_id'):
            continue
        esc = s['title'].replace('"', '\\"')
        res = _igdb(f'search "{esc}"; {IGDB_FIELDS} limit 1;')
        results.append(('seed', sk, _item_from_igdb(res[0]) if res else None, []))
    for gid, sk in by_id.items():
        results.append(('seed', sk, found.get(gid), []))
    sim = sorted({r for _, _, it, _ in results if it for r in it.get('recs') or []})
    sim_ids = [int(x.split(':')[1]) for x in sim]
    extra = []
    for i in range(0, len(sim_ids), 400):
        chunk = ','.join(str(x) for x in sim_ids[i:i + 400])
        extra += [_item_from_igdb(g) for g in _igdb(f"{IGDB_FIELDS} where id = ({chunk}); limit 500;")]
    if results:
        k, sk, it, _ = results[0]
        results[0] = (k, sk, it, extra)
    return ('multi', results)

def _task_candidate(cid: str):
    kind, tid = cid.split(':')
    media = 'tv' if kind == 'series' else 'movie'
    d = _tmdb(f'{media}/{tid}', append_to_response='keywords,credits,external_ids')
    if not d or not d.get('id'):
        return ('gone', cid)
    return ('item', _item_from_tmdb_full(d, media))

def _task_person(pkey: str):
    role, pid = pkey.split(':')
    items = []
    if role in ('d', 'c'):
        d = _tmdb(f'person/{pid}/movie_credits')
        lst = ([x for x in d.get('crew') or [] if x.get('job') == 'Director'] if role == 'd'
               else [x for x in d.get('cast') or [] if x.get('order') is not None and x['order'] <= 3])
        items = [_item_from_tmdb_list(x, 'movie') for x in lst if x.get('id')]
    elif role == 'cr':
        d = _tmdb(f'person/{pid}/tv_credits')
        seen = set()
        for x in d.get('crew') or []:
            if x.get('job') in ('Creator', 'Director') and x.get('id') and x['id'] not in seen:
                seen.add(x['id'])
                items.append(_item_from_tmdb_list(x, 'tv'))
    elif role == 'co':
        res = _igdb(f"{IGDB_FIELDS} where involved_companies.company = {int(pid)} & "
                    f"involved_companies.developer = true & version_parent = null; "
                    f"sort total_rating_count desc; limit 40;")
        items = [_item_from_igdb(g) for g in res]
    return ('person', pkey, items)

def _pending(ctx: _Ctx):
    """Was noch fehlt: Seeds ohne TMDB/IGDB-Daten, Top-Kandidaten ohne
    Keywords/Credits, Lieblings-Macher ohne Werkliste."""
    w_all = ctx.weights(None)
    today = ctx.today

    def prio(sk):
        # Deutlich bewertete und kürzlich gesehene Titel zuerst -> brauchbare
        # Empfehlungen schon nach den ersten Runden.
        s = ctx.seeds[sk]
        sig = abs(sum(x for x, _ in s['entries']) / len(s['entries']))
        ds = [d for _, d in s['entries'] if d]
        age = (today - max(ds)).days if ds else 3650
        return -(0.3 + sig) * (0.3 + 0.5 ** (max(0, age) / 365))

    seeds =[sk for sk, s in ctx.seeds.items() if 'item_id' not in s and sk not in ctx.db['seeds']]
    if not _igdb_ok():
        seeds = [sk for sk in seeds if ctx.seeds[sk]['kind'] != 'game']
    seeds.sort(key=prio)
    video = [sk for sk in seeds if ctx.seeds[sk]['kind'] != 'game']
    game = [sk for sk in seeds if ctx.seeds[sk]['kind'] == 'game']

    corec = defaultdict(float)
    for cid in ctx.cands:
        if ctx.items[cid]['kind'] != 'game':
            corec[cid] = sum(max(0.0, w_all.get(s, 0.0)) * _pf(p) for s, p in ctx.rec_rev.get(cid, ()))
    people_items = {i for p in ctx.db['people'].values() for i in p.get('items') or []}
    ranked = sorted(corec, key=lambda c: -(corec[c] + (0.5 if c in people_items else 0)))[:FULL_CANDIDATE_CAP]
    # Serien ohne IMDb-ID (vor Einführung des IMDb-Links geladen) einmal nachholen
    cands = [c for c in ranked if not ctx.items[c].get('full')
             or (ctx.items[c]['kind'] == 'series' and 'imdb_id' not in ctx.items[c] and not ctx.items[c].get('gone'))]

    people = [k for k, _, _ in _people_weights(ctx) if k not in ctx.db['people']
              and (k.split(':')[0] != 'co' or _igdb_ok())]
    return video, game, cands, people

def _status(ctx: _Ctx) -> dict:
    video, game, cands, people = _pending(ctx)
    total = sum(1 for s in ctx.seeds.values() if 'item_id' not in s)
    pending_seeds = len(video) + len(game)
    return {'seeds_total': total, 'seeds_done': total - pending_seeds,
            'remaining': pending_seeds + len(cands) + len(people),
            'tmdb': bool(TMDB_KEY), 'igdb': _igdb_ok()}

def backfill_recommendations(state: dict, games: dict, diary: list, budget: int = 24) -> dict:
    """Eine Runde Nachladen (parallel, begrenzt). Wird vom Frontend wiederholt
    aufgerufen, bis nichts mehr fehlt. Netzwerkfehler werden nicht gecacht."""
    global _version
    if not _bf_lock.acquire(blocking=False):
        return {'processed': 0, 'remaining': -1, 'busy': True}
    try:
        with _lock:
            ctx = _Ctx(state, games, diary, _load())
            video, game, cands, people = _pending(ctx)
            seeds = ctx.seeds
        tasks = [(_task_video_seed, sk, seeds[sk]) for sk in video[:budget]]
        if game:
            tasks.append((_task_game_seeds, [(sk, seeds[sk]) for sk in game[:40]]))
        left = budget - len(tasks)
        if left > 0:
            tasks += [(_task_candidate, c) for c in cands[:left]]
        if not video or left > 4:
            tasks += [(_task_person, k) for k in people[:3]]
        if not tasks:
            return {'processed': 0, **_status(ctx)}

        results, errors = [], 0
        with ThreadPoolExecutor(max_workers=BACKFILL_WORKERS) as ex:
            futs = [ex.submit(fn, *args) for fn, *args in tasks]
            for f in futs:
                try:
                    results.append(f.result())
                except _Offline:
                    errors += 1
                except Exception as e:
                    errors += 1
                    print(f"  Empfehlungen-Backfill: {type(e).__name__}: {e}")

        flat = []
        for r in results:
            flat += r[1] if r[0] == 'multi' else [r]
        with _lock:
            db = _load()
            for r in flat:
                if r[0] == 'seed':
                    _, sk, it, extra = r
                    for x in extra:
                        _merge_item(x)
                    if it:
                        _merge_item(it)
                    db['seeds'][sk] = it['id'] if it else ''
                elif r[0] == 'item':
                    _merge_item(r[1])
                elif r[0] == 'gone':
                    if r[1] in db['items']:
                        db['items'][r[1]]['gone'] = True
                        db['items'][r[1]]['full'] = True
                elif r[0] == 'person':
                    _, pkey, its = r
                    for x in its:
                        _merge_item(x)
                    db['people'][pkey] = {'ts': time.time(), 'items': [x['id'] for x in its]}
            if flat:
                _version += 1
                _save()
            status = _status(_Ctx(state, games, diary, db))
        offline = errors > 0 and not flat
        return {'processed': len(flat), 'errors': errors, 'offline': offline, **status}
    finally:
        _bf_lock.release()


# ── Kommende Filme passend zum Geschmack (für den Releases-Tab) ───────────────
_upc_lock = threading.Lock()
UPC_VERSION = '|v3'              # erhöhen, wenn sich die Pool-Abfrage ändert -> sofort neu laden
_upc_busy = False

def _refresh_upcoming_pool(today: date):
    """Holt aktuell laufende und kommende Filme (TMDB discover, letzte 90 Tage bis +12 Monate),
    legt sie im Empfehlungs-Pool ab und lädt Details (Keywords/Besetzung) für das Geschmacksprofil nach."""
    global _version, _upc_busy
    from datetime import timedelta
    try:
        start = (today - timedelta(days=90)).isoformat()
        end = (today + timedelta(days=365)).isoformat()
        found = {}
        today_iso = today.isoformat()
        # (Region, Seiten, ab Datum): laufende Filme + gezielt noch nicht erschienene
        for region, pages, gte in (('DE', (1, 2), start), ('US', (1,), start), ('DE', (1, 2, 3, 4), today_iso), ('US', (1, 2), today_iso)):
            for page in pages:
                d = _tmdb('discover/movie', **{
                    'primary_release_date.gte': gte, 'primary_release_date.lte': end,
                    'sort_by': 'popularity.desc', 'include_adult': 'false',
                    'region': region, 'page': page})
                for r in d.get('results') or []:
                    if r.get('id') and r.get('release_date'):
                        found[r['id']] = _item_from_tmdb_list(r, 'movie')
        with _lock:
            db = _load()
            for it in found.values():
                _merge_item(it)
            todo = [it['id'] for it in found.values() if not db['items'][it['id']].get('full')]
        full = []
        with ThreadPoolExecutor(max_workers=BACKFILL_WORKERS) as ex:
            futs = [ex.submit(_task_candidate, cid) for cid in todo[:80]]
            for f in futs:
                try:
                    r = f.result()
                    if r[0] == 'item':
                        full.append(r[1])
                except Exception:
                    pass
        with _lock:
            db = _load()
            for it in full:
                _merge_item(it)
            db['upcoming'] = {'day': today.isoformat() + UPC_VERSION, 'ids': [f"movie:{i}" for i in found]}
            _version += 1
            _save()
    except Exception as e:
        print(f"  Upcoming-Pool: {type(e).__name__}: {e}")
    finally:
        _upc_busy = False

UPC_MODES = ('taste', 'recent', 'people', 'surprise', 'popular')

def upcoming_for_you(state: dict, games: dict, diary: list, limit: int = 12, mode: str = 'taste',
                     when: str = 'all', dismissed=()) -> dict:
    """Kommende/aktuelle Kinofilme, sortiert nach den Empfehlungs-Modi des Empfehlungen-Tabs
    (Allzeit-/Aktueller Geschmack, Regie & Cast, Überrasch mich) oder nach TMDB-Beliebtheit.
    Der Pool wird einmal täglich im Hintergrund aufgefrischt."""
    global _upc_busy
    if mode not in UPC_MODES:
        mode = 'taste'
    today = date.today()
    with _lock:
        up = _load().get('upcoming') or {}
    fresh = up.get('day') == today.isoformat() + UPC_VERSION
    if not fresh:
        with _upc_lock:
            if not _upc_busy:
                _upc_busy = True
                threading.Thread(target=_refresh_upcoming_pool, args=(today,), daemon=True).start()
        if not up.get('ids'):
            return {'status': 'loading', 'items': []}
    status = 'ready' if fresh else 'loading'
    hidden = {int(x) for x in dismissed if str(x).isdigit()}
    with _lock:
        db = _load()
        ctx = _Ctx(state, games, diary, db)
        today_iso = today.isoformat()
        ids = [i for i in (db.get('upcoming') or {}).get('ids', [])
               if i in ctx.items and not ctx.items[i].get('gone')]
        cands = []
        for i in ids:
            it = ctx.items[i]
            d = it.get('date') or ''
            if (not d or d[:4] < MIN_REC_YEAR or not it.get('title') or i in ctx.have_ids
                    or it.get('tmdb_id') in hidden or _title_known(it, ctx.titles)):
                continue
            if when == 'cinema' and d > today_iso:
                continue
            if when == 'coming' and d <= today_iso:
                continue
            if set(it.get('raw_genres') or []) & {'Documentary'} and (it.get('votes') or 0) < 5:
                continue
            cands.append(i)
        ctx.cands = cands
        if not cands:
            return {'status': status, 'items': []}

        if mode == 'popular':
            sc = ctx.taste(None)
            order = sorted(cands, key=lambda c: -(ctx.items[c].get('popularity') or 0))[:limit]
            out = [_out(ctx.items[c], sc.get(c, 0), 'Beliebt bei allen TMDB-Nutzern') for c in order]
        elif mode == 'people':
            out = _mode_people(ctx)[:limit]
        elif mode == 'surprise':
            out = _mode_surprise(ctx, per_kind=limit)[:limit]
        else:
            sc = ctx.taste(RECENT_HALF_LIFE_DAYS if mode == 'recent' else None)
            by_score = sorted(cands, key=lambda c: -sc.get(c, 0))
            if when == 'all':
                # Mischung: bestpassende kommende Filme + bestpassende, die gerade im Kino laufen
                coming = [c for c in by_score if (ctx.items[c].get('date') or '') > today_iso][:limit // 2]
                running = [c for c in by_score if c not in coming][:limit - len(coming)]
                order = sorted(coming + running, key=lambda c: -sc.get(c, 0))
            else:
                order = by_score[:limit]
            out = [_out(ctx.items[c], sc.get(c, 0), ctx.explain(c, RECENT_HALF_LIFE_DAYS if mode == 'recent' else None))
                   for c in order]
        for o in out:
            it = ctx.items[o['key']]
            o['tmdb_id'] = it.get('tmdb_id')
            o['date'] = it.get('date') or ''
            o['in_cinemas'] = bool(o['date']) and o['date'] <= today_iso
    return {'status': status, 'items': out}
