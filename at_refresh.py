"""ArcTime - Refresh-Orchestrierung: Scan + Anreicherung zusammenfuehren, cache.json aktuell halten."""
import hashlib, html, json, os, re, shutil, socket, subprocess, sys, time, threading, urllib.parse, urllib.request, webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path

from at_config import POSTERS
from at_scan import scan_filme, scan_serien
from at_store import load_last_played, save_cache
from at_metadata import OmdbUnavailable, _dcov_key, _dl_poster, _existing_poster_for_key, _merge_tmdb_fallback, _omdb_fetch, _tmdb_fallback_fetch, build_games_api, ensure_diary_poster

# ── Refresh ────────────────────────────────────────────────────────────────────
_reload_lock = threading.Lock()
_omdb_warning: str | None = None   # von der Weboberfläche nach dem Reload angezeigt
_NOT_FOUND_RETRY_SECONDS = 6 * 3600  # not_found-Einträge frühestens nach 6h neu versuchen

def _entry_path_exists(e: dict) -> bool:
    """Prüft, ob die Datei/der Ordner eines Cache-Eintrags noch existiert.
    Bei fehlendem Pfad oder Zugriffsfehlern im Zweifel True (nicht anfassen)."""
    p = e.get('file') or e.get('folder')
    if not p:
        return True
    try:
        return Path(p).exists()
    except OSError:
        return True


def _recover_local_posters(cache: dict) -> int:
    """Versucht für Bibliotheks-Einträge ohne Poster ein bereits lokal
    heruntergeladenes Cover wiederzuverwenden, statt erneut bei OMDB
    anzufragen. Zwei Stellen laden Poster mit unterschiedlichen Dateinamen-
    Schemata: die Bibliothek selbst (Key wie "movie|titel|jahr") und der
    separate Tagebuch-Cover-Backfill (Key wie "diary|movie|titel|jahr", siehe
    ensure_diary_poster) - beide werden hier geprüft. Reine
    Dateisystem-Prüfung, kein Netzwerkzugriff, daher unabhängig davon, ob und
    wie oft OMDB gerade erreichbar ist."""
    recovered = 0
    for key, e in cache.items():
        if e.get('poster_local') or e.get('not_found'):
            continue
        fname = _existing_poster_for_key(key)
        if not fname:
            kind = e.get('kind') or ('movie' if key.startswith('movie|') else 'series')
            title = e.get('title_actual') or e.get('t', '')
            year = e.get('year_actual') or e.get('y', '')
            if title:
                dkey = _dcov_key(kind, title, year)
                fname = _existing_poster_for_key(f"diary|{dkey}")
        if fname:
            e['poster_local'] = fname
            recovered += 1
    return recovered


def _probe_has_subs(path: str) -> bool | None:
    """Prüft per ffprobe, ob die Datei mindestens einen eingebetteten
    Untertitel-Stream enthält (z.B. SRT/ASS/PGS in einer MKV). Reine lokale
    Medienanalyse, kein Netzwerkzugriff. Gibt None zurück, wenn ffprobe nicht
    installiert ist oder die Prüfung aus anderem Grund fehlschlägt - dieser
    Fall wird bewusst NICHT als "keine Untertitel" gewertet, sonst würde ohne
    ffmpeg jeder Film fälschlich das "keine Untertitel"-Icon bekommen."""
    try:
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        out = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 's',
             '-show_entries', 'stream=index', '-of', 'csv=p=0', path],
            capture_output=True, text=True, timeout=20, creationflags=creationflags,
        )
        if out.returncode != 0:
            return None
        return bool(out.stdout.strip())
    except Exception:
        return None


def _backfill_subtitle_flags(cache: dict, limit: int = 25) -> int:
    """Trägt für Filme ohne bekannten Untertitel-Status per ffprobe nach, ob
    ein eingebetteter Untertitel-Stream vorhanden ist (fürs "keine Untertitel"-
    Icon auf dem Cover). Läuft wie das Cover-Backfill in kleinen Schritten pro
    Refresh, damit ein Erstlauf mit hunderten Filmen nicht blockiert. Ohne
    installiertes ffprobe (Teil von ffmpeg) bleibt das Feld einfach leer - kein
    Fehler, nur eben kein Hinweis-Icon."""
    if not shutil.which('ffprobe'):
        return 0
    done = 0
    for e in cache.values():
        if done >= limit:
            break
        if e.get('kind') != 'movie' or 'has_subs' in e:
            continue
        path = e.get('file')
        if not path or not Path(path).exists():
            continue
        e['has_subs'] = _probe_has_subs(path)
        done += 1
    return done


