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
RAW = ROOT / "raw"
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
    sk_hi = RAW / "land_sk.geojson"  # tools/admin.py가 만든 고해상 남한 (행정경계와 좌표 일치)
    for code, polys in src.items():
        if code == "SK" and sk_hi.exists():
            feats.append(json.loads(sk_hi.read_text()))
            continue
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


# ato-engine korea.py의 K-사이트 → ICAO. 민항 공항(airports.geojson 기존 항목)과
# 겹치는 기지는 건너뛰고 순수 군기지만 추가한다.
K_TO_ICAO = {
    "K-1": "RKPK", "K-2": "RKTN", "K-8": "RKJK", "K-13": "RKSW",
    "K-16": "RKSM", "K-18": "RKNN", "K-46": "RKNW", "K-55": "RKSO",
    "K-57": "RKJJ", "K-59": "RKTU", "K-75": "RKTI", "K-77": "RKTP",
}
AB_RE = __import__("re").compile(
    r'AirBase\("(K-\d+)",\s*"([^"]+)",\s*\w+,\s*([\d.]+),\s*([\d.]+),\s*"(ROK|US)"')


def mil_airbases():
    src = (ATO / "atoengine" / "korea.py").read_text()
    out = []
    for m in AB_RE.finditer(src):
        k, name, lat, lon, _nation = m.groups()
        icao = K_TO_ICAO.get(k)
        if icao is None:
            continue
        out.append({"icao": icao, "name": name, "lat": float(lat), "lon": float(lon)})
    return out


def merge_airports():
    path = OUT / "airports.geojson"
    fc = json.loads(path.read_text())
    have = {f["properties"]["icao"] for f in fc["features"]}
    added = []
    for ab in mil_airbases():
        if ab["icao"] in have:
            continue
        have.add(ab["icao"])
        fc["features"].append({
            "type": "Feature",
            "properties": {"icao": ab["icao"], "name": ab["name"], "mil": True},
            "geometry": {"type": "Point", "coordinates": [ab["lon"], ab["lat"]]},
        })
        added.append(ab["icao"])
    path.write_text(json.dumps(fc, ensure_ascii=False))
    print(f"airports: +{len(added)} mil {added}, total {len(fc['features'])}")


def rivers():
    feats = []
    for r in json.loads((ATO / "web" / "kto.riverpoly.json").read_text()):
        ring = [[round(x, 5), round(y, 5)] for x, y in r["pts"]]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        feats.append({"type": "Feature", "properties": {"name": r.get("name", ""), "kind": "poly"},
                      "geometry": {"type": "Polygon", "coordinates": [ring]}})
    rv = json.loads((ATO / "web" / "kto.rivers.json").read_text())
    for r in rv.get("rivers", []):
        feats.append({"type": "Feature", "properties": {"name": r.get("name", ""), "kind": "line"},
                      "geometry": {"type": "LineString",
                                   "coordinates": [[round(x, 5), round(y, 5)] for x, y in r["pts"]]}})
    (OUT / "rivers.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": feats}, ensure_ascii=False))
    print(f"rivers: {len(feats)} features, {(OUT/'rivers.geojson').stat().st_size} bytes")


def main():
    lf = land()
    af = areas()
    merge_airports()
    rivers()
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
