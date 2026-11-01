#!/usr/bin/env python3
"""
ArcTime - lokale Film- und Serienbibliothek mit OMDB-Daten
Starten: python mediathek.py  ->  http://localhost:8765

Dieses Skript ist nur noch der duenne Einstiegspunkt. Die eigentliche Logik
lebt in den at_*.py-Modulen im selben Ordner (siehe deren Docstrings):
  at_config         Pfade, Konstanten, Datei-Namensparsing, Autostart
  at_store          JSON-Flatfile-Persistenz (cache/diary/awards/config/...)
  at_scan           Dateisystem-Scanner
  at_metadata       TMDB/OMDB/IGDB-Anreicherung, Spiele-Registry, Cover
  at_recommendations Empfehlungen
  at_import         Backloggd/CSV/Serializd/Letterboxd-Import & -Sync
  at_refresh        Scan+Anreicherung orchestrieren (refresh())
  at_playback       VLC-Start, DLNA/UPnP-Cast auf den LG-Fernseher
  at_server         HTTP-Handler + HTML-Seiten-Zusammenbau
Frontend liegt in static/app.js und static/app.css (nicht mehr inline).
"""

import sys, threading, webbrowser
from http.server import ThreadingHTTPServer
import at_releases

from at_config import FILME, PORT, SERIEN, get_local_ip, setup_autostart
from at_store import load_cache, load_config
from at_import import sync_letterboxd
import at_refresh
from at_refresh import refresh
from at_server import Handler, _state

# ── Main ────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    # --autostart: wird nur vom Windows-Autostart-Eintrag übergeben (Login-Start
    # im Hintergrund) - dabei soll sich KEIN Browser-Tab öffnen. Beim manuellen
    # Start (Doppelklick, Terminal) fehlt das Flag, Browser öffnet wie gewohnt.
    is_autostart = '--autostart' in sys.argv

    local_ip = get_local_ip()

    print("=" * 52)
    print("  ArcTime")
    print("=" * 52)
    print(f"  Filme:   {FILME}")
    print(f"  Serien:  {SERIEN}\n")

    print("Richte Autostart ein…")
    setup_autostart()

    # Cache laden ist reines Disk-I/O (schnell) - die eigentliche Anreicherung
    # per OMDB/TMDB (refresh) kann dagegen mehrere Sekunden bis Minuten dauern.
    # Damit die Seite ohne Verzögerung geöffnet werden kann, startet der Server
    # SOFORT mit dem bereits gecachten Stand, und refresh() läuft im Hintergrund
    # weiter - neue/fehlende Einträge poppen dann einfach nach und nach auf.
    print("Lade Cache…")
    _state.update(load_cache())
    n_mov = sum(1 for k in _state if k.startswith('movie|'))
    n_ser = sum(1 for k in _state if k.startswith('series|'))
    print(f"  {n_mov} Filme · {n_ser} Serien im Cache")

    print(f"\nErreichbar unter:")
    print(f"  Lokal:    http://localhost:{PORT}")
    print(f"  Im Netz:  http://{local_ip}:{PORT}")
    print("\nZum Beenden: Strg+C\n")

    _httpd = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)

    if not is_autostart:
        threading.Thread(
            target=lambda: webbrowser.open(f"http://localhost:{PORT}"),
            daemon=True
        ).start()

    def _bg_startup_work():
        """Läuft im Hintergrund NACH Serverstart - Ordner scannen/anreichern
        (OMDB/TMDB) und Letterboxd-Sync blockieren die Anzeige nicht mehr."""
        print("Scanne Ordner und reichere Bibliothek an…")
        _state.update(refresh(_state))
        if at_refresh._omdb_warning:
            print(f"\n  ⚠ {at_refresh._omdb_warning}")
        n_m = sum(1 for k in _state if k.startswith('movie|'))
        n_s = sum(1 for k in _state if k.startswith('series|'))
        print(f"\n  {n_m} Filme · {n_s} Serien im Cache (nach Anreicherung)")

        lb_user = load_config().get('lbUsername', '')
        if lb_user:
            try:
                r = sync_letterboxd(_state, lb_user)
                if r.get('ok'):
                    print(f"  Letterboxd: {r['added']} neue Tagebucheinträge synchronisiert "
                          f"({r['skipped']} bereits vorhanden)")
                else:
                    print(f"  Letterboxd-Sync fehlgeschlagen: {r.get('error')}")
            except Exception as e:
                print(f"  Letterboxd-Sync fehlgeschlagen: {e}")

    threading.Thread(target=_bg_startup_work, daemon=True).start()
    at_releases.start_scheduler()     # täglicher Release-Check (TMDB)

    try:
        _httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    _httpd.server_close()