def refresh(cache: dict, on_progress=None) -> dict:
    all_entries = scan_filme() + scan_serien()

    # Einträge entfernen, deren Datei/Ordner nicht mehr existiert (gelöscht,
    # umbenannt oder außerhalb der App verschoben) – sonst bleiben sie für
    # immer im Cache und tauchen nach jedem Reload wieder auf.
    #
    # WICHTIG: Ein Eintrag wird nur gelöscht, wenn sein Datei-/Ordnerpfad
    # tatsächlich nicht mehr auf der Platte existiert – nicht schon allein
    # weil der aktuelle Scan-Durchlauf keinen passenden Key geliefert hat.
    # Reine Key-Abweichungen (z.B. durch Parsing-Feinheiten oder kurzzeitig
    # nicht erreichbare Laufwerke/Ordner) dürfen niemals ganze Kategorien
    # (z.B. alle Serien) aus dem Cache und damit ihre Cover löschen.
    scanned_keys = {e['key'] for e in all_entries}
    stale_keys = [k for k, e in cache.items()
                  if k not in scanned_keys and not _entry_path_exists(e)]
    for k in stale_keys:
        del cache[k]
    if stale_keys:
        save_cache(cache)

    # Karteileichen: dieselbe Datei läuft jetzt unter einem ANDEREN Key als
    # zuvor (z.B. weil eine Titel-Korrektur zwischenzeitlich griff, ohne dass
    # der alte Cache-Eintrag dabei entfernt wurde, oder weil sich die
    # Namens-Erkennung verbessert hat) - die Datei existiert ja weiterhin,
    # die obige "existiert nicht mehr"-Prüfung greift hier also nicht. Ohne
    # diesen Schritt bliebe der alte, falsch erkannte Eintrag für immer neben
    # dem neuen, korrekten stehen.
    current_key_by_path = {}
    for e in all_entries:
        p = e.get('file') or e.get('folder')
        if p:
            current_key_by_path[p] = e['key']
    orphaned_keys = [k for k, e in cache.items()
                     if k not in scanned_keys
                     and (e.get('file') or e.get('folder')) in current_key_by_path]
    for k in orphaned_keys:
        del cache[k]
    if orphaned_keys:
        save_cache(cache)

    # Pfade + Qualität für bekannte Einträge aktualisieren (kein API-Aufruf)
    for e in all_entries:
        if e['key'] in cache:
            for field in ('file', 'folder', 'res', 'hdr'):
                if field in e:
                    cache[e['key']][field] = e[field]
                elif field in cache[e['key']] and field in ('res', 'hdr'):
                    pass  # behalten wenn nicht im Scan-Ergebnis
            # 'added' aus dem echten Datei-/Ordner-Erstellungsdatum nachtragen
            # bzw. auffrischen - betrifft vor allem ältere Einträge, die schon
            # vor Einführung dieses Felds im Cache lagen (Backfill ohne
            # gesondertes Migrationsskript, läuft bei jedem Refresh mit).
            if e.get('added'):
                cache[e['key']]['added'] = e['added']

    global _omdb_warning
    _omdb_warning = None

    # Neue lokale Dateien SOFORT sichtbar machen, als Platzhalter ganz ohne
    # OMDB-Daten (Titel/Jahr/Datei kommen ja allein aus dem Scan). Die
    # Bibliothek darf niemals davon abhängen, dass ein externes API erreichbar
    # ist - OMDB liefert nur Cover/Beschreibung/Genre als Anreicherung obendrauf.
    new = [e for e in all_entries if e['key'] not in cache]
    for e in new:
        # 'added' kommt aus dem Scan (echtes Datei-/Ordner-Erstellungsdatum,
        # siehe scan_filme()/scan_serien()) - Fallback auf "jetzt" nur falls es
        # ausnahmsweise nicht ermittelt werden konnte (z.B. OSError beim Stat).
        cache[e['key']] = {**e, 'omdb_pending': True, 'fetched': 0,
                           'added': e.get('added') or time.time()}

    # Für alle Einträge ohne Poster prüfen, ob schon eine passende Datei lokal
    # liegt (z.B. von einem früheren Durchlauf, oder unter dem separaten
    # Diary-Cover-Key, falls der Titel auch im Tagebuch geloggt wurde) - reine
    # Dateisystem-Prüfung ohne Netzwerkzugriff, läuft daher IMMER und komplett
    # unabhängig davon, ob/wie oft OMDB gerade erreichbar ist.
    if _recover_local_posters(cache):
        save_cache(cache)
    elif new:
        save_cache(cache)

    # Untertitel-Status für Filme ohne bekannten Stand nachtragen (siehe
    # _backfill_subtitle_flags) - ebenfalls reine Dateisystem-/ffprobe-Prüfung,
    # unabhängig von OMDB/TMDB.
    if _backfill_subtitle_flags(cache):
        save_cache(cache)

    # Bereits als "nicht gefunden" gecachte Einträge nach einer Weile erneut
    # versuchen - z.B. war die Ursache damals ein OMDB-Rate-Limit oder ein
    # Netzwerkfehler, keine echte Nichtexistenz des Titels. Ohne das würde ein
    # einziger vorübergehender API-Ausfall betroffene Cover für immer leer
    # lassen, auch nach beliebig vielen weiteren Klicks auf "Aktualisieren".
    # Einträge, die wegen eines OMDB-Ausfalls nur mit lokalen Daten angelegt
    # wurden (omdb_pending, inkl. der gerade eben neu angelegten), werden
    # dagegen bei JEDEM Refresh erneut versucht - kein Cooldown, da sie ja noch
    # nie erfolgreich angereichert wurden.
    #
    # Einträge, die per TMDB-Fallback bereits vollständig lokal nutzbar sind
    # (Poster/Genre/Beschreibung vorhanden, data_source='tmdb'), gelten als
    # erledigt und werden NICHT bei jedem Start erneut angefragt - stattdessen
    # wie "nicht gefunden" nur alle paar Stunden einmal, um zu prüfen, ob OMDB
    # inzwischen wieder verfügbar ist (für die OMDB-exklusiven Felder wie
    # IMDb-Bewertung). Erst lokal, API nur gelegentlich im Hintergrund.
    now = time.time()
    todo = [e for e in all_entries if cache[e['key']].get('omdb_pending') or (
        (cache[e['key']].get('not_found') or cache[e['key']].get('data_source') == 'tmdb')
        and (now - cache[e['key']].get('fetched', 0)) > _NOT_FOUND_RETRY_SECONDS
    )]
    total = len(todo)

    if on_progress:
        on_progress(0, total, f"{total} Einträge werden angereichert (Cover/Beschreibung)…")

    unavailable_streak = 0
    aborted = False
    tmdb_fallback_used = 0
    tmdb_fully_missing = 0
    for i, e in enumerate(todo):
        if on_progress:
            on_progress(i, total, f"Lade: {e['t']}")
        try:
            data = _omdb_fetch(e['t'], e['y'], e['kind'])
        except OmdbUnavailable as ex:
            unavailable_streak += 1
            print(f"  ⚠  {e['t']}: OMDB nicht verfügbar ({ex})")
            # OMDB ist gerade nicht erreichbar - bevor der Eintrag ganz ohne
            # Cover/Beschreibung bleibt, TMDB als Ersatzquelle versuchen. Gilt
            # danach als erledigt (omdb_pending=False) - NICHT bei jedem Start
            # erneut anfragen, sondern erst lokal nutzen. Der Eintrag zählt
            # wegen data_source='tmdb' trotzdem zum Cooldown-Retry oben (alle
            # paar Stunden), um die OMDB-exklusiven Felder nachzuholen, sobald
            # OMDB wieder verfügbar ist - aber eben nicht bei jedem Refresh.
            fb = _tmdb_fallback_fetch(e)
            if fb:
                _merge_tmdb_fallback(cache, e, fb, omdb_pending=False)
                tmdb_fallback_used += 1
                print(f"  ↷  {e['t']}: TMDB-Ersatzdaten verwendet")
            else:
                tmdb_fully_missing += 1
                # WICHTIG: Die Datei ist lokal ja vorhanden und gescannt - sie soll
                # trotzdem in der Bibliothek auftauchen, nur eben (noch) ganz ohne
                # Cover/Beschreibung/Genre. Kein not_found (das wäre inhaltlich
                # falsch), sondern ein eigener "omdb_pending"-Zustand, der bei
                # jedem weiteren Refresh automatisch erneut versucht wird.
                # Bestehenden Cache-Eintrag mergen statt überschreiben - sonst
                # geht ein per _recover_local_posters wiederverwendetes Poster
                # (das hat `e`, der rohe Scan-Eintrag, nie) sofort wieder verloren.
                cache[e['key']] = {**cache[e['key']], **e, 'omdb_pending': True, 'fetched': time.time()}
            # Bei mehreren Fehlschlägen in Folge ist OMDB insgesamt nicht
            # erreichbar (Rate-Limit/Key/Netzwerk) - weitere OMDB-Aufrufe in
            # diesem Durchlauf sind sinnlos. Die übrigen Einträge trotzdem noch
            # per TMDB anreichern, statt sie bis zum nächsten Klick auf
            # "Aktualisieren" komplett unangereichert zu lassen.
            if unavailable_streak >= 3:
                aborted = True
                remaining = todo[i + 1:]
                for j, e2 in enumerate(remaining):
                    if on_progress:
                        on_progress(i + 1 + j, total, f"TMDB-Ersatzdaten: {e2['t']}")
                    fb2 = _tmdb_fallback_fetch(e2)
                    if fb2:
                        _merge_tmdb_fallback(cache, e2, fb2, omdb_pending=False)
                        tmdb_fallback_used += 1
                    else:
                        tmdb_fully_missing += 1
                        cache[e2['key']] = {**cache[e2['key']], **e2, 'omdb_pending': True, 'fetched': time.time()}
                break
            continue
        unavailable_streak = 0
        if data:
            if data.get('poster_url'):
                data['poster_local'] = _dl_poster(data['poster_url'], e['key'])
            elif not data.get('poster_local'):
                # OMDB kennt kein Poster für diesen Titel - ein per
                # _recover_local_posters bereits wiederverwendetes lokales
                # Poster nicht verwerfen (data enthält sonst explizit
                # poster_local:None und würde es beim Merge überschreiben).
                data.pop('poster_local', None)
            # Einmalig zusätzlich TMDB-ID/-Bewertung als Zweitquelle sichern
            # (nur falls noch nicht vorhanden) - damit der Titel auch bei einem
            # künftigen OMDB-Ausfall bereits vollständig lokal nutzbar ist, und
            # damit die Serien-Status-Erkennung ("Completed") auch für Einträge
            # aus der eigenen Bibliothek per TMDB-Staffelzahl funktioniert.
            if not cache[e['key']].get('tmdb_id'):
                extra = _tmdb_fallback_fetch(e)
                if extra:
                    for f in ('tmdb_id', 'tmdb_rating', 'tmdb_votes', 'tagline'):
                        if extra.get(f) is not None:
                            data.setdefault(f, extra[f])
            cache[e['key']] = {**cache[e['key']], **e, **data, 'omdb_pending': False, 'fetched': time.time()}
            print(f"  ✓  {e['t']}")
        else:
            # OMDB kennt den Titel wirklich nicht (echtes "not found", keine
            # Störung) - TMDB als Zweitquelle versuchen, bevor endgültig
            # aufgegeben wird (manche Titel gibt es nur auf einer der beiden
            # Plattformen).
            fb = _tmdb_fallback_fetch(e)
            if fb:
                _merge_tmdb_fallback(cache, e, fb, omdb_pending=False)
                tmdb_fallback_used += 1
                print(f"  ↷  {e['t']}: bei OMDB nicht gefunden, TMDB-Daten verwendet")
            else:
                cache[e['key']] = {**e, 'not_found': True, 'fetched': time.time()}
                print(f"  ✗  {e['t']} (nicht gefunden, auch nicht bei TMDB)")

    if todo:
        save_cache(cache)

    if aborted:
        _omdb_warning = (
            f"OMDB ist gerade nicht erreichbar. "
            + (f"{tmdb_fallback_used} Einträge wurden stattdessen mit TMDB-Daten (Cover/Beschreibung) versorgt. "
               if tmdb_fallback_used else '')
            + (f"{tmdb_fully_missing} Einträge blieben ganz ohne Zusatzdaten (auch bei TMDB nicht gefunden). "
               if tmdb_fully_missing else '')
            + (f"Die {tmdb_fully_missing} ganz ohne Daten werden beim nächsten Aktualisieren erneut versucht. "
               if tmdb_fully_missing else '')
            + (f"Die {tmdb_fallback_used} mit TMDB-Daten werden nur noch gelegentlich im Hintergrund "
               f"auf OMDB geprüft (für IMDb-Bewertung o.ä.), nicht mehr bei jedem Start."
               if tmdb_fallback_used else '')
        )

    if on_progress:
        on_progress(total, total, 'Abgebrochen (OMDB nicht erreichbar)' if aborted else 'Fertig')
    return cache


