"""
BAFU Download → Statischer STAC-Katalog Generator
====================================================
Liest den S3-Bucket data.bafu.admin.ch/download/ rekursiv via
AWS S3 ListObjectsV2 (XML-API) und erstellt einen statischen STAC-Katalog.
"""

import json
import re
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
import requests

# ─── Konfiguration ────────────────────────────────────────────────────────────

BASE_URL   = "https://data.bafu.admin.ch/download/"
OUTPUT_DIR = Path("docs")

# S3 XML Namespace
NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

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
    ".parquet": "application/parquet",
    ".gz":      "application/gzip",
    ".tar":     "application/x-tar",
    ".7z":      "application/x-7z-compressed",
    ".txt":     "text/plain",
    ".html":    "text/html",
}

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "BAFU-STAC-Generator/1.0"

# ─── S3 Listing ───────────────────────────────────────────────────────────────

def s3_list(prefix: str = "") -> tuple[list[str], list[str]]:
    """
    Ruft eine Seite des S3 ListObjectsV2 auf.
    Gibt (Dateien, Unterordner) zurück.
    Paginiert automatisch via ContinuationToken.
    """
    files   = []
    folders = []
    token   = None

    while True:
        params = {
            "list-type": "2",
            "delimiter": "/",
            "prefix":    prefix,
        }
        if token:
            params["continuation-token"] = token

        resp = SESSION.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)

        # Dateien (Contents)
        for obj in root.findall("s3:Contents", NS):
            key = obj.findtext("s3:Key", namespaces=NS) or ""
            if not key.endswith("/"):          # Ordner-Platzhalter überspringen
                files.append(key)

        # Unterordner (CommonPrefixes)
        for cp in root.findall("s3:CommonPrefixes", NS):
            p = cp.findtext("s3:Prefix", namespaces=NS) or ""
            folders.append(p)

        # Nächste Seite?
        is_truncated = root.findtext("s3:IsTruncated", namespaces=NS) or ""
        if is_truncated.lower() == "true":
            token = root.findtext("s3:NextContinuationToken", namespaces=NS)
        else:
            break

    return files, folders


def crawl_all_files() -> list[str]:
    """
    Traversiert den S3-Bucket rekursiv und gibt alle Datei-Keys zurück.
    Nutzt BFS (Breitensuche) über die Ordnerstruktur.
    """
    print("🔍 Crawle S3-Bucket ...")
    all_files = []
    queue = [""]   # Start: Root-Prefix (leer)

    while queue:
        prefix = queue.pop(0)
        label  = prefix or "(root)"
        print(f"  📁 {label}")

        files, folders = s3_list(prefix)
        all_files.extend(files)
        queue.extend(folders)

        print(f"     → {len(files)} Dateien, {len(folders)} Unterordner")

    print(f"\n✅ Total: {len(all_files)} Dateien gefunden.")
    return all_files


# ─── Hilfsfunktionen ──────────────────────────────────────────────────────────

def make_id(key: str) -> str:
    """Erstellt eine sichere, einzigartige STAC-ID aus dem S3-Key."""
    safe = re.sub(r"[^a-zA-Z0-9\-_]", "-", key).lower()
    safe = re.sub(r"-+", "-", safe).strip("-")
    return safe[:200]


def guess_media_type(key: str) -> str:
    suffix = Path(key).suffix.lower()
    return MEDIA_TYPES.get(suffix, "application/octet-stream")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_datetime(key: str) -> str | None:
    """
    Versucht ein Datum aus dem S3-Key zu lesen.
    Beispiel: water/karst-groundwater/v2026-03-17/... → 2026-03-17
    """
    match = re.search(r"v(\d{4}-\d{2}-\d{2})", key)
    if match:
        return match.group(1) + "T00:00:00Z"
    match = re.search(r"(\d{4}-\d{2}-\d{2})", key)
    if match:
        return match.group(1) + "T00:00:00Z"
    return None


# ─── STAC-Generierung ─────────────────────────────────────────────────────────

def make_stac_item(key: str, generated_at: str) -> dict:
    file_url   = BASE_URL + key
    item_id    = make_id(key)
    media_type = guess_media_type(key)
    title      = Path(key).name
    dt         = extract_datetime(key) or generated_at

    # Ordnerstruktur als zusätzliche Metadaten
    parts = key.split("/")
    theme = parts[0] if len(parts) > 1 else "unknown"

    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": item_id,
        "geometry": {
            # Bounding Box Schweiz (WGS84) – als Fallback
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
            "title":       title,
            "datetime":    dt,
            "created":     generated_at,
            "bafu:key":    key,
            "bafu:theme":  theme,
            "providers": [{
                "name":  "Bundesamt für Umwelt (BAFU)",
                "roles": ["producer", "licensor"],
                "url":   "https://www.bafu.admin.ch",
            }],
        },
        "links": [
            {"rel": "self",   "href": f"./items/{item_id}.json", "type": "application/geo+json"},
            {"rel": "root",   "href": "../catalog.json",         "type": "application/json"},
            {"rel": "parent", "href": "../catalog.json",         "type": "application/json"},
        ],
        "assets": {
            "data": {
                "href":  file_url,
                "type":  media_type,
                "title": title,
                "roles": ["data"],
            }
        },
    }


def make_stac_catalog(items: list[dict], generated_at: str) -> dict:
    return {
        "type":          "Catalog",
        "id":            "bafu-opendata",
        "stac_version":  "1.0.0",
        "title":         "BAFU Open Data Downloads",
        "description":   (
            "Statischer STAC-Katalog der Open Data Downloads des "
            f"Bundesamts für Umwelt (BAFU). Quelle: {BASE_URL}"
        ),
        "links": [
            {"rel": "self", "href": "./catalog.json", "type": "application/json"},
            *[
                {
                    "rel":   "item",
                    "href":  f"./items/{item['id']}.json",
                    "type":  "application/geo+json",
                    "title": item["properties"]["title"],
                }
                for item in items
            ],
        ],
        "bafu:generated_at": generated_at,
        "bafu:source_url":   BASE_URL,
        "bafu:item_count":   len(items),
    }


# ─── Datei-Export ─────────────────────────────────────────────────────────────

def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─── Hauptprogramm ────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("BAFU Download → STAC Katalog Generator")
    print("=" * 60)

    generated_at = now_iso()

    # 1. Alle S3-Keys holen
    keys = crawl_all_files()

    # Ordner-Platzhalter entfernen (enden mit /)
    keys = [k for k in keys if not k.endswith("/")]
    print(f"   (Dateien total: {len(keys)})")

    if not keys:
        print("❌ Keine Dateien gefunden!")
        return

    # 2. STAC Items
    print(f"\n📦 Erstelle {len(keys)} STAC Items ...")
    items      = []
    items_dir  = OUTPUT_DIR / "items"

    for key in keys:
        item = make_stac_item(key, generated_at)
        items.append(item)
        write_json(items_dir / f"{item['id']}.json", item)

    # 3. Root Catalog
    print("📂 Erstelle catalog.json ...")
    catalog = make_stac_catalog(items, generated_at)
    write_json(OUTPUT_DIR / "catalog.json", catalog)

    print(f"\n✅ Fertig! {len(items)} Items in {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
