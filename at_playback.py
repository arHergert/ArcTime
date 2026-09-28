"""ArcTime - Wiedergabe: VLC-Start sowie DLNA/UPnP-Cast auf den LG-Fernseher."""
import hashlib, html, json, os, re, shutil, socket, subprocess, sys, time, threading, urllib.parse, urllib.request, webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path

from at_config import PORT, get_local_ip

# ── VLC / Abspielen ────────────────────────────────────────────────────────────
_vlc: str | None = None

def find_vlc() -> str | None:
    global _vlc
    if _vlc:
        return _vlc
    for p in [Path(r"C:\Program Files\VideoLAN\VLC\vlc.exe"),
               Path(r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe")]:
        if p.exists():
            _vlc = str(p)
            return _vlc
    import shutil
    if shutil.which('vlc'):
        _vlc = 'vlc'
    return _vlc


# Immer im Vollbild starten und Englisch als bevorzugte Ton-/Untertitelsprache
# erzwingen (sonst wählt VLC manchmal die erste im Container gefundene Spur,
# z.B. Japanisch bei Game of Thrones).
_VLC_ARGS = ['--fullscreen', '--audio-language=en,eng,english', '--sub-language=en,eng,english']


def play_file(path_str: str) -> dict:
    p = Path(path_str)
    if not p.exists():
        return {'ok': False, 'error': f'Datei nicht gefunden: {p.name}'}

    # Windows: erst VLC versuchen, dann os.startfile als Fallback
    if sys.platform == 'win32':
        vlc = find_vlc()
        if vlc:
            try:
                # Kein CREATE_NO_WINDOW: das kann dazu führen, dass VLCs Fenster
                # im Hintergrund/minimiert landet statt sichtbar im Vordergrund.
                subprocess.Popen(
                    [vlc, *_VLC_ARGS, str(p)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return {'ok': True}
            except Exception:
                pass
        # Fallback: Standard-App (z.B. VLC wenn als Standard gesetzt) - hier
        # können keine Zusatz-Optionen (Vollbild/Sprache) übergeben werden.
        try:
            os.startfile(str(p))
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # Linux/macOS
    vlc = find_vlc()
    if not vlc:
        return {'ok': False, 'error': 'VLC nicht gefunden'}
    try:
        subprocess.Popen([vlc, *_VLC_ARGS, str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


# ── "Auf Gerät wiedergeben" (DLNA/UPnP-Cast auf den LG-Fernseher) ─────────────
# Entspricht dem, was Windows Explorer im Rechtsklick-Menü "Wiedergabe auf
# Gerät" anbietet: das Zielgerät wird per SSDP im lokalen Netzwerk gesucht,
# die Datei wird über den eigenen HTTP-Server gestreamt (der Fernseher HOLT
# sich die Bytes selbst per HTTP-GET, wir "senden" ihm nur die URL), und per
# UPnP-AVTransport-Kommando (SetAVTransportURI + Play) gestartet. Läuft
# komplett mit Python-Bordmitteln (Sockets, urllib, XML) ohne externe
# Bibliotheken.
#
# Windows listet für denselben Fernseher oft ZWEI Einträge im "Wiedergabe auf
# Gerät"-Menü: den echten DLNA-Medienrenderer (AVTransport-Dienst, per SSDP
# auffindbar - das ist der hier gesuchte) und einen separaten Miracast-/
# Wireless-Display-Eintrag (nutzt WiFi-Direct statt SSDP/DLNA und taucht bei
# dieser Suche gar nicht erst auf). Die SSDP-Suche liefert also von sich aus
# schon nur den richtigen Eintrag.

_MIME_BY_EXT = {
    '.mkv': 'video/x-matroska', '.mp4': 'video/mp4', '.m4v': 'video/mp4',
    '.avi': 'video/x-msvideo', '.mov': 'video/quicktime', '.wmv': 'video/x-ms-wmv',
    '.iso': 'video/mp4',
}

_UPNP_DEVICE_NS = 'urn:schemas-upnp-org:device-1-0'
_cast_renderer_cache = {'renderer': None, 'ts': 0.0}


def _ssdp_discover(search_target: str = 'urn:schemas-upnp-org:service:AVTransport:1',
                    timeout: float = 3.0) -> list[str]:
    """SSDP-Multicast-Suche nach UPnP-Geräten, die den angegebenen Dienst
    anbieten. Gibt die LOCATION-URLs der Geräte-Beschreibungs-XML zurück (in
    Antwortreihenfolge), keine Netzwerkabhängigkeit außer UDP-Multicast im
    eigenen LAN."""
    msg = (
        'M-SEARCH * HTTP/1.1\r\n'
        'HOST: 239.255.255.250:1900\r\n'
        'MAN: "ssdp:discover"\r\n'
        'MX: 2\r\n'
        f'ST: {search_target}\r\n'
        '\r\n'
    ).encode('utf-8')
    locations, seen = [], set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)
    try:
        sock.sendto(msg, ('239.255.255.250', 1900))
        end = time.time() + timeout
        while True:
            remaining = end - time.time()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                data, _addr = sock.recvfrom(65507)
            except socket.timeout:
                break
            except OSError:
                break
            text = data.decode('utf-8', errors='ignore')
            m = re.search(r'(?im)^location:\s*(\S+)\s*$', text)
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                locations.append(m.group(1))
    except Exception:
        pass
    finally:
        sock.close()
    return locations


def _upnp_find_avtransport(root: ET.Element, base_url: str) -> dict | None:
    """Durchsucht die Geräte-Beschreibungs-XML (inkl. eingebetteter Geräte) nach
    einem Dienst vom Typ AVTransport und gibt Anzeigename + Steuer-URL zurück -
    unabhängig davon, ob der Dienst am Wurzelgerät oder einem eingebetteten
    Gerät hängt (bei manchen Fernsehern/Settop-Boxen üblich)."""
    dev_tag = f'{{{_UPNP_DEVICE_NS}}}device'
    name_tag = f'{{{_UPNP_DEVICE_NS}}}friendlyName'
    svclist_tag = f'{{{_UPNP_DEVICE_NS}}}serviceList'
    svc_tag = f'{{{_UPNP_DEVICE_NS}}}service'
    type_tag = f'{{{_UPNP_DEVICE_NS}}}serviceType'
    ctrl_tag = f'{{{_UPNP_DEVICE_NS}}}controlURL'
    for device in root.iter(dev_tag):
        svc_list = device.find(svclist_tag)
        if svc_list is None:
            continue
        for svc in svc_list.findall(svc_tag):
            st = (svc.findtext(type_tag) or '')
            if 'AVTransport' in st:
                cu = svc.findtext(ctrl_tag) or ''
                friendly = (device.findtext(name_tag) or '').strip()
                return {
                    'friendly_name': friendly,
                    'service_type': st,
                    'control_url': urllib.parse.urljoin(base_url, cu),
                }
    return None


def find_lg_renderer(force: bool = False) -> dict | None:
    """Sucht per SSDP das erste erreichbare UPnP-Gerät, dessen Anzeigename 'LG'
    enthält und einen AVTransport-Dienst anbietet. Ergebnis wird 5 Minuten
    gecacht, damit nicht bei jedem Klick erneut 2-3 Sekunden auf SSDP-Antworten
    gewartet werden muss."""
    now = time.time()
    if not force and _cast_renderer_cache['renderer'] and now - _cast_renderer_cache['ts'] < 300:
        return _cast_renderer_cache['renderer']
    for location in _ssdp_discover():
        try:
            with urllib.request.urlopen(location, timeout=5) as r:
                root = ET.fromstring(r.read())
        except Exception:
            continue
        info = _upnp_find_avtransport(root, location)
        if info and 'lg' in info['friendly_name'].lower():
            _cast_renderer_cache['renderer'] = info
            _cast_renderer_cache['ts'] = now
            return info
    return None


def _didl_lite(title: str, url: str, mime: str) -> str:
    """Minimale DIDL-Lite-Metadatenbeschreibung des Video-Items, wie sie
    UPnP-Medienrenderer für SetAVTransportURI erwarten."""
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="1" parentID="0" restricted="1">'
        f'<dc:title>{html.escape(title)}</dc:title>'
        '<upnp:class>object.item.videoItem</upnp:class>'
        f'<res protocolInfo="http-get:*:{mime}:*">{html.escape(url)}</res>'
        '</item></DIDL-Lite>'
    )


def _soap_action(control_url: str, service_type: str, action: str, args: dict) -> bool:
    args_xml = ''.join(f'<{k}>{html.escape(str(v))}</{k}>' for k, v in args.items())
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body>'
        f'<u:{action} xmlns:u="{service_type}">{args_xml}</u:{action}>'
        '</s:Body></s:Envelope>'
    ).encode('utf-8')
    req = urllib.request.Request(control_url, data=body, method='POST', headers={
        'Content-Type': 'text/xml; charset="utf-8"',
        'SOAPAction': f'"{service_type}#{action}"',
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def cast_to_lg(file_path: str, title: str) -> dict:
    """Startet die Wiedergabe der Datei auf dem LG-Fernseher (DLNA-Cast, siehe
    Modul-Kommentar oben). Gibt {'ok':True,'device':...} oder
    {'ok':False,'error':...} zurück."""
    p = Path(file_path)
    if not p.exists():
        return {'ok': False, 'error': 'Datei nicht gefunden'}
    renderer = find_lg_renderer()
    if not renderer:
        return {'ok': False, 'error': 'Kein LG-Fernseher im Netzwerk gefunden. '
                                       'Ist er eingeschaltet und im selben WLAN/Netzwerk?'}
    ip = get_local_ip()
    stream_url = f'http://{ip}:{PORT}/stream?path={urllib.parse.quote(str(p))}'
    mime = _MIME_BY_EXT.get(p.suffix.lower(), 'video/x-matroska')
    didl = _didl_lite(title or p.stem, stream_url, mime)
    ok = _soap_action(renderer['control_url'], renderer['service_type'], 'SetAVTransportURI', {
        'InstanceID': 0, 'CurrentURI': stream_url, 'CurrentURIMetaData': didl,
    })
    if not ok:
        return {'ok': False, 'error': f'{renderer["friendly_name"]} hat die Wiedergabe-URL nicht angenommen'}
    if not _soap_action(renderer['control_url'], renderer['service_type'], 'Play',
                         {'InstanceID': 0, 'Speed': 1}):
        return {'ok': False, 'error': f'URL wurde übernommen, aber Play auf {renderer["friendly_name"]} ist fehlgeschlagen'}
    return {'ok': True, 'device': renderer['friendly_name']}


