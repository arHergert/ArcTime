"""ArcTime - Release-Erinnerungen: Filme (Blu-ray/VoD, USA oder Deutschland, frühester Termin zählt) und
Serienstaffeln (Start oder Finale) vormerken, TMDB täglich abfragen, Meldungen + Browser-Push auslösen."""

import base64, hashlib, json, threading, time, warnings
from datetime import date, datetime

# 'requests' (kommt mit pywebpush) meldet harmlos eine zu neue chardet-Version - nicht in der Konsole zeigen
warnings.filterwarnings('ignore', message='.*doesn.t match a supported version.*')

from at_config import CACHE
from at_metadata import _dl_poster_force
from at_recommendations import _Offline, _tmdb

RELFILE = CACHE / "releases.json"
PUSHFILE = CACHE / "push_subs.json"
VAPID_KEY = CACHE / "vapid_private.pem"
_lock = threading.RLock()

# TMDB-Release-Typen: 3 Kino, 4 Digital (VoD), 5 Physisch (Blu-ray/DVD)
HOME_TYPES = {4: 'VoD', 5: 'Blu-ray'}
REGIONS = {'US': 'USA', 'DE': 'Deutschland'}


# ── Speicher ──────────────────────────────────────────────────────────────────
def _load() -> dict:
    if RELFILE.exists():
        try:
            d = json.loads(RELFILE.read_text('utf-8'))
            if isinstance(d, dict):
                d.setdefault('alerts', []); d.setdefault('notifications', []); d.setdefault('last_check', None); d.setdefault('dismissed', [])
                return d
        except Exception:
            pass
    return {'alerts': [], 'notifications': [], 'last_check': None, 'dismissed': []}

def _save(d: dict):
    RELFILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), 'utf-8')

def _load_subs() -> list:
    if PUSHFILE.exists():
        try:
            d = json.loads(PUSHFILE.read_text('utf-8'))
            return d if isinstance(d, list) else []
        except Exception:
            pass
    return []

def _save_subs(s: list):
    PUSHFILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), 'utf-8')


# ── TMDB: Termine ermitteln ───────────────────────────────────────────────────
def fetch_release_info(tmdb_id: int) -> dict:
    """Früheste Heimkino-Veröffentlichung (Blu-ray oder VoD) in USA/Deutschland + Kinostart."""
    data = _tmdb(f'movie/{int(tmdb_id)}/release_dates')
    best = None          # (datum, typ, land)
    theatrical = None
    for res in data.get('results') or []:
        cc = res.get('iso_3166_1')
        if cc not in REGIONS:
            continue
        for rd in res.get('release_dates') or []:
            d = (rd.get('release_date') or '')[:10]
            t = rd.get('type')
            if not d:
                continue
            if t in HOME_TYPES and (best is None or d < best[0]):
                best = (d, t, cc)
            if t in (2, 3) and (theatrical is None or d < theatrical):
                theatrical = d
    return {
        'date': best[0] if best else None,
        'type': HOME_TYPES[best[1]] if best else None,
        'country': best[2] if best else None,
        'theatrical': theatrical,
    }


# ── TMDB: Serien (Staffelstart / Staffelfinale) ───────────────────────────────
TV_EVENTS = {'start': 'Staffelstart', 'finale': 'Staffelfinale'}

def tv_season_info(tmdb_id: int, season: int, listed: dict | None = None) -> dict:
    """Start- und Finaltermin einer Staffel aus den Sendedaten ihrer Folgen.
    Das Finale ist nur 'bekannt', wenn alle Folgen der Staffel ein Datum haben."""
    today = date.today().isoformat()
    sd = _tmdb(f'tv/{int(tmdb_id)}/season/{int(season)}') or {}
    eps = sd.get('episodes') or []
    dates = sorted(e['air_date'] for e in eps if e.get('air_date'))
    total = (listed or {}).get('episode_count') or len(eps)
    start = dates[0] if dates else ((listed or {}).get('air_date') or sd.get('air_date') or None)
    finale = dates[-1] if dates and total and len(dates) >= total else None
    aired = sum(1 for d_ in dates if d_ <= today)
    if not start:
        state = 'unannounced'
    elif start > today:
        state = 'upcoming'
    elif finale and finale <= today:
        state = 'finished'
    else:
        state = 'running'
    return {'season': int(season), 'start': start, 'finale': finale, 'last_known': dates[-1] if dates else None,
            'episodes': total, 'dated': len(dates), 'aired': aired, 'state': state}

