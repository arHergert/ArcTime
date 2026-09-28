# ArcTime

ArcTime ist eine lokale Mediathek für Filme, Serien und Spiele. Die App
durchsucht die eigenen Film- und Serienordner, reichert alles automatisch mit
Postern, Beschreibungen und Bewertungen an und startet Filme mit einem Klick
in VLC oder auf dem Fernseher. Dazu kommen ein Tagebuch im Stil von
Letterboxd, ein Jahresrückblick mit eigenen Awards und eine
Empfehlungs-Engine, die aus dem eigenen Geschmack lernt.

Alles läuft lokal: ein einzelner Python-Server ohne externe Pakete, das
Frontend ist statisches HTML/CSS/JS im Browser.

## Funktionen

**Bibliothek**
- Scannt die Ordner `Filme` und `Serien` und erkennt Titel, Jahr, Staffeln
  und Episoden aus den Dateinamen (inkl. Qualität wie 4K/HDR und
  eingebetteter Untertitel)
- Poster, Handlung, Laufzeit, Regie, Cast und Bewertungen über OMDb und TMDB,
  dauerhaft lokal gecacht
- Titelkorrektur und manuelle Cover-Auswahl, falls die automatische Erkennung
  danebenliegt
- Sortierung nach „Zuletzt geschaut“, „Zuletzt hinzugefügt“, A–Z, Jahr,
  Bewertung und Laufzeit, Genre-Filter, Suche in allen Tabs und eine
  Zufallsauswahl

**Wiedergabe**
- Startet Dateien in VLC im Vollbild, mit Englisch als bevorzugter Ton- und
  Untertitelspur
- Streamt per DLNA/UPnP direkt auf einen Fernseher im Netzwerk (z.B. LG)
- Episodenlisten pro Staffel mit Link zur jeweiligen Reddit-Diskussion

**Tagebuch & Rückblick**
- Einträge für Filme, Serienstaffeln und Spiele mit Sternen, Rewatch,
  Kommentar und Tags
- Import aus Letterboxd (CSV und automatischer RSS-Sync), Serializd und
  Backloggd; Export als CSV
- Spiele über IGDB inklusive Status (spiele gerade, abgebrochen, …)
- Jahresrückblick mit Statistiken, Diagrammen und eigenen Award-Kategorien
  mit Nominierungen und Gewinnern

**Empfehlungen**

Sechs Modi, alle aus dem eigenen Tagebuch berechnet:

| Modus | Idee |
|---|---|
| Aktueller Geschmack | Profil aus den zuletzt gesehenen/gespielten Titeln, exponentieller Zeitabfall (Halbwertszeit 90 Tage) |
| Allzeit-Geschmack | Profil aus dem gesamten Tagebuch |
| Weil du … mochtest | Empfehlungen zu den zuletzt gut bewerteten Titeln, gruppiert |
| Regie & Cast | Weitere Werke von Regisseuren, Serienschöpfern, Studios und Darstellern, die man mehrfach gut fand |
| Geheimtipps | Hoch bewertet, aber mit wenigen Stimmen |
| Überrasch mich | Geschmacksprofil + Diversitäts-Reranking (Maximal Marginal Relevance) |

Technisch: Der Kandidaten-Pool kommt aus den TMDB-Empfehlungen
(Collaborative Filtering über alle TMDB-Nutzer) und IGDB „similar games“ zu
jedem Tagebuch-Titel. Das Geschmacksprofil ist ein TF-IDF-Vektor aus Genres,
Keywords, Regie, Cast und Entwicklern, gewichtet mit der eigenen Bewertung
(zentriert um den persönlichen Schnitt). Jede Karte zeigt einen Match-Wert
und eine Begründung.

Auf jeder Karte gibt es drei Rückmeldungen, die den Titel dauerhaft
ausblenden und ins Profil einfließen:
- ✓ gute Empfehlung, angefangen (positives Signal)
- ½★ Thema passt, aber qualitativ nicht gut genug (leicht positiv, hebt die
  persönliche Qualitätsschwelle)
- ✗ nicht mein Geschmack (negatives Signal)

## Voraussetzungen

- Python 3.10 oder neuer
- [VLC](https://www.videolan.org/) für die Wiedergabe (optional, sonst
  Standard-App des Betriebssystems)
