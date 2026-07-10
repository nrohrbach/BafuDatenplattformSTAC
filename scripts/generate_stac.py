"""
BAFU Download → Statischer STAC-Katalog Generator
====================================================
Liest den S3-Bucket data.bafu.admin.ch/download/ und erstellt
einen statischen STAC-Katalog mit Collections und Items.

Gruppierungslogik:
  - Jeder Themenbereich (water, air, ...) wird zu einer Collection
  - Innerhalb einer Collection können Unterordner zu Items gruppiert werden
  - Alle Dateien in einem Item-Ordner werden zu Assets dieses Items
"""

import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import requests

# ─── Konfiguration ────────────────────────────────────────────────────────────

BASE_URL   = "https://data.bafu.admin.ch/download/"
OUTPUT_DIR = Path("docs")

NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

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

# ─── Gruppierungsregeln ───────────────────────────────────────────────────────
#
# Definiert wie Dateipfade zu STAC Items gruppiert werden.
# Format: prefix → Tiefe des Item-Ordners (gezählt ab 0)
#
# Beispiel für water/observations/live/data/1970/file.csv.gz:
#   Teile:  [water, observations, live, data, 1970, file.csv.gz]
#   Index:   0      1             2     3     4     5
#   item_depth=4 → Item-ID = "water/observations/live/data/1970/"
#
# Alles was NICHT in GROUPING_RULES steht → 1 Datei = 1 Item (Standard)

GROUPING_RULES = {
    # prefix                          item_depth  item_title_part
    "water/observations/live/data/": (4,          "Jahr"),
    # Hier können später weitere Regeln ergänzt werden:
    # "air/measurements/hourly/":    (3,          "Jahr"),
}

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "BAFU-STAC-Generator/1.0"


# ─── S3 Listing ───────────────────────────────────────────────────────────────

def s3_list(prefix: str = "") -> tuple[list[str], list[str]]:
    files, folders, token = [], [], None
    while True:
        params = {"list-type": "2", "delimiter": "/", "prefix": prefix}
        if token:
            params["continuation-token"] = token
        resp = SESSION.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for obj in root.findall("s3:Contents", NS):
            key = obj.findtext("s3:Key", namespaces=NS) or ""
            if not key.endswith("/"):
                files.append(key)
        for cp in root.findall("s3:CommonPrefixes", NS):
            p = cp.findtext("s3:Prefix", namespaces=NS) or ""
            folders.append(p)
        is_truncated = root.findtext("s3:IsTruncated", namespaces=NS) or ""
        if is_truncated.lower() == "true":
            token = root.findtext("s3:NextContinuationToken", namespaces=NS)
        else:
            break
    return files, folders


def crawl_all_files() -> list[str]:
    print("🔍 Crawle S3-Bucket ...")
    all_files, queue = [], [""]
    while queue:
        prefix = queue.pop(0)
        print(f"  📁 {prefix or '(root)'}")
        files, folders = s3_list(prefix)
        all_files.extend(files)
        queue.extend(folders)
        print(f"     → {len(files)} Dateien, {len(folders)} Unterordner")
    print(f"\n✅ Total: {len(all_files)} Dateien gefunden.")
    return all_files


# ─── Hilfsfunktionen ──────────────────────────────────────────────────────────

def make_id(*parts: str) -> str:
    raw  = "/".join(parts)
    safe = re.sub(r"[^a-zA-Z0-9\-_]", "-", raw).lower()
    safe = re.sub(r"-+", "-", safe).strip("-")
    return safe[:200]


def guess_media_type(key: str) -> str:
    # Doppelte Endung: .csv.gz → .gz
    suffix = Path(key).suffix.lower()
    return MEDIA_TYPES.get(suffix, "application/octet-stream")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_datetime(key: str) -> str | None:
    # Versionsdatum: v2026-03-17
    m = re.search(r"v(\d{4}-\d{2}-\d{2})", key)
    if m:
        return m.group(1) + "T00:00:00Z"
    # Jahreszahl: .../1970/...
    m = re.search(r"/(\d{4})/", key)
    if m:
        return m.group(1) + "-01-01T00:00:00Z"
    return None


def find_grouping_rule(key: str) -> tuple[str, int] | None:
    """Gibt (prefix, item_depth) zurück wenn eine Regel passt."""
    for prefix, (depth, _) in GROUPING_RULES.items():
        if key.startswith(prefix):
            return prefix, depth
    return None


# ─── Gruppierung ──────────────────────────────────────────────────────────────

def group_files(keys: list[str]) -> dict[str, list[str]]:
    """
    Gruppiert Dateipfade zu Items.
    Rückgabe: {item_folder_prefix: [key1, key2, ...]}
    
    Standard: jede Datei bekommt ihren eigenen Ordner als Key
    Mit Regel: alle Dateien in item_depth-Ordner werden zusammengefasst
    """
    groups: dict[str, list[str]] = defaultdict(list)

    for key in keys:
        rule = find_grouping_rule(key)
        if rule:
            prefix, depth = rule
            parts = key.split("/")
            # Item-Key = Pfad bis zur definierten Tiefe
            item_key = "/".join(parts[:depth + 1]) + "/"
        else:
            # Standard: Datei selbst ist das Item
            item_key = key

        groups[item_key].append(key)

    return dict(groups)


# ─── STAC Item ────────────────────────────────────────────────────────────────

