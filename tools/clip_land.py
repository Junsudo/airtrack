#!/usr/bin/env python3
"""Natural Earth 10m countries → 한국 주변 bbox로 clip한 land.geojson 생성."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "raw" / "ne_countries.geojson"
DST = ROOT / "docs" / "data" / "land.geojson"

W, S, E, N = 120.0, 28.0, 136.0, 44.0


def clip_ring(ring):
    """Sutherland-Hodgman: bbox 4개 반평면으로 순차 클리핑."""
    def clip_edge(pts, inside, intersect):
        out = []
        n = len(pts)
        for i in range(n):
            cur, prv = pts[i], pts[i - 1]
            ci, pi = inside(cur), inside(prv)
            if ci:
                if not pi:
                    out.append(intersect(prv, cur))
                out.append(cur)
            elif pi:
                out.append(intersect(prv, cur))
        return out

    def ix_v(x):
        def f(a, b):
            t = (x - a[0]) / (b[0] - a[0])
            return [x, a[1] + t * (b[1] - a[1])]
        return f

    def ix_h(y):
        def f(a, b):
            t = (y - a[1]) / (b[1] - a[1])
            return [a[0] + t * (b[0] - a[0]), y]
        return f

    pts = ring
    for inside, ix in (
        (lambda p: p[0] >= W, ix_v(W)),
        (lambda p: p[0] <= E, ix_v(E)),
        (lambda p: p[1] >= S, ix_h(S)),
        (lambda p: p[1] <= N, ix_h(N)),
    ):
        pts = clip_edge(pts, inside, ix)
        if not pts:
            return None
    pts = [[round(x, 4), round(y, 4)] for x, y in pts]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts if len(pts) >= 4 else None


def main():
    src = json.loads(SRC.read_text())
    feats = []
    for f in src["features"]:
        geom = f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        out_polys = []
        for poly in polys:
            rings = [r for r in (clip_ring(ring) for ring in poly) if r]
            if rings:
                out_polys.append(rings)
        if not out_polys:
            continue
        name = f["properties"].get("NAME") or f["properties"].get("ADMIN") or ""
        feats.append({
            "type": "Feature",
            "properties": {"name": name},
            "geometry": {"type": "MultiPolygon", "coordinates": out_polys},
        })
    DST.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False))
    print(f"land.geojson: {len(feats)} countries, {DST.stat().st_size} bytes")
    print([f["properties"]["name"] for f in feats])


if __name__ == "__main__":
    main()
