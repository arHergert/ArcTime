"""ArcTime - HTTP-Server: Request-Routing (Handler) und HTML-Seiten-Zusammenbau."""

import hashlib, html, json, os, re, shutil, socket, subprocess, sys, time, threading, urllib.parse, urllib.request, webbrowser
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from at_config import BASE, FILME, GCOVERS, PEOPLE, POSTERS, SERIEN, STATIC, scan_episodes
from at_store import (_diary_lock, add_title_override, diary_csv, load_awards, load_config,
                       load_diary, load_last_played, load_removed, load_top, save_awards, save_cache, save_top,
                       save_config, save_diary, save_last_played, save_removed)
from at_scan import _key, scan_filme, scan_serien
from at_metadata import (_dcov_key, _dl_poster_force, _games_lock, _tmdb_fetch_full,
                          backfill_diary_posters, get_cast, get_or_create_game,
                          get_tmdb_season_count, igdb_search_multi, load_diary_covers,
                          load_games, save_diary_covers, save_games, title_search_candidates,
                          tmdb_poster_candidates)
from at_recommendations import backfill_recommendations, build_recommendations, set_feedback, upcoming_for_you
from at_import import _looks_like_serializd_html, import_backloggd_journal, import_csv, import_serializd_html, sync_letterboxd
import at_refresh
import at_releases
from at_refresh import _poster_url, _reload_lock, build_api, refresh
from at_playback import _MIME_BY_EXT, cast_to_lg, find_lg_renderer, play_file

# ── HTTP-Handler ───────────────────────────────────────────────────────────────
# Der Server läuft dauerhaft, solange der Prozess lebt - kein Watchdog/Heartbeat
# mehr, der ihn bei geschlossenem Browser-Tab automatisch beendet. Beenden nur
# noch explizit (Strg+C, Prozess killen, o.ä.).
_state: dict = {}
def _qs(path: str) -> dict:
    return urllib.parse.parse_qs(urllib.parse.urlparse(path).query)


