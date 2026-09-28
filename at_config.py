"""ArcTime - Konfiguration, Pfade, Datei-Namensparsing (Qualitaet/Titel/Episoden), Autostart.
Fundament-Modul: haengt von keinem anderen at_*-Modul ab."""
import hashlib, html, json, os, re, shutil, socket, subprocess, sys, time, threading, urllib.parse, urllib.request, webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path

# ── Konfiguration ──────────────────────────────────────────────────────────────
# ArcTime trennt bewusst Code (dieser Ordner, git-fähig) von allem Maschinen-
# spezifischen (API-Keys, Medien-Pfad, Cache-Ort): das liegt in einer .env-Datei
# AUSSERHALB des Repos, damit sich dieser Ordner ohne Secrets z.B. auf GitHub
# hochladen liesse. Siehe README.md und .env.example fuer die Einrichtung.
BASE = Path(__file__).parent


def _find_env_file() -> Path | None:
    """Sucht die .env-Datei mit Zugangsdaten/Pfaden, in dieser Reihenfolge:
    1) ARCTIME_ENV_FILE (Umgebungsvariable) - fuer andere Rechner/CI/wer die
       Datei woanders ablegen moechte, ohne diesen Code aendern zu muessen.
    2) .env direkt neben mediathek.py - fuer wer Code und Config lieber
       zusammen haelt (via .gitignore ohnehin vom Repo ausgeschlossen).
    3) Der Standard-Ablageort auf diesem Rechner."""
    override = os.environ.get('ARCTIME_ENV_FILE')
    if override:
        p = Path(override)
        if p.exists():
            return p
    local = BASE / '.env'
    if local.exists():
        return local
    default = Path(r'D:\Dokumente\Programmierung\arctime.env')
    if default.exists():
        return default
    return None