def tv_overview(tmdb_id: int, season: int | None = None) -> dict:
    """Für die Auswahl im Releases-Tab: Serie, Staffelliste und der Vorschlag, welche Staffel
    als nächstes (oder gerade) läuft - samt Start-/Finaltermin der gewählten Staffel."""
    d = _tmdb(f'tv/{int(tmdb_id)}') or {}
    if not d.get('id'):
        return {'ok': False, 'error': 'Serie nicht gefunden'}
    today = date.today().isoformat()
    seasons = [x for x in d.get('seasons') or [] if x.get('season_number', 0) > 0]
    by_num = {x['season_number']: x for x in seasons}
    nxt = d.get('next_episode_to_air') or {}
    last = d.get('last_episode_to_air') or {}
    last_num = max(by_num) if by_num else 0
    returning = d.get('status') in ('Returning Series', 'In Production', 'Planned', 'Pilot')
    suggested, reason = None, ''
    if nxt.get('season_number'):
        suggested = nxt['season_number']
        reason = 'läuft gerade' if (last.get('season_number') == suggested) else 'kommt als Nächstes'
    else:
        future = sorted(x['season_number'] for x in seasons if (x.get('air_date') or '') > today)
        if future:
            suggested, reason = future[0], 'kommt als Nächstes'
        elif returning:
            suggested, reason = last_num + 1, 'noch nicht angekündigt'
        elif last_num:
            suggested, reason = last_num, 'zuletzt erschienen'
    chosen = season or suggested
    detail = None
    if chosen:
        detail = tv_season_info(tmdb_id, chosen, by_num.get(chosen))
    return {
        'ok': True, 'tmdb_id': d['id'], 'title': d.get('name') or '', 'year': (d.get('first_air_date') or '')[:4],
        'poster': f"https://image.tmdb.org/t/p/w185{d['poster_path']}" if d.get('poster_path') else None,
        'status': d.get('status') or '', 'suggested': suggested, 'reason': reason,
        'seasons': [{'number': x['season_number'], 'name': f"Staffel {x['season_number']}",
                     'air_date': x.get('air_date'), 'episodes': x.get('episode_count')} for x in seasons]
                   + ([{'number': last_num + 1, 'name': f'Staffel {last_num + 1}', 'air_date': None, 'episodes': None}]
                      if returning and suggested == last_num + 1 else []),
        'detail': detail,
    }

def fetch_tv_info(a: dict) -> dict:
    """Wie fetch_release_info, aber für eine Serien-Erinnerung (Staffel + Start/Finale)."""
    sn = int(a['season'])
    d = _tmdb(f"tv/{int(a['tmdb_id'])}") or {}
    listed = next((x for x in d.get('seasons') or [] if x.get('season_number') == sn), None)
    det = tv_season_info(a['tmdb_id'], sn, listed) if listed or d.get('id') else {'start': None, 'finale': None}
    day = det.get('start') if a.get('event') == 'start' else det.get('finale')
    return {'date': day, 'type': TV_EVENTS.get(a.get('event'), 'Staffelstart'), 'country': None,
            'theatrical': None, 'series': det}

def fetch_info(a: dict) -> dict:
    return fetch_tv_info(a) if a.get('media') == 'tv' else fetch_release_info(a['tmdb_id'])


# ── Push ──────────────────────────────────────────────────────────────────────
def push_available() -> bool:
    try:
        import pywebpush  # noqa: F401
        import py_vapid  # noqa: F401
        return True
    except Exception:
        return False