class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        p = self.path.split('?')[0].rstrip('/')
        if   p in ('','/'):          self._html()
        elif p == '/api/data':       self._data()
        elif p == '/api/reload':     self._reload_sse()
        elif p == '/api/play':       self._play()
        elif p == '/api/episodes':   self._episodes()
        elif p == '/api/diary':      self._diary_get()
        elif p == '/api/diary/csv':  self._diary_csv()
        elif p == '/api/awards':     self._json({'awards': load_awards()})
        elif p == '/api/top':        self._json({'top': load_top()})
        elif p == '/api/releases':   self._json(at_releases.public_state())
        elif p == '/sw.js':          self._service_worker()
        elif p == '/api/releases/tv': self._releases_tv()
        elif p == '/api/releases/suggestions': self._releases_suggestions()
        elif p == '/api/cast':       self._cast_get()
        elif p == '/api/letterboxd/config': self._lb_config_get()
        elif p == '/api/letterboxd/sync':   self._lb_sync_get()
        elif p.startswith('/static/'): self._static(p[8:])
        elif p.startswith('/poster/'): self._poster(p[8:])
        elif p.startswith('/people/'): self._person_photo(p[8:])
        elif p.startswith('/gcover/'): self._gcover(p[8:])
        elif p == '/api/games/search': self._games_search()
        elif p == '/api/poster/candidates': self._poster_candidates()
        elif p == '/api/tmdb/seasons': self._tmdb_seasons_get()
        elif p == '/api/recommendations': self._recommendations_get()
        elif p == '/api/title-search': self._title_search_get()
        elif p == '/favicon.ico':    self._favicon()
        elif p == '/icon.png':       self._icon_png()
        elif p == '/fonts/inter-variable.ttf': self._inter_font()
        elif p == '/stream':         self._stream()
        else:                        self.send_error(404)

    def do_HEAD(self):
        # Wird von DLNA-/UPnP-Medienrenderern (z.B. dem LG-Fernseher beim
        # "Auf Gerät wiedergeben") vor dem eigentlichen GET geschickt, um
        # Content-Length/Content-Type/Range-Unterstützung zu prüfen.
        p = self.path.split('?')[0].rstrip('/')
        if p == '/stream':
            self._stream(head_only=True)
        else:
            self.send_error(404)

    def do_POST(self):
        p = self.path.split('?')[0].rstrip('/')
        if   p == '/api/diary':   self._diary_post()
        elif p == '/api/import':  self._import_post()
        elif p == '/api/awards':  self._awards_post()
        elif p == '/api/top':     self._top_post()
        elif p == '/api/releases/add':   self._releases_add()
        elif p == '/api/releases/check': self._json({'ok': True, **at_releases.check_all(), **at_releases.public_state()})
        elif p == '/api/releases/read':  self._releases_read()
        elif p == '/api/releases/manual': self._releases_manual()
        elif p == '/api/releases/dismiss': self._releases_dismiss()
        elif p == '/api/push/subscribe':   self._push_sub(True)
        elif p == '/api/push/unsubscribe': self._push_sub(False)
        elif p == '/api/letterboxd/config': self._lb_config_post()
        elif p == '/api/games/add':    self._games_add()
        elif p == '/api/games/import': self._games_import()
        elif p == '/api/diary/backfill-covers': self._diary_backfill_covers()
        elif p == '/api/poster/choose': self._poster_choose()
        elif p == '/api/recommendations/backfill': self._recommendations_backfill()
        elif p == '/api/recommendations/feedback': self._recommendations_feedback()
        elif p == '/api/title-override': self._title_override_post()
        elif p == '/api/tv-cast':        self._tv_cast_post()
        else:                     self.send_error(404)

    def do_DELETE(self):
        p = self.path.split('?')[0].rstrip('/')
        if   p == '/api/file':    self._delete_file()
        elif p == '/api/diary':   self._diary_delete()
        elif p == '/api/games':   self._games_delete()
        elif p == '/api/library': self._library_remove()
        elif p == '/api/releases': self._releases_delete()
        else:                    self.send_error(404)

    def _html(self):
        body = HTML.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.end_headers(); self.wfile.write(body)

    def _data(self):
        data = build_api(_state)
        data['omdb_warning'] = at_refresh._omdb_warning
        self._json(data)

    def _reload_sse(self):
        if not _reload_lock.acquire(blocking=False):
            self.send_error(429,'Reload läuft bereits'); return
        try:
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream')
            self.send_header('Cache-Control','no-cache')
            self.end_headers()
            def prog(done, total, msg):
                data = json.dumps({'done':False,'n':done,'total':total,'msg':msg})
                try:
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                except Exception: pass
            _state.update(refresh(_state, on_progress=prog))
            self.wfile.write(
                f"data: {json.dumps({'done':True,'data':build_api(_state),'warning':at_refresh._omdb_warning})}\n\n".encode())
            self.wfile.flush()
        finally:
            _reload_lock.release()

    def _play(self):
        path_str = _qs(self.path).get('path',[''])[0]
        key = _qs(self.path).get('key',[''])[0]
        result = play_file(path_str)
        if result.get('ok') and key:
            lp = load_last_played()
            lp[key] = time.strftime('%Y-%m-%d')
            save_last_played(lp)
        self._json(result)

    def _resolve_streamable_path(self):
        """Löst den 'path'-Query-Parameter auf und stellt sicher, dass er
        wirklich innerhalb der Filme-/Serien-Ordner liegt (wie bei
        _delete_file) - diese Route ist ohne Login erreichbar, sobald der
        Fernseher sie per Cast anfragt, daher dieselbe Pfad-Absicherung."""
        path_str = _qs(self.path).get('path', [''])[0]
        if not path_str:
            return None
        try:
            p = Path(path_str).resolve()
        except Exception:
            return None
        try:
            if not (p.is_relative_to(FILME.resolve()) or p.is_relative_to(SERIEN.resolve())):
                return None
        except Exception:
            return None
        return p if p.exists() else None

    def _stream(self, head_only=False):
        """Liefert eine Video-Datei mit Range-Unterstützung (HTTP 206) aus -
        das brauchen DLNA-/UPnP-Medienrenderer (z.B. beim 'Auf LG TV
        abspielen') praktisch immer, sowohl fürs Vorspulen als auch oft schon
        für den allerersten Zugriff. Dient NUR dem Cast-Feature - die normale
        Wiedergabe im Player läuft weiterhin lokal über VLC/play_file."""
        p = self._resolve_streamable_path()
        if not p:
            self.send_error(404); return

        try:
            file_size = p.stat().st_size
        except OSError:
            self.send_error(404); return
        mime = _MIME_BY_EXT.get(p.suffix.lower(), 'application/octet-stream')
        range_header = self.headers.get('Range')

        try:
            if range_header:
                m = re.match(r'bytes=(\d*)-(\d*)', range_header)
                if not m or (not m.group(1) and not m.group(2)):
                    self.send_error(416); return
                start = int(m.group(1)) if m.group(1) else 0
                end = int(m.group(2)) if m.group(2) else file_size - 1
                end = min(end, file_size - 1)
                if start > end or start >= file_size:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{file_size}')
                    self.end_headers()
                    return
                length = end - start + 1
                self.send_response(206)
                self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Length', str(length))
                self.send_header('Content-Type', mime)
                self.end_headers()
                if head_only:
                    return
                with open(p, 'rb') as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(262144, remaining))
                        if not chunk:
                            break
                        try:
                            self.wfile.write(chunk)
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                            return
                        remaining -= len(chunk)
            else:
                self.send_response(200)
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Length', str(file_size))
                self.send_header('Content-Type', mime)
                self.end_headers()
                if head_only:
                    return
                with open(p, 'rb') as f:
                    while True:
                        chunk = f.read(262144)
                        if not chunk:
                            break
                        try:
                            self.wfile.write(chunk)
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                            return
        except Exception:
            try:
                self.send_error(500)
            except Exception:
                pass

    def _tv_cast_post(self):
        """Startet die Wiedergabe eines Bibliotheks-Films auf dem LG-Fernseher
        (siehe cast_to_lg/find_lg_renderer)."""
        try:
            n = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(n)) if n else {}
        except Exception:
            self._json({'ok': False, 'error': 'Ungültige Daten'}, 400); return
        key = (body.get('key') or '').strip()
        e = _state.get(key)
        if not e or not e.get('file'):
            self._json({'ok': False, 'error': 'Film nicht gefunden'}, 404); return
        title = e.get('title_actual') or e.get('t') or ''
        result = cast_to_lg(e['file'], title)
        self._json(result, 200 if result.get('ok') else 502)

    def _episodes(self):
        key    = _qs(self.path).get('key',[''])[0]
        folder = _state.get(key,{}).get('folder','')
        self._json({'episodes': scan_episodes(folder) if folder else []})

    def _delete_file(self):
        path_str = _qs(self.path).get('path',[''])[0]
        if not path_str:
            self._json({'ok':False,'error':'Kein Pfad'},400); return
        try:
            p = Path(path_str).resolve()
            in_filme  = p.is_relative_to(FILME.resolve())
            in_serien = p.is_relative_to(SERIEN.resolve())
            if not (in_filme or in_serien):
                self._json({'ok':False,'error':'Pfad außerhalb der erlaubten Ordner'},403); return
        except Exception:
            self._json({'ok':False,'error':'Ungültiger Pfad'},400); return
        if not p.exists():
            self._json({'ok':False,'error':'Datei nicht gefunden'},404); return
        try:
            p.unlink()
            # Cache-Eintrag sofort entfernen, sonst taucht der Film nach einem
            # Reload/Neustart wieder auf, weil refresh() nur ergänzt, nie entfernt.
            key = _qs(self.path).get('key', [''])[0]
            if not key:
                key = next((k for k, e in _state.items() if e.get('file') == str(p)), None)
            if key and key in _state:
                del _state[key]
                save_cache(_state)
            self._json({'ok':True})
        except Exception as e:
            self._json({'ok':False,'error':str(e)},500)

    def _library_remove(self):
        """Entfernt einen Bibliotheks-Eintrag (typischerweise eine Serie ohne
        verbleibende Folgen) aus der Anzeige, OHNE Dateien auf der Platte
        anzufassen. Der zugehörige Datei-/Ordnerpfad landet auf einer
        Sperrliste (removed_entries.json), damit scan_serien()/scan_filme()
        ihn nicht beim nächsten Refresh einfach wieder als "neu" aufgreifen -
        der Ordner kann ja durchaus noch existieren (nur eben leer). Ein
        vorhandener Tagebuch-Eintrag/eine Bewertung bleibt unangetastet."""
        key = _qs(self.path).get('key', [''])[0]
        if not key:
            self._json({'ok': False, 'error': 'Key fehlt'}, 400); return
        e = _state.get(key)
        if not e:
            self._json({'ok': False, 'error': 'Eintrag nicht gefunden'}, 404); return
        path = e.get('file') or e.get('folder')
        del _state[key]
        save_cache(_state)
        if path:
            removed = load_removed()
            removed[path] = {'kind': e.get('kind', ''), 'removed_at': time.time()}
            save_removed(removed)
        self._json({'ok': True, 'data': build_api(_state)})

    # ── Tagebuch ──────────────────────────────────────────────────────────────
    def _diary_get(self):
        self._json({'entries': load_diary()})

    def _diary_backfill_covers(self):
        """Lädt in kleinen, begrenzten Schritten fehlende Cover nach (siehe
        backfill_diary_posters). Wird vom Frontend im Hintergrund wiederholt
        aufgerufen, blockiert also nie den normalen Seitenaufbau."""
        try:
            n = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(n)) if n else {}
        except Exception:
            body = {}
        limit = body.get('limit') or 15
        result = backfill_diary_posters(load_diary(), limit=limit)
        self._json({
            'ok': True, 'entries': result['entries'],
            'processed': result['processed'], 'updated': result['updated'],
            'remaining': result['remaining'],
        })

    def _diary_csv(self):
        body = diary_csv(load_diary()).encode('utf-8-sig')
        self.send_response(200)
        self.send_header('Content-Type', 'text/csv; charset=utf-8')
        self.send_header('Content-Disposition', 'attachment; filename="tagebuch.csv"')
        self.send_header('Content-Length', len(body))
        self.end_headers(); self.wfile.write(body)

    def _diary_post(self):
        try:
            n = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(n)) if n else {}
        except Exception:
            self._json({'ok': False, 'error': 'Ungültige Daten'}, 400); return

        eid = data.get('id')
        if not eid and not data.get('title'):
            self._json({'ok': False, 'error': 'Titel fehlt'}, 400); return

        with _diary_lock:
            entries = load_diary()
            if eid:  # Bearbeiten
                found = False
                for e in entries:
                    if e.get('id') == eid:
                        for f in ('rating', 'date', 'comment', 'rewatch',
                                  'noms', 'nomPeople', 'mini', 'statusOverride', 'season',
                                  'startDate', 'finishDate', 'platform'):
                            if f in data:
                                e[f] = data[f]
                        found = True
                        break
                if not found:
                    self._json({'ok': False, 'error': 'Eintrag nicht gefunden'}, 404); return
            else:    # Neu
                entries.append({
                    'id':      hashlib.md5(f"{time.time()}{data.get('title')}".encode()).hexdigest()[:12],
                    'key':     data.get('key', ''),
                    'kind':    data.get('kind', 'movie'),   # 'movie' | 'season' | 'game'
                    'title':   data.get('title', ''),
                    'year':    data.get('year', ''),
                    'season':  data.get('season'),
                    'poster':  data.get('poster'),
                    'genre':   data.get('genre', ''),
                    'rating':  data.get('rating'),
                    'date':    data.get('date') or time.strftime('%Y-%m-%d'),
                    'startDate': data.get('startDate'),
                    'finishDate': data.get('finishDate'),
                    'platform': data.get('platform', ''),
                    'comment': data.get('comment', ''),
                    'rewatch': bool(data.get('rewatch')),
                    'noms':    data.get('noms', []),
                    'nomPeople': data.get('nomPeople', {}),
                    'mini':    bool(data.get('mini')),
                    'statusOverride': data.get('statusOverride'),
                    'created': time.time(),
                })
            save_diary(entries)
        self._json({'ok': True, 'entries': load_diary()})

    def _import_post(self):
        try:
            n = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(n)) if n else {}
        except Exception:
            self._json({'ok': False, 'error': 'Ungültige Daten'}, 400); return
        txt = data.get('csv', '')
        if not txt.strip():
            self._json({'ok': False, 'error': 'Datei ist leer'}, 400); return
        try:
            if _looks_like_serializd_html(txt):
                self._json(import_serializd_html(txt, _state))
            else:
                self._json(import_csv(txt, _state))
        except Exception as e:
            self._json({'ok': False, 'error': f'Import fehlgeschlagen: {e}'}, 500)

    # ── Releases (Erinnerungen) & Push ────────────────────────────────────────
    def _body_json(self) -> dict:
        try:
            n = int(self.headers.get('Content-Length', 0))
            d = json.loads(self.rfile.read(n)) if n else {}
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _releases_add(self):
        b = self._body_json()
        try:
            r = at_releases.add_alert(b.get('tmdb_id'), (b.get('title') or '').strip(), b.get('year'), b.get('poster'),
                                      media=b.get('media') or 'movie', season=b.get('season'), event=b.get('event'))
        except Exception as e:
            self._json({'ok': False, 'error': str(e)}, 400); return
        self._json({**r, **at_releases.public_state()})

    def _releases_delete(self):
        aid = _qs(self.path).get('id', [''])[0]
        ok = at_releases.remove_alert(aid)
        self._json({'ok': ok, **at_releases.public_state()})

    def _releases_tv(self):
        q = _qs(self.path)
        try:
            tid = int(q.get('id', [''])[0])
            sn = q.get('season', [''])[0]
            self._json(at_releases.tv_overview(tid, int(sn) if sn.isdigit() else None))
        except Exception as e:
            self._json({'ok': False, 'error': str(e)}, 400)

    def _releases_suggestions(self):
        q = _qs(self.path)
        mode = (q.get('mode', ['taste'])[0] or 'taste')
        when = (q.get('when', ['all'])[0] or 'all')
        res = upcoming_for_you(_state, load_games(), load_diary(), mode=mode, when=when,
                               dismissed=at_releases.dismissed_ids())
        self._json({'ok': True, **res})

    def _releases_dismiss(self):
        b = self._body_json()
        try:
            at_releases.set_dismissed(b.get('tmdb_id'), not b.get('undo'))
        except (TypeError, ValueError):
            self._json({'ok': False}, 400); return
        self._json({'ok': True})

    def _releases_manual(self):
        b = self._body_json()
        ok = at_releases.set_manual(b.get('id') or '', (b.get('date') or '').strip() or None)
        self._json({'ok': ok, **at_releases.public_state()})

    def _releases_read(self):
        at_releases.mark_read(self._body_json().get('id') or 'all')
        self._json({'ok': True, **at_releases.public_state()})

    def _push_sub(self, on: bool):
        b = self._body_json()
        if on:
            ok = at_releases.subscribe(b.get('subscription'))
        else:
            at_releases.unsubscribe(b.get('endpoint') or '')
            ok = True
        self._json({'ok': ok, **at_releases.public_state()})

    def _service_worker(self):
        body = (STATIC / 'sw.js').read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'application/javascript; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers(); self.wfile.write(body)

    def _top_post(self):
        """Speichert die Top-10-Liste eines Mediums (Reihenfolge = Platzierung)."""
        try:
            n = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(n)) if n else {}
        except Exception:
            self._json({'ok': False, 'error': 'Ungültige Daten'}, 400); return
        kind, ids = data.get('kind'), data.get('ids')
        if kind not in ('movie', 'season', 'game') or not isinstance(ids, list):
            self._json({'ok': False, 'error': 'Ungültige Liste'}, 400); return
        clean = []
        for i in ids:
            if isinstance(i, dict):
                # Externer Treffer (TMDB/IGDB), nicht im Tagebuch: Cover einmalig lokal ablegen
                title = str(i.get('title') or '').strip()
                if not title:
                    continue
                poster = str(i.get('poster') or '')
                year = str(i.get('year') or '')[:4]
                if any(isinstance(c, dict) and c.get('title') == title and c.get('year') == year for c in clean):
                    continue
                if poster.startswith('http'):
                    fname = _dl_poster_force(poster, f"top|{kind}|{title}|{year}")
                    poster = f"/poster/{fname}" if fname else ''
                clean.append({'ext': True, 'kind': kind, 'title': title, 'year': year, 'poster': poster or None})
            elif isinstance(i, str) and i not in clean:
                clean.append(i)
        with _diary_lock:
            t = load_top()
            t[kind] = clean[:10]
            t.setdefault('_updated', {})[kind] = time.strftime('%Y-%m-%dT%H:%M:%S')
            save_top(t)
        self._json({'ok': True, 'top': load_top()})

    def _awards_post(self):
        try:
            n = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(n)) if n else {}
        except Exception:
            self._json({'ok': False, 'error': 'Ungültige Daten'}, 400); return
        year, cat = str(data.get('year', '')), data.get('cat', '')
        if not year or not cat:
            self._json({'ok': False, 'error': 'Jahr oder Kategorie fehlt'}, 400); return
        with _diary_lock:
            a = load_awards()
            a.setdefault(year, {})
            eid = data.get('entryId')
            if eid:
                a[year][cat] = eid
            else:
                a[year].pop(cat, None)
            save_awards(a)
        self._json({'ok': True, 'awards': load_awards()})

    # ── Letterboxd-Sync ──────────────────────────────────────────────────────
    def _lb_config_get(self):
        cfg = load_config()
        self._json({
            'username': cfg.get('lbUsername', ''),
            'lastSync': cfg.get('lbLastSync'),
        })

    def _lb_config_post(self):
        try:
            n = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(n)) if n else {}
        except Exception:
            self._json({'ok': False, 'error': 'Ungültige Daten'}, 400); return
        cfg = load_config()
        cfg['lbUsername'] = (data.get('username') or '').strip()
        save_config(cfg)
        self._json({'ok': True})

    def _lb_sync_get(self):
        cfg = load_config()
        username = cfg.get('lbUsername', '')
        try:
            self._json(sync_letterboxd(_state, username))
        except Exception as e:
            self._json({'ok': False, 'error': f'Sync fehlgeschlagen: {e}'}, 500)

    def _diary_delete(self):
        eid = _qs(self.path).get('id', [''])[0]
        if not eid:
            self._json({'ok': False, 'error': 'Keine ID'}, 400); return
        with _diary_lock:
            entries = [e for e in load_diary() if e.get('id') != eid]
            save_diary(entries)
        self._json({'ok': True, 'entries': entries})

    def _favicon(self):
        p = BASE / 'favicon.ico'
        if not p.exists():
            self.send_error(404); return
        body = p.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'image/x-icon')
        self.send_header('Content-Length', len(body))
        self.send_header('Cache-Control', 'max-age=604800')
        self.end_headers(); self.wfile.write(body)

    def _icon_png(self):
        p = BASE / 'icon.png'
        if not p.exists():
            self.send_error(404); return
        body = p.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'image/png')
        self.send_header('Content-Length', len(body))
        self.send_header('Cache-Control', 'max-age=604800')
        self.end_headers(); self.wfile.write(body)

    def _inter_font(self):
        p = BASE / 'fonts' / 'InterVariable.ttf'
        if not p.exists():
            self.send_error(404); return
        body = p.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'font/ttf')
        self.send_header('Content-Length', len(body))
        self.send_header('Cache-Control', 'max-age=604800')
        self.end_headers(); self.wfile.write(body)

    _STATIC_MIME = {'.js': 'application/javascript; charset=utf-8',
                    '.css': 'text/css; charset=utf-8'}

    def _static(self, fname: str):
        """Liefert app.js/app.css aus dem static/-Ordner (ausgelagert aus dem
        frueher riesigen eingebetteten <script>/<style>-String in HTML)."""
        fname = Path(fname).name
        p = STATIC / fname
        if not p.exists():
            self.send_error(404); return
        ctype = self._STATIC_MIME.get(p.suffix.lower(), 'application/octet-stream')
        body = p.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', len(body))
        self.send_header('Cache-Control', 'max-age=604800')
        self.end_headers(); self.wfile.write(body)

    def _poster(self, fname: str):
        fname = Path(fname).name
        p = POSTERS / fname
        if not p.exists():
            self.send_error(404); return
        body = p.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type','image/jpeg')
        self.send_header('Content-Length', len(body))
        self.send_header('Cache-Control','max-age=604800')
        self.end_headers(); self.wfile.write(body)

    def _person_photo(self, fname: str):
        fname = Path(fname).name
        p = PEOPLE / fname
        if not p.exists():
            self.send_error(404); return
        body = p.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type','image/jpeg')
        self.send_header('Content-Length', len(body))
        self.send_header('Cache-Control','max-age=604800')
        self.end_headers(); self.wfile.write(body)

    def _cast_get(self):
        q = _qs(self.path)
        title = (q.get('title', [''])[0] or '').strip()
        year  = (q.get('year',  [''])[0] or '').strip()
        kind  = (q.get('kind',  ['movie'])[0] or 'movie').strip()
        if not title:
            self._json({'ok': False, 'error': 'Titel fehlt'}, 400); return
        try:
            cast = get_cast(title, year, kind)
            self._json({'ok': True, 'cast': cast})
        except Exception as e:
            self._json({'ok': True, 'cast': [], 'warning': str(e)})

    def _tmdb_seasons_get(self):
        q = _qs(self.path)
        raw_ids = (q.get('ids', [''])[0] or '').strip()
        if not raw_ids:
            self._json({'ok': False, 'error': 'Keine IDs übergeben'}, 400); return
        out = {}
        for part in raw_ids.split(','):
            part = part.strip()
            if not part.isdigit():
                continue
            try:
                info = get_tmdb_season_count(int(part))
            except Exception:
                info = None
            if info is not None:
                out[part] = info
        self._json({'ok': True, 'shows': out})

    def _recommendations_get(self):
        mode = _qs(self.path).get('mode', ['recent'])[0]
        res = build_recommendations(_state, load_games(), load_diary(), mode=mode)
        self._json({'ok': True, **res})

    def _recommendations_backfill(self):
        try:
            n = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(n)) if n else {}
        except Exception:
            body = {}
        budget = max(1, min(40, int(body.get('budget') or 24)))
        result = backfill_recommendations(_state, load_games(), load_diary(), budget=budget)
        self._json({'ok': True, **result})

    def _recommendations_feedback(self):
        try:
            n = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(n)) if n else {}
        except Exception:
            body = {}
        if not set_feedback(body.get('key') or '', body.get('verdict') or ''):
            self._json({'ok': False, 'error': 'key/verdict fehlt'}, 400); return
        self._json({'ok': True})

    def _gcover(self, fname: str):
        fname = Path(fname).name
        p = GCOVERS / fname
        if not p.exists():
            self.send_error(404); return
        body = p.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type','image/jpeg')
        self.send_header('Content-Length', len(body))
        self.send_header('Cache-Control','max-age=604800')
        self.end_headers(); self.wfile.write(body)

    def _poster_candidates(self):
        q = _qs(self.path)
        title = (q.get('title', [''])[0] or '').strip()
        year  = (q.get('year',  [''])[0] or '').strip()
        kind  = (q.get('kind',  ['movie'])[0] or 'movie').strip()
        if not title:
            self._json({'ok': False, 'error': 'Titel fehlt'}, 400); return
        try:
            candidates = tmdb_poster_candidates(title, year, kind)
            self._json({'ok': True, 'candidates': candidates})
        except Exception as e:
            self._json({'ok': True, 'candidates': [], 'warning': str(e)})

    def _poster_choose(self):
        length = int(self.headers.get('Content-Length', 0))
        try:
            body = json.loads(self.rfile.read(length) or b'{}')
        except Exception:
            self._json({'ok': False, 'error': 'Ungültiges JSON'}, 400); return

        url = (body.get('url') or '').strip()
        key = (body.get('key') or '').strip()
        entry_id = (body.get('entryId') or '').strip()
        if not url:
            self._json({'ok': False, 'error': 'Keine Bild-URL'}, 400); return
        if not key and not entry_id:
            self._json({'ok': False, 'error': 'Kein Ziel (key/entryId) angegeben'}, 400); return

        dedup_key = key or f"diarypick|{entry_id}"
        fname = _dl_poster_force(url, dedup_key)
        if not fname:
            self._json({'ok': False, 'error': 'Download fehlgeschlagen'}, 500); return
        poster_path = _poster_url(fname)

        updated_diary = 0
        if key and key in _state:
            _state[key]['poster_local'] = fname
            save_cache(_state)
            with _diary_lock:
                entries = load_diary()
                for e in entries:
                    if e.get('key') == key:
                        e['poster'] = poster_path
                        updated_diary += 1
                if updated_diary:
                    save_diary(entries)

        if entry_id:
            with _diary_lock:
                entries = load_diary()
                for e in entries:
                    if e.get('id') == entry_id:
                        e['poster'] = poster_path
                        updated_diary += 1
                        # Auch die Diary-Cover-Cache aktualisieren, damit der
                        # Hintergrund-Backfill diese Auswahl nie überschreibt.
                        if e.get('kind') in ('movie', 'season'):
                            omdb_kind = 'series' if e['kind'] == 'season' else 'movie'
                            dkey = _dcov_key(omdb_kind, e.get('title', ''), e.get('year', ''))
                            covers = load_diary_covers()
                            c = covers.get(dkey, {})
                            c['poster_local'] = fname
                            covers[dkey] = c
                            save_diary_covers(covers)
                save_diary(entries)

        self._json({'ok': True, 'poster': poster_path, 'updated_diary': updated_diary,
                     'entries': load_diary()})

    def _title_search_get(self):
        q = _qs(self.path)
        query = (q.get('q', [''])[0] or '').strip()
        kind  = (q.get('kind', ['movie'])[0] or 'movie').strip()
        if not query:
            self._json({'ok': False, 'error': 'Suchbegriff fehlt'}, 400); return
        try:
            candidates = title_search_candidates(query, kind)
            self._json({'ok': True, 'candidates': candidates})
        except Exception as e:
            self._json({'ok': True, 'candidates': [], 'warning': str(e)})

    def _title_override_post(self):
        """Manuelle Korrektur einer falsch erkannten Bibliotheks-Datei (z.B.
        "Spartacus Remastered - US" -> "US" statt "Spartacus"). Der Nutzer
        wählt entweder einen konkreten Treffer aus /api/title-search (tmdb_id,
        eindeutig) oder gibt einen neuen Freitext-Titel ein (Fuzzy-Suche als
        Fallback). Die Korrektur wird dauerhaft unter dem Dateipfad hinterlegt
        (title_overrides.json), damit sie jeden künftigen Scan übersteht."""
        length = int(self.headers.get('Content-Length', 0))
        try:
            body = json.loads(self.rfile.read(length) or b'{}')
        except Exception:
            self._json({'ok': False, 'error': 'Ungültiges JSON'}, 400); return

        path = (body.get('path') or '').strip()
        kind = (body.get('kind') or 'movie').strip()
        tmdb_id = body.get('tmdb_id')
        title = (body.get('title') or '').strip()
        year = (body.get('year') or '').strip()
        if not path:
            self._json({'ok': False, 'error': 'Kein Dateipfad angegeben'}, 400); return
        if not tmdb_id and not title:
            self._json({'ok': False, 'error': 'Titel oder Auswahl fehlt'}, 400); return

        fb = _tmdb_fetch_full(title, year, kind, tmdb_id=tmdb_id)
        if not fb:
            self._json({'ok': False, 'error': 'Bei TMDB nicht gefunden'}, 404); return

        resolved_title = fb['title_actual'] or title
        resolved_year = fb['year_actual'] or year

        add_title_override(path, resolved_title, resolved_year, fb['tmdb_id'])

        # Alle alten (falschen) Cache-Einträge für diese Datei entfernen, damit
        # keiner als Karteileiche neben dem korrigierten Eintrag stehen bleibt
        # (nicht nur den ersten Treffer - falls mehrere frühere Fehlversuche
        # unter verschiedenen Keys im Cache gelandet sind).
        old_keys = [k for k, e in _state.items()
                    if e.get('file') == path or e.get('folder') == path]
        # "Zuletzt hinzugefügt" soll sich nach der Korrektur nicht ändern - das
        # echte Datei-/Ordner-Erstellungsdatum neu ermitteln (zuverlässigste
        # Quelle), sonst das älteste bekannte Datum der entfernten Duplikate
        # als Fallback übernehmen.
        try:
            added = Path(path).stat().st_ctime
        except OSError:
            added = min((_state[k].get('added') for k in old_keys if _state[k].get('added')),
                        default=time.time())
        for k in old_keys:
            del _state[k]

        new_key = _key(resolved_title, resolved_year, kind)
        if fb.get('poster_url'):
            fb['poster_local'] = _dl_poster_force(fb['poster_url'], new_key)
        entry = {
            't': resolved_title, 'y': resolved_year, 'kind': kind, 'key': new_key,
            ('file' if kind == 'movie' else 'folder'): path,
            **fb, 'omdb_pending': False, 'manual_override': True, 'fetched': time.time(),
            'added': added,
        }
        _state[new_key] = entry
        save_cache(_state)

        self._json({'ok': True, 'data': build_api(_state)})

    def _games_search(self):
        q = _qs(self.path)
        title = (q.get('title', [''])[0] or '').strip()
        if not title:
            self._json({'ok': False, 'error': 'Titel fehlt'}, 400); return
        try:
            results = igdb_search_multi(title)
            self._json({'ok': True, 'results': results})
        except Exception as e:
            self._json({'ok': True, 'results': [], 'warning': str(e)})

    def _games_add(self):
        length = int(self.headers.get('Content-Length', 0))
        try:
            body = json.loads(self.rfile.read(length) or b'{}')
        except Exception:
            self._json({'ok': False, 'error': 'Ungültiges JSON'}, 400); return
        title = (body.get('title') or '').strip()
        if not title:
            self._json({'ok': False, 'error': 'Titel fehlt'}, 400); return
        entry = get_or_create_game(
            title,
            platform_hint=(body.get('platform') or '').strip(),
            igdb_id=body.get('igdb_id'),
        )
        self._json({'ok': True, 'game': entry})

    def _games_delete(self):
        key = _qs(self.path).get('key', [''])[0]
        if not key:
            self._json({'ok': False, 'error': 'Key fehlt'}, 400); return
        with _games_lock:
            games = load_games()
            if key in games:
                del games[key]
                save_games(games)
        with _diary_lock:
            entries = [e for e in load_diary() if e.get('key') != key]
            save_diary(entries)
        self._json({'ok': True, 'entries': load_diary()})

    def _games_import(self):
        length = int(self.headers.get('Content-Length', 0))
        try:
            body = json.loads(self.rfile.read(length) or b'{}')
        except Exception:
            self._json({'ok': False, 'error': 'Ungültiges JSON'}, 400); return
        text = body.get('text') or ''
        if not text.strip():
            self._json({'ok': False, 'error': 'Kein Text übergeben'}, 400); return
        try:
            result = import_backloggd_journal(text)
            self._json({'ok': True, **result})
        except Exception as e:
            self._json({'ok': False, 'error': str(e)}, 500)

    def _json(self, data: dict, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.end_headers(); self.wfile.write(body)

    def log_message(self, *_): pass


# ── HTML ────────────────────────────────────────────────────────────────────────
def _static_asset_url(name: str) -> str:
    """Cache-busting-Query analog zu den Postern: sobald sich static/app.js
    oder static/app.css aendert (mtime), laden Browser automatisch die neue
    Version statt einer gecachten alten Kopie."""
    try:
        v = int((STATIC / name).stat().st_mtime)
    except OSError:
        v = 0
    return f'/static/{name}?v={v}'

_HTML_HEAD = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ArcTime</title>
<link rel="icon" type="image/x-icon" href="/favicon.ico">
<link rel="icon" type="image/png" sizes="1400x1400" href="/icon.png">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css">
"""
_HTML_MID = """
</head>
<body>

<div class="topbar">
<header>
  <input type="text" id="search" placeholder="Filme durchsuchen… ( / )" oninput="onSearchInput()">
  <div class="controls">
    <select id="sortBy" onchange="render()">
      <option value="watched" selected>Zuletzt geschaut</option>
      <option value="added">Zuletzt hinzugefügt</option>
      <option value="title">A–Z</option>
      <option value="year">Jahr</option>
      <option value="rating">Bewertung</option>
      <option value="runtime">Laufzeit</option>
    </select>
    <select id="filterGenre" onchange="render()">
      <option value="">Alle Genres</option>
    </select>
  </div>
  <button class="btn" id="randomBtn" onclick="pickRandom()" title="Zufälliger Titel (aktuelle Filter)">Zufall</button>
  <span class="count" id="countLabel"></span>
  <button class="btn btn-ghost" id="reloadBtn" onclick="doReload()" title="Bibliothek aktualisieren">
    <span id="reloadIcon">↻</span>
  </button>
  <div class="bell-wrap">
    <button class="bell-btn" id="bellBtn" onclick="toggleBell(event)" aria-label="Benachrichtigungen" aria-expanded="false">
      <i class="fa-solid fa-bell"></i><span class="bell-badge" id="bellBadge" hidden></span>
    </button>
    <div class="bell-panel" id="bellPanel" hidden></div>
  </div>
</header>

<div class="tabs">
  <div class="tab active" onclick="switchTab('movies')">Filme</div>
  <div class="tab" onclick="switchTab('series')">Serien</div>
  <div class="tab" onclick="switchTab('diary')">Tagebuch</div>
  <div class="tab" onclick="switchTab('stats')">Rückblick</div>
  <div class="tab" onclick="switchTab('recommendations')">Empfehlungen</div>
  <div class="tab" onclick="switchTab('releases')">Releases</div>
</div>
</div>

<div id="prog-wrap" style="display:none">
  <span id="prog-msg">Lade…</span>
  <div id="prog-bg"><div id="prog-bar"></div></div>
</div>

<div id="section-movies" class="section active">
  <div class="grid" id="grid-movies"></div>
</div>
<div id="section-series" class="section">
  <div class="grid" id="grid-series"></div>
</div>

<!-- Tagebuch -->
<div id="section-diary" class="section">
  <div class="diary-bar">
    <div class="dfilters">
      <button class="dfilter active" data-f="all"    onclick="setDiaryFilter('all',this)">Alle</button>
      <button class="dfilter"        data-f="movie"  onclick="setDiaryFilter('movie',this)">Filme</button>
      <button class="dfilter"        data-f="season" onclick="setDiaryFilter('season',this)">Serien</button>
      <button class="dfilter"        data-f="game"   onclick="setDiaryFilter('game',this)">Spiele</button>
    </div>
    <span class="dcount" id="diary-count"></span>
    <button class="btn" id="gameAddBtn" onclick="openGameAdd()">+ Spiel</button>
    <button class="btn" id="openGamesBtn" onclick="openOpenGames()" title="Aktuell laufende Spiele abschließen oder abbrechen">
      Offene Spiele<span class="og-btn-count" id="ogBtnCount" style="display:none"></span>
    </button>
    <div class="menu-wrap" id="ieMenuWrap">
      <button class="btn" id="ieMenuBtn" onclick="toggleIeMenu(event)">Import/Export ▾</button>
      <div class="menu-pop" id="ieMenuPop">
        <button class="menu-item" onclick="closeIeMenu();openImport()">Tagebuch importieren (CSV/HTML)</button>
        <button class="menu-item" id="gameImportBtn" onclick="closeIeMenu();openGameImport()">Backloggd-Import (Spiele)</button>
        <button class="menu-item" id="lbBtn" onclick="closeIeMenu();openLbModal()">Letterboxd-Sync einrichten</button>
        <a class="menu-item" href="/api/diary/csv" download onclick="closeIeMenu()">Tagebuch als CSV exportieren</a>
      </div>
    </div>
  </div>
  <div id="diary-list"></div>
</div>

<!-- Letterboxd-Sync -->
<div class="mo" id="lbModal" onclick="if(event.target===this)closeLbModal()">
  <div class="md" style="max-width:460px">
    <div class="mc" style="padding:24px">
      <div class="mh">
        <div><div class="mtitle">Letterboxd-Sync</div>
          <div class="myear" id="lbLastSync">Noch nie synchronisiert</div></div>
        <div class="mclose" onclick="closeLbModal()">✕</div>
      </div>
      <div class="rate-row">
        <input type="text" id="lbUsername" placeholder="Letterboxd-Benutzername">
        <button class="rate-save" id="lbSaveBtn">Speichern</button>
      </div>
      <div class="imp-note">
        Dein Letterboxd-<b>Profil muss öffentlich</b> sein. Bei jedem Start von
        mediathek.py werden automatisch neue Einträge deines Tagebuchs abgeglichen
        (RSS-Feed, zeigt die letzten ~50 Einträge). Bereits vorhandene Einträge
        werden nicht doppelt angelegt.
      </div>
      <button class="btn" id="lbSyncBtn" style="width:100%;justify-content:center;margin-top:6px">
        ↻ Jetzt synchronisieren
      </button>
      <div id="lbResult"></div>
    </div>
  </div>
</div>

<!-- Import-Dialog -->
<div class="mo" id="impModal" onclick="if(event.target===this)closeImport()">
  <div class="md" style="max-width:520px">
    <div class="mc" style="padding:24px">
      <div class="mh">
        <div><div class="mtitle">Tagebuch importieren</div>
          <div class="myear">Letterboxd · Serializd · eigene CSV</div></div>
        <div class="mclose" onclick="closeImport()">✕</div>
      </div>
      <div class="imp-drop" id="impDrop">
        CSV-Datei hierher ziehen oder klicken
      </div>
      <input type="file" id="impFile" accept=".csv,text/csv" style="display:none">
      <details class="imp-details">
        <summary>Anleitung anzeigen</summary>
        <div class="imp-note">
          <b>Letterboxd:</b> Einstellungen → Data → Export Your Data, dann <code>diary.csv</code>.<br>
          <b>Serializd:</b> Settings → Export (CSV) — oder direkt die eigene <b>Diary</b>-Seite
          auf serializd.com öffnen, Rechtsklick → <b>Untersuchen</b> (DevTools), den Diary-Bereich
          markieren, <b>„Copy outerHTML“</b> wählen und unten einfügen.<br>
          Staffeln werden automatisch erkannt. Bereits vorhandene Einträge (gleicher Titel, Datum
          und Staffel) werden übersprungen.
        </div>
      </details>
      <div class="rate-row">
        <textarea id="impPasteText" placeholder="Alternativ: HTML von der Serializd-Diary-Seite hier einfügen…" style="min-height:120px"></textarea>
      </div>
      <button class="btn" id="impPasteRunBtn" style="width:100%;justify-content:center;margin-top:6px"
              onclick="runImportPaste()">⬆ Eingefügten Text importieren</button>
      <div id="impResult"></div>
    </div>
  </div>
</div>

<!-- Spiel hinzufügen -->
<div class="mo" id="gameAddModal" onclick="if(event.target===this)closeGameAdd()">
  <div class="md" style="max-width:560px">
    <div class="mc" style="padding:24px">
      <div class="mh">
        <div><div class="mtitle">Spiel hinzufügen</div>
          <div class="myear">Suche via IGDB</div></div>
        <div class="mclose" onclick="closeGameAdd()">✕</div>
      </div>
      <div class="rate-row">
        <input type="text" id="gameSearchInput" placeholder="Spieltitel…"
               onkeydown="if(event.key==='Enter')runGameSearch()">
        <button class="rate-save" onclick="runGameSearch()">Suchen</button>
      </div>
      <div id="gameSearchResults"></div>
    </div>
  </div>
</div>

<!-- Backloggd-Journal-Import -->
<div class="mo" id="gameImportModal" onclick="if(event.target===this)closeGameImport()">
  <div class="md" style="max-width:560px">
    <div class="mc" style="padding:24px">
      <div class="mh">
        <div><div class="mtitle">Backloggd-Journal importieren</div>
          <div class="myear">Journaltext einfügen</div></div>
        <div class="mclose" onclick="closeGameImport()">✕</div>
      </div>
      <details class="imp-details">
        <summary>Anleitung anzeigen</summary>
        <div class="imp-note">
          Auf backloggd.com im eigenen Profil unter <b>Journal</b>: Rechtsklick auf die
          Journal-Liste → <b>Untersuchen</b> (DevTools), das umschließende Element markieren,
          <b>„Copy outerHTML“</b> wählen und hier einfügen. So werden IGDB-ID, Cover und
          Bewertung direkt übernommen. Alternativ funktioniert auch reiner Text der
          Journal-Seite, ist aber weniger zuverlässig.
        </div>
      </details>
      <div class="rate-row">
        <textarea id="gameImportText" placeholder="Journaltext hier einfügen…" style="min-height:180px"></textarea>
      </div>
      <button class="btn" id="gameImportRunBtn" style="width:100%;justify-content:center;margin-top:6px"
              onclick="runGameImport()">⬆ Importieren</button>
      <div id="gameImportResult"></div>
    </div>
  </div>
</div>

<!-- Offene Spiele -->
<div class="mo" id="openGamesModal" onclick="if(event.target===this)closeOpenGames()">
  <div class="md" style="max-width:600px">
    <div class="mc" style="padding:24px">
      <div class="mh">
        <div><div class="mtitle">Offene Spiele</div>
          <div class="myear">Aktuell als „Spielt gerade" markiert</div></div>
        <div class="mclose" onclick="closeOpenGames()">✕</div>
      </div>
      <div id="openGamesList"></div>
    </div>
  </div>
</div>

<!-- Cover-Auswahl -->
<div class="mo" id="posterModal" onclick="if(event.target===this)closePosterPicker()">
  <div class="md" style="max-width:680px">
    <div class="mc" style="padding:24px">
      <div class="mh">
        <div><div class="mtitle">Cover auswählen</div>
          <div class="myear" id="posterModalSub"></div></div>
        <div class="mclose" onclick="closePosterPicker()">✕</div>
      </div>
      <div id="posterModalBody"><div class="ep-loading">Suche Cover…</div></div>
    </div>
  </div>
</div>

<!-- Titel korrigieren (falsch erkannter Film/Serie) -->
<div class="mo" id="titleFixModal" onclick="if(event.target===this)closeTitleFix()">
  <div class="md" style="max-width:640px">
    <div class="mc" style="padding:24px">
      <div class="mh">
        <div><div class="mtitle">Titel korrigieren</div>
          <div class="myear" id="tfCurrent"></div></div>
        <div class="mclose" onclick="closeTitleFix()">✕</div>
      </div>
      <div class="tf-search">
        <input type="text" id="tfQuery" placeholder="Titel eingeben…" autocomplete="off"
          onkeydown="if(event.key==='Enter')searchTitleFix()">
        <button onclick="searchTitleFix()">Suchen</button>
      </div>
      <div id="tfResults"><div class="ep-loading">Suche…</div></div>
    </div>
  </div>
</div>

<!-- Schauspieler-Auswahl -->
<div class="mo" id="actorModal" onclick="if(event.target===this)closeActorPicker()">
  <div class="md" style="max-width:640px">
    <div class="mc" style="padding:24px">
      <div class="mh">
        <div><div class="mtitle" id="actorModalTitle">Person auswählen</div>
          <div class="myear" id="actorModalSub"></div></div>
        <div class="mclose" onclick="closeActorPicker()">✕</div>
      </div>
      <div id="actorModalBody"><div class="ep-loading">Lade Besetzung…</div></div>
    </div>
  </div>
</div>

<!-- Jahresauswertung -->
<div id="section-stats" class="section">
  <div class="diary-bar">
    <select id="statsYear" onchange="renderStats()"></select>
  </div>
  <div id="stats-body"></div>
</div>

<div id="section-recommendations" class="section">
  <div class="diary-bar">
    <div class="dfilters">
      <button class="dfilter active" data-f="all"    onclick="setRecFilter('all',this)">Alle</button>
      <button class="dfilter"        data-f="movie"  onclick="setRecFilter('movie',this)">Filme</button>
      <button class="dfilter"        data-f="series" onclick="setRecFilter('series',this)">Serien</button>
      <button class="dfilter"        data-f="game"   onclick="setRecFilter('game',this)">Spiele</button>
    </div>
    <span class="dcount" id="rec-count"></span>
    <select id="rec-mode" onchange="setRecMode(this.value)" title="Empfehlungs-Algorithmus">
      <option value="recent">Aktueller Geschmack</option>
      <option value="alltime">Allzeit-Geschmack</option>
      <option value="because">Weil du … mochtest</option>
      <option value="people">Regie &amp; Cast</option>
      <option value="gems">Geheimtipps</option>
      <option value="surprise">Überrasch mich</option>
    </select>
  </div>
  <div class="rec-note">
    <span id="rec-mode-desc"></span>
    <div class="rec-progress" id="rec-progress" hidden>
      <div class="rec-progress-bar"><div class="rec-progress-fill" id="rec-progress-fill"></div></div>
      <span id="rec-progress-label"></span>
    </div>
  </div>
  <div id="rec-grid"></div>
</div>

<div id="section-releases" class="section">
  <div id="releases-body"></div>
</div>

<div id="empty">Keine Einträge gefunden.</div>
<div id="toast"></div>

<!-- Modal -->
<div class="mo" id="mo" onclick="bgClose(event)">
  <div class="md">
    <div class="mc">
      <div class="mh">
        <div class="mh-left">
          <div class="mthumb" id="m-poster"></div>
          <div><div class="mtitle" id="m-title"></div><div class="myear" id="m-year"></div></div>
        </div>
        <div class="mclose" onclick="closeModal()">✕</div>
      </div>
      <div class="mrr">
        <div class="mrating" id="m-rating"></div>
        <div class="mgenres" id="m-genres"></div>
      </div>
      <hr class="div">
      <!-- Beschreibung + Laufzeit/Regie/etc.: bei Filmen nur eingeklappt, wenn
           die Beschreibung länger als 2 Zeilen ist (per JS gemessen); bei
           Serien immer eingeklappt gestartet, da hier meist zuerst die
           Episoden/Bewertung interessieren. -->
      <div class="minfo" id="m-info">
        <div class="minfo-body" id="m-info-body">
          <div class="mplot" id="m-plot"></div>
          <div class="mmg" id="m-meta"></div>
        </div>
        <div class="minfo-toggle" id="m-info-toggle" onclick="toggleInfo()">
          <span id="m-info-toggle-label">Mehr anzeigen</span>
          <i class="fa-solid fa-chevron-down"></i>
        </div>
      </div>
      <div class="mlinks" id="m-links"></div>
    </div>
    <div id="m-ep-section" style="display:none">
      <hr class="div">
      <div class="ep-wrap">
        <div class="ep-title">Episoden</div>
        <div id="m-ep-list"><div class="ep-loading">Lade…</div></div>
      </div>
    </div>
    <hr class="div" id="m-rate-div">
    <div class="rate-box" id="m-rate"></div>
  </div>
</div>

<!-- Empfehlungs-Detail-Modal: rein informativ (Beschreibung/Metadaten), kein
     Bewerten/Episoden - der Titel ist ja noch nicht in der Bibliothek. -->
<div class="mo" id="recMo" onclick="bgCloseRec(event)">
  <div class="md">
    <div class="mc">
      <div class="mh">
        <div class="mh-left">
          <div class="mthumb" id="rm-poster"></div>
          <div><div class="mtitle" id="rm-title"></div><div class="myear" id="rm-year"></div></div>
        </div>
        <div class="mclose" onclick="closeRecModal()">✕</div>
      </div>
      <div class="mrr">
        <div class="mrating" id="rm-rating"></div>
        <div class="mgenres" id="rm-genres"></div>
      </div>
      <div class="rm-why" id="rm-why"></div>
      <hr class="div">
      <div class="mplot" id="rm-plot"></div>
      <div class="mmg" id="rm-meta"></div>
      <div class="mlinks" id="rm-links"></div>
      <div class="rm-fb">
        <button class="rm-fb-btn dislike" onclick="recFeedbackFromModal('dislike')"><i class="fa-solid fa-xmark"></i> Nicht mein Geschmack</button>
        <button class="rm-fb-btn quality" onclick="recFeedbackFromModal('quality')"><i class="fa-solid fa-star-half-stroke"></i> Thema ja, Qualität nein</button>
        <button class="rm-fb-btn like" onclick="recFeedbackFromModal('like')"><i class="fa-solid fa-check"></i> Gute Empfehlung – angefangen</button>
      </div>
    </div>
  </div>
</div>

"""
_HTML_TAIL = """
</body>
</html>"""

HTML = (
    _HTML_HEAD
    + f'<link rel="stylesheet" href="{_static_asset_url("app.css")}">'
    + _HTML_MID
    + f'<script src="{_static_asset_url("app.js")}"></script>'
    + _HTML_TAIL
)


