"""
BAFU Download → Statischer STAC-Katalog Generator
====================================================
Dieses Skript liest die Dateiliste von data.bafu.admin.ch/download/
und erstellt daraus einen statischen STAC-Katalog (JSON-Dateien).

STAC = SpatioTemporal Asset Catalog (Standard für Geodaten-Kataloge)
Dokumentation: https://stacspec.org
"""

import json
import re
import hashlib
import os
import requests
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup


# ─── Konfiguration ───────────────────────────────────────────────────────────

BASE_URL = "https://data.bafu.admin.ch/download/"
OUTPUT_DIR = Path("catalog")

CATALOG_ID = "bafu-opendata"
CATALOG_TITLE = "BAFU Open Data Downloads"
CATALOG_DESCRIPTION = (
    "Statischer STAC-Katalog der Open Data Downloads des "
    "Bundesamts für Umwelt (BAFU). Automatisch generiert aus "
    f"{BASE_URL}"
)

# Dateiendungen → STAC Media Types
MEDIA_TYPES = {
    ".csv":     "text/csv",
    ".xlsx":    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls":     "application/vnd.ms-excel",
    ".geojson": "application/geo+json",
    ".gpkg":    "application/geopackage+sqlite3",
    ".shp":     "application/octet-stream",
    ".zip":     "application/zip",
    ".pdf":     "application/pdf",
    ".json":    "application/json",
    ".xml":     "application/xml",
    ".tif":     "image/tiff",
    ".tiff":    "image/tiff",
    ".nc":      "application/x-netcdf",
}


# ─── Hilfsfunktionen ─────────────────────────────────────────────────────────

def make_id(filename: str) -> str:
    """Erstellt eine sichere ID aus dem Dateinamen (ohne Sonderzeichen)."""
    stem = Path(filename).stem
    # Nur Buchstaben, Zahlen, Bindestriche erlaubt
    safe = re.sub(r"[^a-zA-Z0-9\-_]", "-", stem).lower()
    safe = re.sub(r"-+", "-", safe).strip("-")
    return safe or hashlib.md5(filename.encode()).hexdigest()[:8]


def guess_media_type(filename: str) -> str:
    """Gibt den MIME-Type basierend auf der Dateiendung zurück."""
    suffix = Path(filename).suffix.lower()
    return MEDIA_TYPES.get(suffix, "application/octet-stream")


