#!/usr/bin/env python3
"""OSM aeroway=taxiway → taxiways.geojson.

- raw/osm_taxiways.json: Overpass로 airports.geojson 31곳 반경 5 km의
  way["aeroway"="taxiway"]를 out geom으로 받은 원본.
  재생성: tools 커밋 메시지/README의 쿼리 참조 (runways와 같은 방식).
- 심벌 전용이므로 좌표는 소수 5자리(≈1 m)로 반올림, ref 태그만 보존.
실행: python3 tools/parse_taxiways.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "raw" / "osm_taxiways.json"
DST = ROOT / "docs" / "data" / "taxiways.geojson"


def main():
    d = json.loads(SRC.read_text())
    feats = []
    seen = set()
    for el in d.get("elements", []):
        if el.get("type") != "way" or el["id"] in seen:
            continue
        seen.add(el["id"])
        geom = el.get("geometry") or []
        if len(geom) < 2:
            continue
        coords = [[round(p["lon"], 5), round(p["lat"], 5)] for p in geom]
        props = {}
        ref = (el.get("tags") or {}).get("ref")
        if ref:
            props["ref"] = ref
        feats.append({"type": "Feature", "properties": props,
                      "geometry": {"type": "LineString", "coordinates": coords}})
    fc = {"type": "FeatureCollection", "features": feats}
    DST.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
    print(f"taxiways: {len(feats)} ways → {DST} ({DST.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
