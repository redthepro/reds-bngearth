#!/usr/bin/env python3
"""Resolve bounded OSM road samples; never connects to the game.

Default is offline replay of saved responses. --fetch permits at most one request
per real scenario per invocation (2 MiB response cap, 45 s socket timeout).
Overpass uses a 1 km radius; map API uses a 0.003 degree square (forest 0.015).
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://overpass-api.de/api/interpreter"
MAX_BYTES = 2 * 1024 * 1024
CLASSES = "primary|secondary|tertiary|unclassified|residential|living_street"


def map_xml(raw):
    root = ET.fromstring(raw)
    nodes = {int(n.attrib['id']): {"lat": float(n.attrib['lat']), "lon": float(n.attrib['lon'])}
             for n in root.findall('node')}
    ways = []
    for w in root.findall('way'):
        ids = [int(n.attrib['ref']) for n in w.findall('nd')]
        if any(n not in nodes for n in ids):
            continue
        ways.append({"type": "way", "id": int(w.attrib['id']),
                     "version": int(w.attrib['version']), "timestamp": w.attrib.get('timestamp'),
                     "nodes": ids, "geometry": [nodes[n] for n in ids],
                     "tags": {t.attrib['k']: t.attrib['v'] for t in w.findall('tag')}})
    return {"elements": ways, "osm3s": {"timestamp_osm_base": None}}


def distance(a, b):
    p, q = math.radians(a[0]), math.radians(b[0])
    dp, dl = q - p, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p) * math.cos(q) * math.sin(dl / 2) ** 2
    return 6371008.8 * 2 * math.asin(min(1, math.sqrt(h)))


def select_route(data, center):
    candidates = []
    for way in data.get("elements", []):
        tags = way.get("tags", {})
        if way.get("type") != "way" or tags.get("highway") not in CLASSES.split("|"):
            continue
        if any(tags.get(k) in {"no", "private", "agricultural", "forestry", "destination"}
               for k in ("access", "vehicle", "motor_vehicle", "motorcar")):
            continue
        if tags.get("area") == "yes" or tags.get("construction") or tags.get("proposed"):
            continue
        points = [(g["lat"], g["lon"]) for g in way.get("geometry", []) if "lat" in g and "lon" in g]
        if len(points) < 2 or len(points) != len(way.get("nodes", [])):
            continue
        # Keep only a contiguous <=1.2 km window near the center. Every edge is
        # from one OSM way, so no straight-line jumps between roads are invented.
        nearest = min(range(len(points)), key=lambda i: distance(center, points[i]))
        start = max(0, nearest - 1)
        end, length = start, 0.0
        while end + 1 < len(points):
            edge = distance(points[end], points[end + 1])
            if length + edge > 1200:
                break
            length += edge
            end += 1
        if length < 60:
            # A nearby end of a longer way: walk backward instead.
            end, start, length = nearest, nearest, 0.0
            while start > 0:
                edge = distance(points[start - 1], points[start])
                if length + edge > 1200:
                    break
                length += edge
                start -= 1
        if length < 60:
            continue
        route = points[start:end + 1]
        nodes = way["nodes"][start:end + 1]
        if tags.get("oneway") == "-1":
            route.reverse()
            nodes.reverse()
        near_m = min(distance(center, p) for p in route)
        candidates.append((near_m, -length, way["id"], way, route, nodes, length))
    if not candidates:
        raise ValueError("No eligible continuous road segment >=60 m in bounded response")
    near_m, _, _, way, points, nodes, length = min(candidates, key=lambda r: r[:3])
    return {"osm_way_id": way["id"], "osm_version": way.get("version"),
            "osm_timestamp": way.get("timestamp"), "tags": way.get("tags", {}),
            "node_ids": nodes, "coordinates_lat_lon": points, "length_m": round(length, 2),
            "nearest_vertex_to_center_m": round(near_m, 2),
            "direction": "OSM way order, reversed only for oneway=-1",
            "status": "source_resolved_engine_drivability_pending"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--provider", choices=("overpass", "osm-map"), default="overpass",
                    help="osm-map: API v0.6 GET, 0.003 degree square (forest: 0.015)")
    ap.add_argument("--output-root", type=Path, default=Path(os.environ["LOCALAPPDATA"]) /
                    "beamng-earth-2" / "benchmarks" / "01-baseline" / "routes")
    args = ap.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    scenarios = json.loads((ROOT / "config" / "benchmark-scenarios.json").read_text())
    manifest = {"schema_version": 1, "resolver_revision": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "attribution": "© OpenStreetMap contributors", "license": "ODbL-1.0",
                "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
                "copyright_url": "https://www.openstreetmap.org/copyright",
                "endpoint": ENDPOINT if args.provider == 'overpass' else 'https://api.openstreetmap.org/api/0.6/map',
                "provider": args.provider,
                "coordinate_reference": "WGS84 latitude/longitude degrees; no elevations supplied",
                "vehicle": {"selection": "Ibishu Covet, stock base manual (proposed)", "actual_config": None},
                "engine_protocol": {"speed_m_s": 10, "weather": "clear fixed noon (proposed)",
                    "resolution": None, "graphics": None, "game_version": None,
                    "status": "unrun; record actual vehicle/config/settings before comparisons"},
                "routes": []}
    for scenario in scenarios["scenarios"]:
        sid = scenario["id"]
        if scenario.get("synthetic"):
            manifest["routes"].append({"scenario_id": sid, "synthetic": True,
                "coordinates_lat_lon": [[0, 179.999], [0, -179.999]],
                "status": "coordinate seam fixture only; no road or engine route"})
            continue
        lat, lon = scenario["center_lat"], scenario["center_lon"]
        query = (f'[out:json][timeout:25][maxsize:16777216];way["highway"~"^({CLASSES})$"]'
                 f'(around:1000,{lat},{lon});out meta geom;')
        suffix = '.json' if args.provider == 'overpass' else '.osm'
        raw_path = args.output_root / f"{sid}{suffix}"
        query_path = args.output_root / f"{sid}.query.txt"
        item = {"scenario_id": sid, "scenario_center_lat_lon": [lat, lon],
                "query": query, "raw_path": str(raw_path),
                "category_coverage": "unverified; scenario label is not source or engine evidence"}
        if args.provider == 'osm-map':
            half = .0075 if sid == 'rural_forest' else .0015
            bbox = f'{lon - half},{lat - half},{lon + half},{lat + half}'
            item['query'] = 'GET https://api.openstreetmap.org/api/0.6/map?bbox=' + bbox
        query_digest = hashlib.sha256(item['query'].encode()).hexdigest()
        raw_path = args.output_root / f'{sid}-{query_digest[:12]}{suffix}'
        item.update(raw_path=str(raw_path), query_sha256=query_digest)
        query_path.write_text(item['query'] + "\n", encoding="utf-8")
        try:
            if not raw_path.exists():
                if not args.fetch:
                    raise FileNotFoundError("No saved response; use --fetch for bounded source acquisition")
                if args.provider == 'osm-map':
                    req = urllib.request.Request(item['query'][4:],
                        headers={"User-Agent": "beamng-earth-2-local-baseline/1.0"})
                else:
                    req = urllib.request.Request(ENDPOINT,
                        data=urllib.parse.urlencode({"data": query}).encode(),
                        headers={"User-Agent": "beamng-earth-2-local-baseline/1.0",
                                 "Content-Type": "application/x-www-form-urlencoded"})
                with urllib.request.urlopen(req, timeout=45) as response:
                    raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError("Response exceeded 2 MiB cap")
                (json.loads(raw) if args.provider == 'overpass' else map_xml(raw))
                raw_path.write_bytes(raw)
            raw = raw_path.read_bytes()
            if len(raw) > MAX_BYTES:
                raise ValueError("Saved response exceeded cap")
            data = json.loads(raw) if args.provider == 'overpass' else map_xml(raw)
            if data.get("remark"):
                raise ValueError("Overpass reported partial/error response: " + data["remark"][:180])
            item.update(select_route(data, (lat, lon)))
            item.update(raw_sha256=hashlib.sha256(raw).hexdigest(), raw_bytes=len(raw),
                        osm_base_timestamp=data.get("osm3s", {}).get("timestamp_osm_base"),
                        saved_file_mtime_utc=dt.datetime.fromtimestamp(raw_path.stat().st_mtime, dt.timezone.utc).isoformat(),
                        acquisition_time_note="Local saved-file mtime; copied files may have later mtime. OSM element timestamps/version are retained separately.")
        except Exception as exc:
            item.update(status="unresolved", error=f"{type(exc).__name__}: {exc}")
        manifest["routes"].append(item)
        print(sid, item["status"], item.get("osm_way_id", ""), flush=True)
        # Save partial progress even if a later endpoint call fails.
        (args.output_root / "routes.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # The synthetic seam scenario is last and makes no HTTP request.
    (args.output_root / "routes.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return int(any(r["status"] == "unresolved" for r in manifest["routes"]))


if __name__ == "__main__":
    raise SystemExit(main())
