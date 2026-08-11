#!/usr/bin/env python3
"""ato-engine의 지도·공역 데이터 → airtrack GeoJSON.

- docs/data/land.geojson  ← ato-engine web/kto.geo.json (국가별 해안선 폴리곤)
- docs/data/areas.geojson ← atoengine.areas.AREAS + atoengine.airspace.p518_rings()

eAIP ENR 5.1 파싱본을 대체한다 (eAIP는 구역 경계를 서술형으로 적어 좌표 나열
파싱으로는 도형이 성립하지 않음 — ato-engine 쪽이 MDL/NLL 기반으로 닫아 둔
검증본이다). 실행: python3 tools/from_ato.py
"""
import json
import math
import sys
from pathlib import Path

ATO = Path("/Users/junsu/ato-engine")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data"

sys.path.insert(0, str(ATO))
from atoengine.areas import AREAS              # noqa: E402
from atoengine.airspace import p518_rings      # noqa: E402

NATION = {
    "SK": "South Korea", "NK": "North Korea", "CN": "China",
    "JP": "Japan", "RU": "Russia", "TW": "Taiwan",
}


def circle_poly(lon, lat, r_nm, n=48):
    r_deg = r_nm / 60.0
    pts = []
    for i in range(n + 1):
        a = 2 * math.pi * i / n
        pts.append([
            round(lon + r_deg * math.sin(a) / max(0.2, math.cos(math.radians(lat))), 5),
            round(lat + r_deg * math.cos(a), 5),
        ])
    return pts


def ring_from_pts(pts):
    """(lat, lon) 시퀀스 → 닫힌 [lon, lat] ring."""
    ring = [[round(lon, 5), round(lat, 5)] for lat, lon in pts]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def land():
    src = json.loads((ATO / "web" / "kto.geo.json").read_text())
    feats = []
    for code, polys in src.items():
        rings = []
        for poly in polys:
            ring = [[round(x, 5), round(y, 5)] for x, y in poly]
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            if len(ring) >= 4:
                rings.append([ring])
        feats.append({
            "type": "Feature",
            "properties": {"name": NATION.get(code, code)},
            "geometry": {"type": "MultiPolygon", "coordinates": rings},
        })
    return feats


def areas():
    feats = []
    for a in list(AREAS) + p518_rings():
        if a.get("kind") == "circle":
            ring = circle_poly(a["lon"], a["lat"], a["r_nm"])
        else:
            ring = ring_from_pts(a["pts"])
        if len(ring) < 4:
            continue
        aid = a["id"]
        short = aid[2:] if aid.startswith("RK") else aid
        feats.append({
            "type": "Feature",
            "properties": {"id": short, "cls": a["cls"], "name": a.get("name", short)},
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        })
    return feats


def main():
    lf = land()
    af = areas()
    (OUT / "land.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": lf}, ensure_ascii=False))
    (OUT / "areas.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": af}, ensure_ascii=False))
    print(f"land: {len(lf)} nations, {(OUT/'land.geojson').stat().st_size} bytes")
    from collections import Counter
    print(f"areas: {len(af)} zones {dict(Counter(f['properties']['cls'] for f in af))}, "
          f"{(OUT/'areas.geojson').stat().st_size} bytes")
    for f in af:
        if f["properties"]["id"].startswith("P518") or f["properties"]["id"] == "P73":
            cs = f["geometry"]["coordinates"][0]
            lons = [c[0] for c in cs]
            lats = [c[1] for c in cs]
            print(" ", f["properties"]["id"],
                  f"lon {min(lons):.2f}~{max(lons):.2f} lat {min(lats):.2f}~{max(lats):.2f} ({len(cs)}pts)")


if __name__ == "__main__":
    main()
