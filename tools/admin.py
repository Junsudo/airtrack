#!/usr/bin/env python3
"""KOSTAT TopoJSON(원본 해상도) → 행정경계 + 남한 육지 GeoJSON.

입력: raw/prov_topo.json, raw/muni_topo.json (southkorea-maps kostat 2013, full)
방법:
  - TopoJSON arc를 디코드한 뒤 arc 단위로 Douglas-Peucker 단순화.
    arc는 인접 구획이 공유하는 경계이므로 이렇게 하면 이웃끼리 절대 어긋나지
    않는다(topology 보존). 끝점은 DP가 항상 보존.
  - 남한 육지(land_sk)는 시도 폴리곤에서 사용 횟수가 1인 arc(=외곽선)를
    정수 좌표로 정확히 이어붙여 만든다 → 해안선과 행정경계가 좌표 단위로 일치.
출력: docs/data/boundaries.geojson, docs/data/bnd_labels.geojson,
      raw/land_sk.geojson (from_ato.py가 land.geojson 조립에 사용)
실행: python3 tools/admin.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
OUT = ROOT / "docs" / "data"

TOL = 0.0004  # DP 허용오차(도). 약 40m — z13에서도 사실상 무손실


def load_topo(path):
    t = json.loads(path.read_text())
    sx, sy = t["transform"]["scale"]
    tx, ty = t["transform"]["translate"]
    int_arcs, ll_arcs = [], []
    for arc in t["arcs"]:
        x = y = 0
        ints = []
        for dx, dy in arc:
            x += dx
            y += dy
            ints.append((x, y))
        int_arcs.append(ints)
        ll_arcs.append([(x * sx + tx, y * sy + ty) for x, y in ints])
    obj = next(iter(t["objects"].values()))
    return obj["geometries"], int_arcs, ll_arcs


def dp(pts, tol):
    """Douglas-Peucker (끝점 보존)."""
    if len(pts) < 3:
        return pts
    stack, keep = [(0, len(pts) - 1)], [False] * len(pts)
    keep[0] = keep[-1] = True
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        dmax, imax = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            if seg2 == 0:
                d2 = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / seg2
                t = 0 if t < 0 else 1 if t > 1 else t
                d2 = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d2 > dmax:
                dmax, imax = d2, i
        if dmax > tol * tol:
            keep[imax] = True
            stack.append((a, imax))
            stack.append((imax, b))
    return [p for p, k in zip(pts, keep) if k]


def ring_from_arcs(ring_spec, arcs):
    ring = []
    for idx in ring_spec:
        pts = arcs[~idx][::-1] if idx < 0 else arcs[idx]
        ring.extend(pts if not ring else pts[1:])
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def geom_rings(geom):
    if geom["type"] == "Polygon":
        return geom["arcs"]
    if geom["type"] == "MultiPolygon":
        return [r for poly in geom["arcs"] for r in poly]
    return []


def rnd_ring(ring):
    return [[round(x, 5), round(y, 5)] for x, y in ring]


def feature_from(geom, simp_arcs, props):
    if geom["type"] == "Polygon":
        coords = [rnd_ring(ring_from_arcs(r, simp_arcs)) for r in geom["arcs"]]
        gj = {"type": "Polygon", "coordinates": coords}
    else:
        coords = [[rnd_ring(ring_from_arcs(r, simp_arcs)) for r in poly] for poly in geom["arcs"]]
        gj = {"type": "MultiPolygon", "coordinates": coords}
    return {"type": "Feature", "properties": props, "geometry": gj}


def ring_area(ring):
    s = 0.0
    for i in range(len(ring) - 1):
        s += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    return abs(s) / 2


def label_point(feat):
    g = feat["geometry"]
    rings = [g["coordinates"][0]] if g["type"] == "Polygon" else [p[0] for p in g["coordinates"]]
    best = max(rings, key=ring_area)
    n = len(best) - 1 or 1
    return [round(sum(p[0] for p in best[:n]) / n, 5), round(sum(p[1] for p in best[:n]) / n, 5)]


def stitch_outline(geoms, int_arcs, simp_arcs):
    """사용횟수 1인 arc들을 정수 끝점 기준으로 이어붙여 외곽 ring들 생성."""
    from collections import defaultdict
    usage = defaultdict(int)
    for g in geoms:
        for ring in geom_rings(g):
            for idx in ring:
                usage[~idx if idx < 0 else idx] += 1
    border = [i for i, c in usage.items() if c == 1]
    ends = defaultdict(list)  # 정수 끝점 → [(arc_id, at_start?)]
    for i in border:
        ends[int_arcs[i][0]].append((i, True))
        ends[int_arcs[i][-1]].append((i, False))
    used, rings = set(), []
    for start in border:
        if start in used:
            continue
        used.add(start)
        pts = list(simp_arcs[start])
        head, tail = int_arcs[start][0], int_arcs[start][-1]
        while tail != head:
            nxt = next(((a, at_s) for a, at_s in ends[tail] if a not in used), None)
            if nxt is None:
                break  # 열린 사슬(데이터 결함) — 그대로 닫는다
            a, at_start = nxt
            used.add(a)
            seg_ll = simp_arcs[a] if at_start else simp_arcs[a][::-1]
            pts.extend(seg_ll[1:])
            tail = int_arcs[a][-1] if at_start else int_arcs[a][0]
        ring = rnd_ring(pts)
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        if len(ring) >= 4:
            rings.append(ring)
    return rings


def main():
    from collections import defaultdict

    # ---- 시군구 topology 하나로 경계선·해안선 모두 생성 (완전 일치 보장) ----
    geoms, int_arcs, ll_arcs = load_topo(RAW / "muni_topo.json")
    simp = [dp(a, TOL) for a in ll_arcs]

    usage = defaultdict(int)
    owner = defaultdict(set)  # arc → 인접 구획의 시도 코드(2자리)
    for g in geoms:
        pref = str(g.get("properties", {}).get("code", ""))[:2]
        for ring in geom_rings(g):
            for idx in ring:
                i = ~idx if idx < 0 else idx
                usage[i] += 1
                owner[i].add(pref)

    bnd = []
    for i, cnt in usage.items():
        if cnt != 2:
            continue  # 1 = 해안(land가 담당), 그 외는 데이터 결함
        level = 1 if len(owner[i]) == 2 else 2
        bnd.append({
            "type": "Feature", "properties": {"level": level},
            "geometry": {"type": "LineString", "coordinates": rnd_ring(simp[i])[:-1]
                         if simp[i][0] == simp[i][-1] else [[round(x, 5), round(y, 5)] for x, y in simp[i]]},
        })

    # 라벨: 시군구는 muni 폴리곤 중심, 시도는 prov 폴리곤 중심
    labels = []
    for g in geoms:
        p = g.get("properties", {})
        f = feature_from(g, simp, {})
        labels.append({"type": "Feature",
                       "properties": {"level": 2, "name": p.get("name", ""), "name_eng": p.get("name_eng", "")},
                       "geometry": {"type": "Point", "coordinates": label_point(f)}})
    pg, pia, pla = load_topo(RAW / "prov_topo.json")
    psimp = [dp(a, TOL) for a in pla]
    for g in pg:
        p = g.get("properties", {})
        f = feature_from(g, psimp, {})
        labels.append({"type": "Feature",
                       "properties": {"level": 1, "name": p.get("name", ""), "name_eng": p.get("name_eng", "")},
                       "geometry": {"type": "Point", "coordinates": label_point(f)}})

    # 남한 육지: 사용횟수 1 arc(해안+휴전선) 이어붙이기
    rings = stitch_outline(geoms, int_arcs, simp)
    rings = [r for r in rings if ring_area(r) > 1e-7]  # 극소 암초 정리
    rings.sort(key=ring_area, reverse=True)
    land_sk = {"type": "Feature", "properties": {"name": "South Korea"},
               "geometry": {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}}
    (RAW / "land_sk.geojson").write_text(json.dumps(land_sk))

    (OUT / "boundaries.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": bnd}, ensure_ascii=False))
    (OUT / "bnd_labels.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": labels}, ensure_ascii=False))
    n1 = sum(1 for f in bnd if f["properties"]["level"] == 1)
    print(f"boundaries(lines): 도경계 {n1} + 시군구 {len(bnd) - n1} arcs, "
          f"{(OUT/'boundaries.geojson').stat().st_size} bytes")
    print(f"labels: {len(labels)}, land_sk: {len(rings)} rings, "
          f"{(RAW/'land_sk.geojson').stat().st_size} bytes")


if __name__ == "__main__":
    main()
