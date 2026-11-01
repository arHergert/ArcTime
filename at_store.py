"""ArcTime - Persistenz-Schicht: alle JSON-Flatfile-Caches (cache.json, diary.json,
title_overrides.json, awards.json, config.json, removed_entries.json, last_played.json)."""
import hashlib, html, json, os, re, shutil, socket, subprocess, sys, time, threading, urllib.parse, urllib.request, webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path

from at_config import CACHE, CFILE, CONFFILE, PLAYFILE, REMOVED_FILE, TOVERRIDE_FILE

# ── Cache ──────────────────────────────────────────────────────────────────────
def load_cache() -> dict:
    if CFILE.exists():
        try:
            return json.loads(CFILE.read_text('utf-8'))
        except Exception:
            pass
    return {}

def save_cache(c: dict):
    CFILE.write_text(json.dumps(c, ensure_ascii=False, indent=2), 'utf-8')


# ── Titel-Korrekturen (title_overrides.json) ───────────────────────────────────
# Manuelle Korrekturen für Dateien, deren Titel/Jahr falsch aus dem Dateinamen
# erkannt wurde (z.B. "Spartacus Remastered - US" -> "US"). Key ist der
# absolute Dateipfad, damit die Korrektur auch nach einem Umbenennen des
# geparsten Titels stabil zugeordnet bleibt. scan_filme/scan_serien wenden das
# hier hinterlegte Titel/Jahr an, bevor der Cache-Key gebildet wird.
_toverride_lock = threading.Lock()

def load_title_overrides() -> dict:
    if TOVERRIDE_FILE.exists():
        try:
            d = json.loads(TOVERRIDE_FILE.read_text('utf-8'))
            return d if isinstance(d, dict) else {}
        except Exception:
            pass
    return {}

def save_title_overrides(o: dict):
    TOVERRIDE_FILE.write_text(json.dumps(o, ensure_ascii=False, indent=2), 'utf-8')

def add_title_override(path: str, title: str, year: str, tmdb_id) -> dict:
    """Speichert eine Korrektur atomar (load+modify+save unter Lock) - ohne das
    würden mehrere kurz hintereinander gespeicherte Korrekturen sich gegenseitig
    überschreiben (lost update), da jede einzeln lädt/speichert."""
    with _toverride_lock:
        overrides = load_title_overrides()
        overrides[path] = {'title': title, 'year': year, 'tmdb_id': tmdb_id}
        save_title_overrides(overrides)
        return overrides


# ── Tagebuch (diary.json) ─────────────────────────────────────────────────────
DFILE = CACHE / "diary.json"
_diary_lock = threading.Lock()

def load_diary() -> list:
    if DFILE.exists():
        try:
            d = json.loads(DFILE.read_text('utf-8'))
            return d if isinstance(d, list) else []
        except Exception:
            pass
    return []

def save_diary(entries: list):
    DFILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2), 'utf-8')

def diary_csv(entries: list) -> str:
    """Letterboxd-kompatibler CSV-Export."""
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Title', 'Year', 'WatchedDate', 'Rating', 'Rewatch', 'Review',
                'Type', 'Season', 'Tags', 'Nominations'])
    for e in sorted(entries, key=lambda x: x.get('date', '')):
        w.writerow([
            e.get('title', ''), e.get('year', ''), e.get('date', ''),
            e.get('rating', ''), 'Yes' if e.get('rewatch') else '',
            (e.get('comment') or '').replace('\n', ' '),
            'Serie' if e.get('kind') == 'season' else 'Film',
            e.get('season', ''), e.get('tags', ''),
            ';'.join(e.get('noms') or []),
        ])
    return buf.getvalue()



# ── Entfernte Bibliothekseintraege (removed_entries.json) ──────────────────────
# Verschoben aus dem alten "Empfehlungen"-Abschnitt: gehoert inhaltlich zur
# allgemeinen Persistenz-Schicht, nicht zu den Empfehlungen.
def load_removed() -> dict:
    """Pfade (Datei/Ordner), die der Nutzer bewusst aus der Bibliothek entfernt
    hat - z.B. eine durchgeschaute Serie, deren Folgen bereits geloescht wurden,
    deren (jetzt leerer) Ordner aber noch existiert. Ohne diese Sperrliste wuerde
    refresh() den Titel beim naechsten Scan sofort wieder als "neu" anlegen."""
    if REMOVED_FILE.exists():
        try:
            d = json.loads(REMOVED_FILE.read_text('utf-8'))
            return d if isinstance(d, dict) else {}
        except Exception:
            pass
    return {}

def save_removed(d: dict):
    REMOVED_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), 'utf-8')

# ── Awards (awards.json) ──────────────────────────────────────────────────────
AFILE = CACHE / "awards.json"

def load_awards() -> dict:
    if AFILE.exists():
        try:
            d = json.loads(AFILE.read_text('utf-8'))
            return d if isinstance(d, dict) else {}
        except Exception:
            pass
    return {}

def save_awards(a: dict):
    AFILE.write_text(json.dumps(a, ensure_ascii=False, indent=2), 'utf-8')


# ── Top-10-Listen (top10.json): {"movie":[entryId,...], "season":[...], "game":[...]} ──
TOPFILE = CACHE / "top10.json"

def load_top() -> dict:
    if TOPFILE.exists():
        try:
            d = json.loads(TOPFILE.read_text('utf-8'))
            return d if isinstance(d, dict) else {}
        except Exception:
            pass
    return {}

def save_top(t: dict):
    TOPFILE.write_text(json.dumps(t, ensure_ascii=False, indent=2), 'utf-8')


# ── Konfiguration (Letterboxd-Sync) ───────────────────────────────────────────
def load_config() -> dict:
    if CONFFILE.exists():
        try:
            d = json.loads(CONFFILE.read_text('utf-8'))
            return d if isinstance(d, dict) else {}
        except Exception:
            pass
    return {}

def save_config(c: dict):
    CONFFILE.write_text(json.dumps(c, ensure_ascii=False, indent=2), 'utf-8')

def load_last_played() -> dict:
    if PLAYFILE.exists():
        try:
            d = json.loads(PLAYFILE.read_text('utf-8'))
            return d if isinstance(d, dict) else {}
        except Exception:
            pass
    return {}

def save_last_played(d: dict):
    PLAYFILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), 'utf-8')