def _poster_url(fname: str | None) -> str | None:
    """Poster-URL mit Cache-Buster (Datei-mtime als Query-Parameter). Ohne das
    behält der Browser nach einer Titel-Korrektur oder manuellen Cover-Wahl das
    alte Bild im Disk-Cache, wenn der neue Dateiname zufällig identisch mit dem
    alten ist (z.B. weil sich am Dateinamen-Key nichts geändert hat, nur die
    zugrunde liegende TMDB-Zuordnung) - die URL sah dann exakt gleich aus und
    der Browser hat nie neu geladen, obwohl die Datei auf der Platte längst
    überschrieben war."""
    if not fname:
        return None
    try:
        v = int((POSTERS / fname).stat().st_mtime)
    except OSError:
        v = 0
    return f"/poster/{fname}?v={v}"


def build_api(cache: dict) -> dict:
    movies, series = [], []
    last_played = load_last_played()
    for key, e in cache.items():
        if e.get('not_found'):
            obj = {'t': e.get('t',''), 'y': e.get('y'), 'key': key,
                   'not_found': True, 'file': e.get('file',''),
                   'folder': e.get('folder',''),
                   'res': e.get('res',''), 'hdr': e.get('hdr',''),
                   'added': e.get('added') or e.get('fetched') or 0}
        else:
            obj = {
                't':       e.get('title_actual') or e.get('t',''),
                'y':       e.get('year_actual')  or e.get('y') or '',
                'key':     key,
                'poster':  _poster_url(e.get('poster_local')),
                'genre':   e.get('genre',''),
                'runtime': e.get('runtime',''),
                # Bewertung: bevorzugt IMDb (von OMDB), sonst TMDB-Bewertung als
                # Ersatz - damit ein Titel, der bislang nur über den TMDB-Fallback
                # angereichert wurde, trotzdem eine Bewertung anzeigt statt "–".
                'rating':  e.get('imdb_rating') if e.get('imdb_rating') is not None
                           else (round(e['tmdb_rating'], 1) if e.get('tmdb_rating') else None),
                'votes':   e.get('imdb_votes') or (str(e.get('tmdb_votes','')) if e.get('tmdb_votes') else ''),
                'rating_source': 'imdb' if e.get('imdb_rating') is not None
                                  else ('tmdb' if e.get('tmdb_rating') else None),
                'plot':    e.get('plot',''),
                'director':e.get('director',''),
                'actors':  e.get('actors',''),
                'country': e.get('country',''),
                'language':e.get('language',''),
                'awards':  e.get('awards',''),
                'rated':   e.get('rated',''),
                'imdb_id': e.get('imdb_id',''),
                'seasons': e.get('total_seasons',''),
                'file':    e.get('file',''),
                'folder':  e.get('folder',''),
                # None = noch nicht geprüft/ffprobe fehlt (kein Icon), False =
                # geprüft und wirklich keine eingebetteten Untertitel gefunden.
                'has_subs': e.get('has_subs') if e.get('kind') == 'movie' else None,
                'res':     e.get('res',''),
                'hdr':     e.get('hdr',''),
                'last_played': last_played.get(key),
                'added':   e.get('added') or e.get('fetched') or 0,
            }
        (movies if key.startswith('movie|') else series).append(obj)
    return {'movies': movies, 'series': series, 'games': build_games_api()}


