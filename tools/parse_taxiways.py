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


def _hav(a, b):
    import math
    R = 6371000
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(h))


def turn_ring(coords):
    """턴패드 검출: 폐곡(또는 15 m 이내 근접 폐곡)이고 둘레가 60 m 넘는 루프.
    OSM은 턴패드를 centerline 루프로 그리므로, 루프가 감싼 면을 포장으로 채운다."""
    n = len(coords)
    if n >= 4 and coords[0] == coords[-1]:
        return coords
    best = None
    for i in range(n - 4):
        for j in range(n - 1, i + 3, -1):
            if _hav(coords[i], coords[j]) < 15:
                path = sum(_hav(coords[k - 1], coords[k]) for k in range(i + 1, j + 1))
                if path > 60 and (best is None or j - i > best[1] - best[0]):
                    best = (i, j)
                break
    if best:
        ring = coords[best[0]:best[1] + 1]
        if ring[0] != ring[-1]:
            ring = ring + [ring[0]]
        return ring
    return None


def main():
    d = json.loads(SRC.read_text())
    feats = []
    seen = set()
    pads = 0
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
        ring = turn_ring(coords)
        if ring:
            pads += 1
            feats.append({"type": "Feature", "properties": {"pad": 1},
                          "geometry": {"type": "Polygon", "coordinates": [ring]}})
    fc = {"type": "FeatureCollection", "features": feats}
    DST.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
    print(f"taxiways: {len(feats)} features (turn pads {pads}) → {DST} ({DST.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
