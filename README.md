# STAC Katalog für BAFU Datenplattform

Dieses Repository generiert automatisch einen statischen [STAC-Katalog](https://stacspec.org)
aus den Open Data Downloads des Bundesamts für Umwelt (BAFU).

**Quelle:** https://data.bafu.admin.ch/download/

**Katalog:** https://nrohrbach.github.io/BafuDatenplattformSTAC/catalog.json

**STAC-Browser** https://radiantearth.github.io/stac-browser/#/external/nrohrbach.github.io/BafuDatenplattformSTAC/catalog.json?.language=de

## Was ist STAC?

STAC (SpatioTemporal Asset Catalog) ist ein offener Standard, der es einfach macht,
Geodaten zu beschreiben, zu suchen und zu teilen. Viele GIS-Tools können STAC-Kataloge
direkt lesen (QGIS, GDAL, Python-stac, etc.).

## Struktur

```
BafuDatenplattformSTAC/
├── catalog/
│   ├── catalog.json        ← Einstiegspunkt des STAC-Katalogs
│   └── items/
│       ├── datensatz-1.json
│       ├── datensatz-2.json
│       └── ...
├── scripts/
│   └── generate_stac.py    ← Hauptskript
├── .github/workflows/
│   └── update-catalog.yml  ← Automatische wöchentliche Aktualisierung
└── requirements.txt
```

## Lokal ausführen

```bash
# 1. Repository klonen
git clone https://github.com/nrohrbach/BafuDatenplattformSTAC.git
cd BafuDatenplattformSTAC

# 2. Abhängigkeiten installieren
pip install -r requirements.txt

# 3. Skript ausführen
python scripts/generate_stac.py

# → Katalog wird in catalog/ erstellt
```

## GitHub Pages (optional)

Den Katalog öffentlich zugänglich machen:

1. Repository-Einstellungen → **Pages**
2. Source: **Deploy from a branch**
3. Branch: `main`, Ordner: `/catalog`
4. Dann ist der Katalog unter `https://nrohrbach.github.io/BafuDatenplattformSTAC/catalog.json` erreichbar

## Automatische Aktualisierung

GitHub Actions aktualisiert den Katalog jeden **Montag um 06:00 UTC** automatisch.
Manuell auslösen: Repository → **Actions** → **STAC Katalog aktualisieren** → **Run workflow**

## Datenquelle & Lizenz

Daten: © Bundesamt für Umwelt BAFU
Dieses Repository steht unter der MIT-Lizenz.