def _load_env(path: Path | None) -> dict:
    if not path:
        return {}
    out = {}
    try:
        for line in path.read_text('utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


_ENV_FILE = _find_env_file()
_ENV = _load_env(_ENV_FILE)


def _env(key: str, default: str = '') -> str:
    # Echte Umgebungsvariable hat Vorrang vor der .env-Datei (Standardverhalten,
    # erlaubt z.B. temporaeres Ueberschreiben in einer Konsole/CI).
    return os.environ.get(key) or _ENV.get(key) or default


# Medien liegen bewusst NICHT im Programmordner (koennen z.B. auf einem
# anderen Laufwerk liegen und sollen unabhaengig vom Code-Ordner bleiben).
MEDIA_DIR = Path(_env('ARCTIME_MEDIA_DIR', r'D:\Downloads\jMedien'))
FILME  = MEDIA_DIR / "Filme"
SERIEN = MEDIA_DIR / "Serien"

# Cache: Standard liegt eine Ebene ueber dem Code (BASE.parent), also z.B.
# G:\Programme\ArcTime\cache, wenn der Code in G:\Programme\ArcTime\src liegt -
# automatisch ausserhalb eines spaeteren git-Repos in src/. Ueber
# ARCTIME_CACHE_DIR in der .env-Datei frei ueberschreibbar.
CACHE   = Path(_env('ARCTIME_CACHE_DIR') or (BASE.parent / "cache"))
POSTERS = CACHE / "posters"
PEOPLE  = CACHE / "people"
GCOVERS = CACHE / "game_covers"
STATIC  = BASE / "static"
CFILE   = CACHE / "cache.json"
CASTFILE = CACHE / "cast.json"
TVSFILE = CACHE / "tmdb_seasons.json"
CONFFILE = CACHE / "config.json"
GFILE   = CACHE / "games.json"
DCOVFILE = CACHE / "diary_covers.json"
PLAYFILE = CACHE / "last_played.json"
REC_ENGINE_FILE   = CACHE / "recengine.json"                  # TMDB/IGDB-Daten für die Empfehlungs-Engine
REC_FEEDBACK_FILE = CACHE / "recommendations_feedback.json"   # ✓/✗ auf Empfehlungskarten
REC_HIDDEN_FILE   = CACHE / "recommendations_hidden.json"     # Alt: ausgeblendete kuratierte Titel
TOVERRIDE_FILE = CACHE / "title_overrides.json"
REMOVED_FILE = CACHE / "removed_entries.json"
API_KEY  = _env('OMDB_API_KEY')
TMDB_KEY = _env('TMDB_API_KEY')
IGDB_CLIENT_ID     = _env('IGDB_CLIENT_ID')
IGDB_CLIENT_SECRET = _env('IGDB_CLIENT_SECRET')
PORT    = 8765

def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return 'localhost'

CACHE.mkdir(exist_ok=True)
POSTERS.mkdir(exist_ok=True)
PEOPLE.mkdir(exist_ok=True)
GCOVERS.mkdir(exist_ok=True)

VIDEO_EXT = {'.mkv', '.mp4', '.avi', '.iso', '.mov', '.m4v', '.wmv'}

NOISE = re.compile(
    r'\b(4K|UHD|2160p|1080[ip]|720p|480p|BluRay|BDRip|BRRip|WEB[\-.]?DL|WEBRip|'
    r'HDTV|REMUX|HDR|DoVi|HEVC|AVC|x265|x264|H\.26[45]|DTS(?:[\-.]HD(?:\.MA)?)?|'
    r'AAC\d?(?:\.\d)?|DD[\+P]?\d?\.?\d?|AC3|FLAC|TrueHD|Atmos|EAC3|LPCM|MA5?\.?1|'
    r'IMAX|Remaster(?:ed)?|Criterion|Directors?\.?Cut|PROPER|REPACK|LIMITED|SUBBED|'
    r'MULTi|EXTENDED|THEATRICAL|SDR|CC|AMZN|NF|HMAX|ATV|PTP|FLUX|RARBG|YTS|YIFY|'
    r'GRACE|BETTY|SPHD|KRATOS|CMRG|PHOEW|BYNDR)\b.*',
    re.IGNORECASE
)

# ── Qualitäts-Erkennung aus Dateinamen ─────────────────────────────────────────
def extract_quality(filename: str) -> dict:
    n = filename.upper()
    q = {}
    if re.search(r'2160P|4K\b|UHD\b', n):
        q['res'] = '4K'
    elif re.search(r'1080[IP]', n):
        q['res'] = 'FHD'
    elif re.search(r'720P', n):
        q['res'] = 'HD'

    if re.search(r'DOVI|DOLBY[\s.]?VISION', n):
        q['hdr'] = 'DV'
    elif re.search(r'\bHDR\b', n):
        q['hdr'] = 'HDR'
    return q


# ── Titel-Parser ───────────────────────────────────────────────────────────────
def parse_name(raw: str) -> tuple:
    s = raw.strip()
    for ext in VIDEO_EXT:
        if s.lower().endswith(ext):
            s = s[:-len(ext)]
    ym = re.search(r'(?<!\d)((?:19|20)\d{2})(?!\d)', s)
    year = ym.group(1) if ym else None
    if year:
        s = s[:s.find(year)]
    # Punkte in Dateinamen sind bei Scene-Releases immer Worttrenner (z.B.
    # "Der.Herr.der.Ringe.2001") und werden daher immer wie Leerzeichen
    # behandelt, sonst schlägt die OMDB-Titelsuche fehl.
    s = s.replace('.', ' ')
    s = s.replace('_', ' ')
    s = NOISE.sub('', s)
    s = re.sub(r'\s*[-–]\s*(US|UK|DE|FR)\s*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*S\d{2}.*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*Season\s*\d+.*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*\(\s*\d{4}\s*\)', '', s)
    s = re.sub(r'\s*\([^)]{0,40}\)\s*$', '', s)
    s = re.sub(r'\s*\[[^\]]*\]', '', s)
    s = re.sub(r'\s+', ' ', s).strip().strip('([ -–')
    return s, year



# ── Titel-Normalisierung (geteilt ueber viele Module) ──────────────────────────
def _norm_title(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())

# ── Episoden-Parser ────────────────────────────────────────────────────────────
def parse_episode(path: Path) -> dict:
    stem = path.stem
    m = re.search(r'[Ss](\d{1,2})\s*[Ee](\d{1,3})', stem)
    if m:
        season, episode = int(m.group(1)), int(m.group(2))
        rest = stem[m.end():]
        dash_m = re.match(r'\s*-+\s*(.+?)\s*(?:--|$)', rest)
        if dash_m:
            name = re.sub(
                r'\s*(?:--|1080|2160|720|BluRay|WEB|x265|x264|HEVC|AC3|DTS).*$',
                '', dash_m.group(1), flags=re.IGNORECASE
            ).strip()
        else:
            rest = rest.lstrip('.-_ ')
            name = NOISE.sub('', re.sub(r'[._]', ' ', rest)).strip()
        return {'season': season, 'episode': episode,
                'label': f"S{season:02d}E{episode:02d}",
                'name': name or f"Episode {episode}", 'path': str(path)}

    m = re.search(r'(\d{1,2})x(\d{1,3})', stem)
    if m:
        season, episode = int(m.group(1)), int(m.group(2))
        return {'season': season, 'episode': episode,
                'label': f"S{season:02d}E{episode:02d}",
                'name': f"Episode {episode}", 'path': str(path)}

    parent = path.parent.name
    sm = re.search(r'[Ss](?:eason\s*|tafel\s*)?(\d+)', parent, re.IGNORECASE)
    season = int(sm.group(1)) if sm else 0
    em = re.search(r'^(\d{1,3})', stem)
    episode = int(em.group(1)) if em else 0
    label = (f"S{season:02d}E{episode:02d}" if season and episode
             else (f"Ep{episode:02d}" if episode else "—"))
    clean = NOISE.sub('', re.sub(r'[._]', ' ', stem)).strip()
    return {'season': season, 'episode': episode, 'label': label,
            'name': clean or path.stem, 'path': str(path)}


def scan_episodes(folder_str: str) -> list:
    folder = Path(folder_str)
    if not folder.exists():
        return []
    eps = []
    for f in folder.rglob('*'):
        if f.suffix.lower() not in VIDEO_EXT:
            continue
        try:
            if f.stat().st_size < 20_000_000:
                continue
        except OSError:
            continue
        eps.append(parse_episode(f))
    eps.sort(key=lambda e: (e['season'], e['episode'], e['name']))
    return eps


# ── Autostart via Registry (kein Admin nötig) ─────────────────────────────────
def setup_autostart():
    if sys.platform != 'win32':
        return
    try:
        import winreg
        wscript = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'System32' / 'wscript.exe'
        vbs     = BASE / 'mediathek-starten.vbs'
        # "autostart"-Argument markiert den Login-gesteuerten Start (kein
        # Browser-Popup) - Doppelklick auf die .vbs von Hand läuft ohne dieses
        # Argument weiter wie gewohnt und öffnet sofort den Browser.
        value   = f'"{wscript}" "{vbs}" autostart'
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Run',
            0, winreg.KEY_SET_VALUE
        )
        # Migration vom alten Projektnamen "Mediathek" -> "ArcTime": alten
        # Eintrag entfernen, damit der Autostart nicht doppelt ausgelöst wird.
        try:
            winreg.DeleteValue(key, 'Mediathek')
        except FileNotFoundError:
            pass
        winreg.SetValueEx(key, 'ArcTime', 0, winreg.REG_SZ, value)
        winreg.CloseKey(key)
        print("  Autostart eingerichtet (startet bei Anmeldung, kein Fenster)")
    except Exception as e:
        print(f"  Autostart: {e}")


