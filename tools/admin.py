#!/usr/bin/env python3
"""행정경계(시도/시군구) → boundaries.geojson + 라벨 중심점 bnd_labels.geojson.

입력: raw/provinces.json, raw/municipalities.json
      (southkorea-maps kostat 2013 *_geo_simple.json — 이미 단순화된 WGS84)
출력 속성: level 1(시도) / 2(시군구), name(한글), name_eng
라벨 점은 가장 큰 폴리곤 ring의 꼭짓점 평균(면적 가중 근사)으로 놓는다.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
OUT = ROOT / "docs" / "data"


def rings(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    return geom["coordinates"]


def ring_area(ring):
    s = 0.0
    for i in range(len(ring) - 1):
        s += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    return abs(s) / 2


def label_point(geom):
    best = max((p[0] for p in rings(geom)), key=ring_area)
    n = len(best) - 1 or 1
    lon = sum(p[0] for p in best[:n]) / n
    lat = sum(p[1] for p in best[:n]) / n
    return [round(lon, 5), round(lat, 5)]


def rnd(geom, nd=5):
    def r(x):
        if isinstance(x, list):
            return [r(v) for v in x]
        return round(x, nd)
    return {"type": geom["type"], "coordinates": r(geom["coordinates"])}


def main():
    bnd, labels = [], []
    for fn, level in (("provinces.json", 1), ("municipalities.json", 2)):
        src = json.loads((RAW / fn).read_text())
        for f in src["features"]:
            p = f["properties"]
            props = {"level": level, "name": p["name"], "name_eng": p.get("name_eng", "")}
            bnd.append({"type": "Feature", "properties": props, "geometry": rnd(f["geometry"])})
            labels.append({
                "type": "Feature", "properties": props,
                "geometry": {"type": "Point", "coordinates": label_point(f["geometry"])},
            })
    (OUT / "boundaries.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": bnd}, ensure_ascii=False))
    (OUT / "bnd_labels.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": labels}, ensure_ascii=False))
    n1 = sum(1 for f in bnd if f["properties"]["level"] == 1)
    print(f"boundaries: {n1} 시도 + {len(bnd) - n1} 시군구, "
          f"{(OUT/'boundaries.geojson').stat().st_size} bytes; labels {len(labels)}")


if __name__ == "__main__":
    main()