def make_stac_item(item_key: str, asset_keys: list[str],
                   collection_id: str, generated_at: str) -> dict:
    """
    Erstellt ein STAC Item.
    item_key:   Ordnerpfad (z.B. "water/observations/live/data/1970/")
                oder Dateipfad (z.B. "water/groundwater/v2026-03-17/regions/file.geojson")
    asset_keys: Liste aller Dateien die zu diesem Item gehören
    """
    item_id = make_id(collection_id, item_key)
    dt      = extract_datetime(item_key) or generated_at

    # Titel: letzter nicht-leerer Teil des Pfades
    parts = [p for p in item_key.rstrip("/").split("/") if p]
    title = parts[-1] if parts else item_key

    # Assets: alle Dateien
    assets = {}
    for key in sorted(asset_keys):
        filename   = Path(key).name
        asset_id   = re.sub(r"[^a-zA-Z0-9\-_]", "-", filename).lower().strip("-")
        media_type = guess_media_type(key)
        assets[asset_id] = {
            "href":  BASE_URL + key,
            "type":  media_type,
            "title": filename,
            "roles": ["data"],
        }

    return {
        "type":         "Feature",
        "stac_version": "1.0.0",
        "id":           item_id,
        "collection":   collection_id,
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [5.9559, 45.8183], [10.4923, 45.8183],
                [10.4923, 47.8084], [5.9559, 47.8084],
                [5.9559, 45.8183],
            ]]
        },
        "bbox": [5.9559, 45.8183, 10.4923, 47.8084],
        "properties": {
            "title":    title,
            "datetime": dt,
            "created":  generated_at,
            "providers": [{
                "name":  "Bundesamt für Umwelt (BAFU)",
                "roles": ["producer", "licensor"],
                "url":   "https://www.bafu.admin.ch",
            }],
        },
        "links": [
            {"rel": "self",       "href": f"./items/{item_id}.json",        "type": "application/geo+json"},
            {"rel": "root",       "href": "../../catalog.json",             "type": "application/json"},
            {"rel": "parent",     "href": f"../collection.json",            "type": "application/json"},
            {"rel": "collection", "href": f"../collection.json",            "type": "application/json"},
        ],
        "assets": assets,
    }


# ─── STAC Collection ──────────────────────────────────────────────────────────

def make_stac_collection(collection_id: str, items: list[dict],
                         generated_at: str) -> dict:
    return {
        "type":         "Collection",
        "id":           collection_id,
        "stac_version": "1.0.0",
        "title":        collection_id.replace("/", " / ").replace("-", " ").title(),
        "description":  f"BAFU Open Data: {collection_id}",
        "license":      "proprietary",
        "extent": {
            "spatial":  {"bbox": [[5.9559, 45.8183, 10.4923, 47.8084]]},
            "temporal": {"interval": [["1970-01-01T00:00:00Z", None]]},
        },
        "links": [
            {"rel": "self",   "href": "./collection.json", "type": "application/json"},
            {"rel": "root",   "href": "../../catalog.json","type": "application/json"},
            {"rel": "parent", "href": "../../catalog.json","type": "application/json"},
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
        "bafu:item_count":   len(items),
    }


# ─── STAC Root Catalog ────────────────────────────────────────────────────────

def make_stac_catalog(collection_ids: list[str], generated_at: str) -> dict:
    return {
        "type":         "Catalog",
        "id":           "bafu-opendata",
        "stac_version": "1.0.0",
        "title":        "BAFU Open Data Downloads",
        "description":  (
            "Statischer STAC-Katalog der Open Data Downloads des "
            f"Bundesamts für Umwelt (BAFU). Quelle: {BASE_URL}"
        ),
        "links": [
            {"rel": "self", "href": "./catalog.json", "type": "application/json"},
            *[
                {
                    "rel":   "child",
                    "href":  f"./{cid}/collection.json",
                    "type":  "application/json",
                    "title": cid,
                }
                for cid in sorted(collection_ids)
            ],
        ],
        "bafu:generated_at":    generated_at,
        "bafu:collection_count": len(collection_ids),
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

    # 1. Alle S3-Keys holen (nur echte Dateien, keine assets/css etc.)
    all_keys = crawl_all_files()
    keys = [
        k for k in all_keys
        if not k.endswith("/")
        and not k.startswith("assets/")
        and "index.html" not in k
        and "error.html" not in k
    ]
    print(f"   (relevante Dateien: {len(keys)})")

    # 2. Dateien gruppieren → Items
    groups = group_files(keys)
    print(f"   (Items nach Gruppierung: {len(groups)})")

    # 3. Items nach Collection (= erster Pfadteil) sortieren
    #    z.B. "water/observations/..." → collection_id = "water/observations"
    collections: dict[str, list] = defaultdict(list)

    for item_key, asset_keys in groups.items():
        parts = item_key.split("/")
        # Collection = die ersten 2 Pfadteile (z.B. "water/observations")
        collection_id = "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
        collections[collection_id].append((item_key, asset_keys))

    # 4. STAC schreiben
    print(f"\n📚 Erstelle {len(collections)} Collections ...")

    for collection_id, item_list in sorted(collections.items()):
        print(f"  📂 {collection_id} ({len(item_list)} Items)")

        col_dir   = OUTPUT_DIR / collection_id
        items_dir = col_dir / "items"
        items     = []

        for item_key, asset_keys in item_list:
            item = make_stac_item(item_key, asset_keys, collection_id, generated_at)
            items.append(item)
            write_json(items_dir / f"{item['id']}.json", item)

        collection = make_stac_collection(collection_id, items, generated_at)
        write_json(col_dir / "collection.json", collection)

    # 5. Root Catalog
    print("\n📂 Erstelle Root catalog.json ...")
    catalog = make_stac_catalog(list(collections.keys()), generated_at)
    write_json(OUTPUT_DIR / "catalog.json", catalog)

    total_items = sum(len(v) for v in collections.values())
    print(f"\n✅ Fertig!")
    print(f"   Collections: {len(collections)}")
    print(f"   Items total: {total_items}")


if __name__ == "__main__":
    main()