def now_iso() -> str:
    """Gibt die aktuelle Zeit als ISO 8601 String zurück."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── Scraping ────────────────────────────────────────────────────────────────

def fetch_file_list(url: str) -> list[dict]:
    """
    Liest die Dateiliste von der BAFU Download-Seite.
    
    Die Seite zeigt die Dateien als HTML-Tabelle oder Liste an.
    Wir lesen alle <a>-Links, die auf Dateien zeigen.
    """
    print(f"📥 Lade Dateiliste von {url} ...")
    
    headers = {
        "User-Agent": "BAFU-STAC-Generator/1.0 (github.com/DEIN-USERNAME/bafu-stac)"
    }
    
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, "html.parser")
    
    files = []
    seen = set()
    
    for link in soup.find_all("a", href=True):
        href = link["href"]
        
        # Nur Dateien (mit Endung), keine Verzeichnisse oder externe Links
        if not href or href.startswith("http") and BASE_URL not in href:
            continue
        
        # Absoluten URL bauen
        if href.startswith("/"):
            file_url = "https://data.bafu.admin.ch" + href
        elif href.startswith("http"):
            file_url = href
        else:
            file_url = url.rstrip("/") + "/" + href.lstrip("/")
        
        # Nur bekannte Dateiendungen
        filename = Path(href.split("?")[0]).name
        suffix = Path(filename).suffix.lower()
        
        if not suffix or suffix not in MEDIA_TYPES:
            continue
        
        if file_url in seen:
            continue
        seen.add(file_url)
        
        # Link-Text als Titel verwenden (falls vorhanden)
        title = link.get_text(strip=True) or filename
        
        files.append({
            "filename": filename,
            "url": file_url,
            "title": title,
        })
    
    print(f"✅ {len(files)} Dateien gefunden.")
    return files


# ─── STAC-Generierung ────────────────────────────────────────────────────────

def make_stac_item(file_info: dict, generated_at: str) -> dict:
    """
    Erstellt ein einzelnes STAC Item (= eine Datei im Katalog).
    
    STAC Items beschreiben einzelne Datensätze mit Metadaten.
    Da wir keine räumlichen Infos haben, verwenden wir die ganze Schweiz
    als Bounding Box.
    """
    item_id = make_id(file_info["filename"])
    media_type = guess_media_type(file_info["filename"])
    
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": item_id,
        "geometry": {
            # Bounding Box Schweiz (WGS84)
            "type": "Polygon",
            "coordinates": [[
                [5.9559, 45.8183],
                [10.4923, 45.8183],
                [10.4923, 47.8084],
                [5.9559, 47.8084],
                [5.9559, 45.8183],
            ]]
        },
        "bbox": [5.9559, 45.8183, 10.4923, 47.8084],
        "properties": {
            "title": file_info["title"],
            "description": f"BAFU Open Data Datei: {file_info['filename']}",
            "datetime": generated_at,  # Zeitpunkt der Kataloggenerierung
            "created": generated_at,
            "providers": [
                {
                    "name": "Bundesamt für Umwelt (BAFU)",
                    "roles": ["producer", "licensor"],
                    "url": "https://www.bafu.admin.ch",
                }
            ],
        },
        "links": [
            {
                "rel": "self",
                "href": f"./items/{item_id}.json",
                "type": "application/geo+json",
            },
            {
                "rel": "root",
                "href": "../catalog.json",
                "type": "application/json",
            },
            {
                "rel": "parent",
                "href": "../catalog.json",
                "type": "application/json",
            },
        ],
        "assets": {
            "data": {
                "href": file_info["url"],
                "type": media_type,
                "title": file_info["title"],
                "roles": ["data"],
            }
        },
    }


def make_stac_catalog(items: list[dict], generated_at: str) -> dict:
    """
    Erstellt den STAC Root Catalog (Einstiegspunkt des Katalogs).
    """
    item_links = [
        {
            "rel": "item",
            "href": f"./items/{item['id']}.json",
            "type": "application/geo+json",
            "title": item["properties"]["title"],
        }
        for item in items
    ]
    
    return {
        "type": "Catalog",
        "id": CATALOG_ID,
        "stac_version": "1.0.0",
        "title": CATALOG_TITLE,
        "description": CATALOG_DESCRIPTION,
        "links": [
            {
                "rel": "self",
                "href": "./catalog.json",
                "type": "application/json",
            },
            *item_links,
        ],
        "conformsTo": [
            "https://api.stacspec.org/v1.0.0/core",
        ],
        "bafu:generated_at": generated_at,
        "bafu:source_url": BASE_URL,
    }


# ─── Datei-Export ────────────────────────────────────────────────────────────

def write_json(path: Path, data: dict) -> None:
    """Schreibt ein Dictionary als formatiertes JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  💾 {path}")


# ─── Hauptprogramm ───────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("BAFU Download → STAC Katalog Generator")
    print("=" * 60)
    
    generated_at = now_iso()
    
    # 1. Dateiliste laden
    files = fetch_file_list(BASE_URL)
    
    if not files:
        print("❌ Keine Dateien gefunden! Bitte Seite manuell prüfen.")
        return
    
    # 2. STAC Items erstellen
    print(f"\n📦 Erstelle {len(files)} STAC Items ...")
    items = []
    items_dir = OUTPUT_DIR / "items"
    
    for file_info in files:
        item = make_stac_item(file_info, generated_at)
        items.append(item)
        write_json(items_dir / f"{item['id']}.json", item)
    
    # 3. Root Catalog erstellen
    print(f"\n📂 Erstelle Root Catalog ...")
    catalog = make_stac_catalog(items, generated_at)
    write_json(OUTPUT_DIR / "catalog.json", catalog)
    
    # 4. Zusammenfassung
    print(f"\n✅ Fertig!")
    print(f"   Katalog:  {OUTPUT_DIR}/catalog.json")
    print(f"   Items:    {len(items)} Dateien in {items_dir}/")
    print(f"   Zeitpunkt: {generated_at}")


if __name__ == "__main__":
    main()
