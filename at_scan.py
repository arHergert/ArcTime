"""ArcTime - Dateisystem-Scanner: findet Filme/Serien-Ordner und baut Bibliotheks-Eintraege."""
import hashlib, html, json, os, re, shutil, socket, subprocess, sys, time, threading, urllib.parse, urllib.request, webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path

from at_config import FILME, SERIEN, VIDEO_EXT, extract_quality, parse_name
from at_store import load_removed, load_title_overrides

# ── Scanner ────────────────────────────────────────────────────────────────────
def _key(t: str, y, kind: str) -> str:
    return f"{kind}|{re.sub(r'[^a-z0-9]', '', t.lower())}|{y or ''}"


def scan_filme() -> list:
    if not FILME.exists():
        return []
    overrides = load_title_overrides()
    removed = load_removed()
    entries, seen_keys, seen_titles = [], set(), {}
    for f in FILME.rglob('*'):
        if f.suffix.lower() not in VIDEO_EXT:
            continue
        if str(f) in removed:
            continue
        try:
            st = f.stat()
            if st.st_size < 50_000_000:
                continue
        except OSError:
            continue
        ov = overrides.get(str(f))
        if ov:
            t, y = ov.get('title', ''), ov.get('year') or None
        else:
            t, y = parse_name(f.stem)
        if not t or len(t) < 2:
            continue
        tl = t.lower()
        if tl in seen_titles:
            if y and not seen_titles[tl]['y']:
                old = seen_titles[tl]['key']
                entries = [e for e in entries if e['key'] != old]
                seen_keys.discard(old)
            else:
                continue
        k = _key(t, y, 'movie')
        if k in seen_keys:
            continue
        seen_keys.add(k)
        seen_titles[tl] = {'key': k, 'y': y}
        q = extract_quality(f.name)
        # st_ctime ist unter Windows das echte Erstellungsdatum der Datei -
        # also genau der Zeitpunkt, zu dem die Datei tatsächlich in die
        # Bibliothek gelangt ist (kopiert/verschoben). Das ist ein
        # zuverlässigerer Indikator für "zuletzt hinzugefügt" als ein
        # Cache-Zeitstempel, der sich bei jeder erneuten Anreicherung
        # (OMDB/TMDB-Refresh) unabhängig davon ändert.
        entries.append({'t': t, 'y': y, 'kind': 'movie', 'key': k,
                        'file': str(f), 'added': st.st_ctime, **q})
    return entries


def scan_serien() -> list:
    if not SERIEN.exists():
        return []
    overrides = load_title_overrides()
    removed = load_removed()
    entries, seen_keys, seen_titles = [], set(), {}
    for d in sorted(SERIEN.iterdir()):
        if not d.is_dir():
            continue
        if str(d) in removed:
            continue
        ov = overrides.get(str(d))
        if ov:
            t, y = ov.get('title', ''), ov.get('year') or None
        else:
            t, y = parse_name(d.name)
        if not t or len(t) < 2:
            continue
        tl = t.lower()
        if tl in seen_titles:
            if y and not seen_titles[tl]['y']:
                old = seen_titles[tl]['key']
                entries = [e for e in entries if e['key'] != old]
                seen_keys.discard(old)
            else:
                continue
        k = _key(t, y, 'series')
        if k in seen_keys:
            continue
        seen_keys.add(k)
        seen_titles[tl] = {'key': k, 'y': y}
        try:
            added = d.stat().st_ctime
        except OSError:
            added = None
        entries.append({'t': t, 'y': y, 'kind': 'series', 'key': k,
                        'folder': str(d), 'added': added})
    return entries