def _ensure_vapid() -> str | None:
    """Erzeugt bei Bedarf das VAPID-Schlüsselpaar; gibt den öffentlichen Schlüssel (base64url) zurück."""
    if not push_available():
        return None
    from py_vapid import Vapid
    from cryptography.hazmat.primitives import serialization
    v = Vapid()
    if VAPID_KEY.exists():
        v = Vapid.from_file(str(VAPID_KEY))
    else:
        v.generate_keys()
        v.save_key(str(VAPID_KEY))
    raw = v.public_key.public_bytes(serialization.Encoding.X962,
                                    serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')

def subscribe(sub: dict) -> bool:
    if not isinstance(sub, dict) or not sub.get('endpoint'):
        return False
    with _lock:
        subs = [s for s in _load_subs() if s.get('endpoint') != sub['endpoint']]
        subs.append(sub)
        _save_subs(subs)
    return True

def unsubscribe(endpoint: str):
    with _lock:
        _save_subs([s for s in _load_subs() if s.get('endpoint') != endpoint])

def _send_push(title: str, body: str, icon: str | None):
    if not push_available():
        return
    from pywebpush import WebPushException, webpush
    _ensure_vapid()
    payload = json.dumps({'title': title, 'body': body, 'icon': icon or '/icon.png', 'url': '/#releases'})
    dead = []
    for s in _load_subs():
        try:
            webpush(subscription_info=s, data=payload, vapid_private_key=str(VAPID_KEY),
                    vapid_claims={'sub': 'mailto:arctime@example.com'}, ttl=86400)
        except WebPushException as e:
            code = getattr(getattr(e, 'response', None), 'status_code', None)
            if code in (404, 410):
                dead.append(s.get('endpoint'))
        except Exception:
            pass
    for ep in dead:
        unsubscribe(ep)


# ── Erinnerungen ──────────────────────────────────────────────────────────────
def _apply_info(a: dict, info: dict):
    a['release'] = {'date': info['date'], 'type': info['type'], 'country': info['country']} if info['date'] else None
    # Selbst eingetragener Termin gilt, solange TMDB keinen früheren kennt
    m = a.get('manual')
    if m and (a['release'] is None or m < a['release']['date']):
        a['release'] = {'date': m, 'type': 'Eigener Termin', 'country': None}
    a['theatrical'] = info['theatrical']
    if info.get('series') is not None:
        a['series'] = info['series']
    a['checked'] = datetime.now().isoformat(timespec='seconds')

def public_state() -> dict:
    with _lock:
        d = _load()
    return {
        'alerts': d['alerts'], 'notifications': d['notifications'][:60], 'last_check': d['last_check'],
        'push': {'available': push_available(), 'subscriptions': len(_load_subs()),
                 'key': _ensure_vapid() if push_available() else None},
    }

def add_alert(tmdb_id: int, title: str, year: str, poster_url: str | None,
              media: str = 'movie', season: int | None = None, event: str | None = None) -> dict:
    tmdb_id = int(tmdb_id)
    media = 'tv' if media == 'tv' else 'movie'
    if media == 'tv':
        season = int(season or 1)
        event = event if event in TV_EVENTS else 'start'
    with _lock:
        d = _load()
        for a in d['alerts']:
            if (a['tmdb_id'] == tmdb_id and a.get('media', 'movie') == media
                    and (media == 'movie' or (a.get('season') == season and a.get('event') == event))):
                return {'ok': True, 'duplicate': True, 'alert': a}
    poster = None
    if poster_url and str(poster_url).startswith('http'):
        fn = _dl_poster_force(poster_url, f'rel|{media}|{tmdb_id}' if media == 'tv' else f'rel|{tmdb_id}')
        poster = f'/poster/{fn}' if fn else None
    elif poster_url:
        poster = poster_url
    a = {'id': hashlib.md5(f'{time.time()}{tmdb_id}'.encode()).hexdigest()[:12], 'tmdb_id': tmdb_id,
         'title': title, 'year': str(year or '')[:4], 'poster': poster,
         'created': datetime.now().isoformat(timespec='seconds'),
         'released': False, 'notified': False, 'release': None, 'theatrical': None, 'checked': None,
         'media': media}
    if media == 'tv':
        a.update(season=season, event=event)
    try:
        _apply_info(a, fetch_info(a))
    except _Offline:
        pass
    # Ist der Film beim Vormerken schon erhältlich, ist das keine "neue" Meldung
    if a['release'] and a['release']['date'] <= date.today().isoformat():
        a['released'] = True; a['notified'] = True
    with _lock:
        d = _load()
        d['alerts'].append(a)
        _save(d)
    return {'ok': True, 'alert': a}

def remove_alert(alert_id: str) -> bool:
    with _lock:
        d = _load()
        n = len(d['alerts'])
        d['alerts'] = [a for a in d['alerts'] if a['id'] != alert_id]
        d['notifications'] = [x for x in d['notifications'] if x.get('alert_id') != alert_id]
        _save(d)
        return len(d['alerts']) < n

def set_manual(alert_id: str, day: str | None) -> bool:
    """Eigenen Termin setzen (YYYY-MM-DD) oder entfernen (None/leer); wertet sofort neu aus."""
    if day:
        try:
            date.fromisoformat(day)
        except ValueError:
            return False
    with _lock:
        d = _load()
        for a in d['alerts']:
            if a['id'] == alert_id:
                a['manual'] = day or None
                try:
                    _apply_info(a, fetch_info(a))
                except _Offline:
                    a['release'] = ({'date': day, 'type': 'Eigener Termin', 'country': None} if day else None)
                if a['release'] and a['release']['date'] <= date.today().isoformat():
                    a['released'] = True; a['notified'] = True      # schon erschienen: keine Push-Meldung
                else:
                    a['released'] = False
                _save(d)
                return True
    return False

def dismissed_ids() -> list:
    with _lock:
        return list(_load().get('dismissed', []))

def set_dismissed(tmdb_id: int, on: bool = True):
    """Vorschlag dauerhaft ausblenden (on=True) bzw. wieder einblenden (on=False)."""
    tmdb_id = int(tmdb_id)
    with _lock:
        d = _load()
        cur = [x for x in d.get('dismissed', []) if x != tmdb_id]
        d['dismissed'] = cur + [tmdb_id] if on else cur
        _save(d)

def mark_read(nid: str | None):
    with _lock:
        d = _load()
        for x in d['notifications']:
            if nid in (None, 'all') or x['id'] == nid:
                x['read'] = True
        _save(d)

def _notif_text(a: dict, r: dict) -> tuple:
    """(Satzteil nach dem Titel, Zusatzzeile) für Glocke und Push."""
    if a.get('media') == 'tv':
        n = a.get('season')
        if a.get('event') == 'finale':
            return f' – das Finale von Staffel {n} ist erschienen', 'Staffelfinale'
        return f' – Staffel {n} ist gestartet', 'Staffelstart'
    label = f"{r['type']} · {REGIONS.get(r['country'], r['country'])}" if r['country'] else r['type']
    return ' ist jetzt erhältlich', label

def check_all() -> dict:
    """Fragt TMDB für alle offenen Erinnerungen ab und löst Meldungen/Push aus."""
    today = date.today().isoformat()
    new = []
    with _lock:
        d = _load()
    errors = 0
    for a in d['alerts']:
        if a.get('released'):
            continue
        try:
            _apply_info(a, fetch_info(a))
        except _Offline:
            errors += 1
            continue
        r = a.get('release')
        if r and r['date'] <= today:
            a['released'] = True
            if not a.get('notified'):
                a['notified'] = True
                n = {'id': hashlib.md5(f"{time.time()}{a['id']}".encode()).hexdigest()[:12], 'alert_id': a['id'],
                     'title': a['title'], 'year': a['year'], 'poster': a['poster'], 'date': r['date'],
                     'type': r['type'], 'country': r['country'], 'read': False,
                     'created': datetime.now().isoformat(timespec='seconds')}
                n['rest'], n['sub'] = _notif_text(a, r)
                d['notifications'].insert(0, n)
                new.append(n)
    d['notifications'] = d['notifications'][:100]
    if not errors:
        d['last_check'] = datetime.now().isoformat(timespec='seconds')
    with _lock:
        # Zwischenzeitlich gelöschte/hinzugefügte Erinnerungen nicht überschreiben
        cur = _load()
        by_id = {a['id']: a for a in d['alerts']}
        cur['alerts'] = [by_id.get(a['id'], a) for a in cur['alerts']]
        known = {a['id'] for a in cur['alerts']}
        cur['notifications'] = [n for n in d['notifications'] if n.get('alert_id') in known]
        cur['last_check'] = d['last_check'] or cur['last_check']
        _save(cur)
    for n in new:
        _send_push(f"{n['title']}{n['rest']}", f"{n['sub']} · seit {n['date']}", n['poster'])
    return {'checked': len(d['alerts']), 'new': len(new), 'errors': errors}


# ── Täglicher Hintergrund-Check ───────────────────────────────────────────────
def _scheduler():
    while True:
        try:
            d = _load()
            last = (d.get('last_check') or '')[:10]
            if d['alerts'] and last != date.today().isoformat():
                check_all()
        except Exception:
            pass
        time.sleep(20 * 60)

def start_scheduler():
    threading.Thread(target=_scheduler, daemon=True, name='release-check').start()
