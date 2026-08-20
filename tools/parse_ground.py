#!/usr/bin/env python3
"""OSM 공항 지상 시설 → ground.geojson (단일 파일, k 프로퍼티로 레이어 분기).

- raw/osm_ground.json: runway(포장)·apron·gate·parking_position (Overpass, 31곳 반경 5 km)
- raw/osm_buildings.json: terminal·hangar
- k: rwy(포장 라인) | apron(폴리곤) | bldg(폴리곤) | stand(주기장 리드인 라인)
     | gate(포인트, ref 라벨)
- eAIP 활주로 라인은 THR→THR(착륙거리 기준)이라 실제 포장보다 짧다 —
  k=rwy 포장 라인이 그 시각 공백(턴패드까지의 연장 구간)을 채운다.
실행: python3 tools/parse_ground.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DST = ROOT / "docs" / "data" / "ground.geojson"


def coords_of(el, close=False):
    cs = [[round(p["lon"], 5), round(p["lat"], 5)] for p in el.get("geometry") or []]
    if close and cs and cs[0] != cs[-1]:
        cs.append(cs[0])
    return cs


def main():
    feats = []
    seen = set()

    def add(el, k, geom_type, close=False, ref_ok=False):
        key = (el["type"], el["id"])
        if key in seen:
            return
        seen.add(key)
        props = {"k": k}
        ref = (el.get("tags") or {}).get("ref")
        if ref_ok and ref:
            props["ref"] = ref
        if geom_type == "Point":
            geom = {"type": "Point", "coordinates": [round(el["lon"], 5), round(el["lat"], 5)]}
        else:
            cs = coords_of(el, close)
            if len(cs) < (4 if geom_type == "Polygon" else 2):
                return
            geom = {"type": geom_type, "coordinates": [cs] if geom_type == "Polygon" else cs}
        feats.append({"type": "Feature", "properties": props, "geometry": geom})

    # 활주로 포장: OSM way를 그대로 쓰면 끝(턴패드 방향)에서 굽는다 —
    # eAIP THR 라인 축에 OSM 포장 정점을 투영해 곧게 편 선으로 만든다.
    # 길이(THR 밖 포장 포함)는 투영 범위로 유지된다.
    import math
    eaip = json.loads((ROOT / "docs" / "data" / "runways.geojson").read_text())["features"]
    osm_rwy_ways = []

    g = json.loads((ROOT / "raw" / "osm_ground.json").read_text())
    for el in g.get("elements", []):
        aw = (el.get("tags") or {}).get("aeroway")
        if el["type"] == "way" and aw == "runway":
            key = ("rwy-raw", el["id"])
            if key in seen:
                continue
            seen.add(key)
            osm_rwy_ways.append(coords_of(el))
            continue
        elif el["type"] == "way" and aw == "apron":
            add(el, "apron", "Polygon", close=True)
        elif el["type"] == "way" and aw == "parking_position":
            add(el, "stand", "LineString", ref_ok=True)
            # 라벨용 포인트: 리드인 라인 끝점
            if el.get("geometry") and (el.get("tags") or {}).get("ref"):
                p = el["geometry"][-1]
                feats.append({"type": "Feature",
                              "properties": {"k": "gate", "ref": el["tags"]["ref"]},
                              "geometry": {"type": "Point",
                                           "coordinates": [round(p["lon"], 5), round(p["lat"], 5)]}})
        elif el["type"] == "node" and aw in ("gate", "parking_position"):
            add(el, "gate", "Point", ref_ok=True)

    # 포장 직선화: eAIP 라인마다 80 m 이내 OSM 정점을 축에 투영해 범위 산출
    used = set()
    for f in eaip:
        a, bb = f["geometry"]["coordinates"][0], f["geometry"]["coordinates"][-1]
        latc = (a[1] + bb[1]) / 2
        kx = 111320 * math.cos(math.radians(latc)); ky = 111320
        ax, ay = a[0] * kx, a[1] * ky
        dx, dy = bb[0] * kx - ax, bb[1] * ky - ay
        L = math.hypot(dx, dy)
        ux, uy = dx / L, dy / L
        tmin, tmax = 0.0, L
        matched = False
        for wi, cs in enumerate(osm_rwy_ways):
            pts = [(c[0] * kx - ax, c[1] * ky - ay) for c in cs]
            lat_ok = all(abs(-uy * px + ux * py) < 80 for px, py in pts)
            near = any(-200 < (px * ux + py * uy) < L + 200 for px, py in pts)
            if lat_ok and near:
                matched = True
                used.add(wi)
                for px, py in pts:
                    t = px * ux + py * uy
                    tmin, tmax = min(tmin, t), max(tmax, t)
        if matched:
            cs = [[round((ax + tmin * ux) / kx, 5), round((ay + tmin * uy) / ky, 5)],
                  [round((ax + tmax * ux) / kx, 5), round((ay + tmax * uy) / ky, 5)]]
        else:
            cs = [list(a), list(bb)]
        feats.append({"type": "Feature", "properties": {"k": "rwy"},
                      "geometry": {"type": "LineString", "coordinates": cs}})
    # eAIP에 없는 OSM 활주로(군기지 일부)는 양끝 chord로 직선화해 유지
    for wi, cs in enumerate(osm_rwy_ways):
        if wi in used or len(cs) < 2:
            continue
        feats.append({"type": "Feature", "properties": {"k": "rwy"},
                      "geometry": {"type": "LineString", "coordinates": [cs[0], cs[-1]]}})

    b = json.loads((ROOT / "raw" / "osm_buildings.json").read_text())
    for el in b.get("elements", []):
        if el["type"] == "way":
            add(el, "bldg", "Polygon", close=True)

    # relation(multipolygon) 에이프런·터미널 — CJU 주 에이프런 등은 way가 아니라
    # relation으로 매핑돼 있다. outer 멤버 way들을 끝점 연결로 링에 이어 붙인다.
    rel_path = ROOT / "raw" / "osm_ground_rel.json"
    if rel_path.exists():
        rel = json.loads(rel_path.read_text())
        for el in rel.get("elements", []):
            if el["type"] != "relation":
                continue
            k = "apron" if (el.get("tags") or {}).get("aeroway") == "apron" else "bldg"
            chains = [[[round(p["lon"], 5), round(p["lat"], 5)] for p in m.get("geometry") or []]
                      for m in el.get("members", [])
                      if m.get("type") == "way" and m.get("role") in ("outer", "")]
            chains = [c for c in chains if len(c) >= 2]
            rings = []
            while chains:
                ring = chains.pop(0)
                grew = True
                while grew and ring[0] != ring[-1]:
                    grew = False
                    for i, c in enumerate(chains):
                        if c[0] == ring[-1]:
                            ring += c[1:]
                        elif c[-1] == ring[-1]:
                            ring += list(reversed(c))[1:]
                        elif c[-1] == ring[0]:
                            ring = c[:-1] + ring
                        elif c[0] == ring[0]:
                            ring = list(reversed(c))[:-1] + ring
                        else:
                            continue
                        chains.pop(i)
                        grew = True
                        break
                if ring[0] != ring[-1]:
                    ring.append(ring[0])
                if len(ring) >= 4:
                    rings.append(ring)
            for ring in rings:
                feats.append({"type": "Feature", "properties": {"k": k},
                              "geometry": {"type": "Polygon", "coordinates": [ring]}})

    fc = {"type": "FeatureCollection", "features": feats}
    DST.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
    from collections import Counter
    print(Counter(f["properties"]["k"] for f in feats))
    print(f"→ {DST} ({DST.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