- [ffprobe](https://ffmpeg.org/) im PATH für die Untertitel-Erkennung
  (optional)
- Kostenlose API-Keys für [TMDB](https://www.themoviedb.org/settings/api),
  [OMDb](https://www.omdbapi.com/apikey.aspx) und
  [IGDB](https://api-docs.igdb.com/#getting-started) (über Twitch)

Keine `pip install`-Schritte nötig, das Projekt nutzt nur die
Python-Standardbibliothek.

## Setup

1. Repository klonen:
   ```
   git clone <repo-url> ArcTime/src
   ```
2. `.env.example` kopieren, z.B. nach `D:\Dokumente\Programmierung\arctime.env`
   (Grund für den Ort außerhalb des Repos: siehe [Konfiguration](#konfiguration)),
   und dort die API-Keys sowie den Pfad zu den Medienordnern eintragen.
3. Starten:
   ```
   python mediathek.py
   ```
   Öffnet `http://localhost:8765` im Standardbrowser.

Unter Windows startet `mediathek-starten.vbs` den Server ohne sichtbares
Konsolenfenster, `mediathek-neustart.bat` beendet einen laufenden Server und
startet ihn neu. Beim ersten Start trägt sich die App unter Windows in den
Autostart ein, der Server läuft dann ab der Anmeldung im Hintergrund.

## Konfiguration

Alles Maschinenspezifische (API-Keys, Medienpfad, Cache-Ort) liegt in einer
`.env`-Datei außerhalb des Repos, damit keine Zugangsdaten oder persönlichen
Pfade eingecheckt werden. Gesucht wird in dieser Reihenfolge:

1. Pfad aus der Umgebungsvariable `ARCTIME_ENV_FILE`
2. `.env` direkt in diesem Ordner (per `.gitignore` ausgeschlossen)
3. `D:\Dokumente\Programmierung\arctime.env`

Echte Umgebungsvariablen haben Vorrang vor der Datei. Alle Variablen stehen in
`.env.example`.

**Medienordner:** `ARCTIME_MEDIA_DIR` zeigt auf den Ordner mit den
Unterordnern `Filme` und `Serien`.

**Cache:** Datenbank-Dateien (`cache.json`, `diary.json`, `recengine.json`, …)
und heruntergeladene Bilder liegen standardmäßig eine Ebene über diesem
Ordner in `cache/`. Liegt der Code in `ArcTime/src`, landet der Cache in
`ArcTime/cache` und ist damit automatisch nicht Teil des Repos. Über
`ARCTIME_CACHE_DIR` frei änderbar.

## Projektstruktur

```
mediathek.py           Einstiegspunkt: Startup, Hintergrund-Jobs, Server
at_config.py           Pfade, .env-Laden, Konstanten, Dateinamen-Parsing
at_store.py            JSON-Persistenz (Cache, Tagebuch, Awards, Konfiguration)
at_scan.py             Dateisystem-Scanner für Filme und Serien
at_metadata.py         TMDB/OMDb/IGDB-Anbindung, Spiele-Registry, Cover
at_recommendations.py  Empfehlungs-Engine (Profil, Modi, Feedback, Backfill)
at_import.py           Letterboxd/Serializd/Backloggd-Import und -Sync
at_refresh.py          Scan + Anreicherung der Bibliothek
at_playback.py         VLC-Start, DLNA/UPnP-Cast
at_server.py           HTTP-Routing und HTML-Grundgerüst
static/                Frontend (app.js, app.css)
fonts/                 Inter (SIL Open Font License)
test_arctime.py        Regressionstests (pytest)
```

## Tests

```
pip install pytest
pytest test_arctime.py -v
```

## Datenquellen

Film- und Seriendaten stammen von [TMDB](https://www.themoviedb.org/) und
[OMDb](https://www.omdbapi.com/), Spieldaten von [IGDB](https://www.igdb.com/).
Dieses Projekt nutzt die TMDB-API, ist aber nicht von TMDB unterstützt oder
zertifiziert.

## Lizenz

[MIT](LICENSE). Die mitgelieferte Schrift Inter steht unter der SIL Open Font
License (siehe `fonts/LICENSE.txt`).
