#!/usr/bin/env python3
"""트랙 지상 정제 (오프라인판) — 앱 docs/groundmatch.js와 같은 파이프라인.

GPX(또는 track JSON 배열)를 받아 지상 구간을 유도로·활주로 centerline에
정합한 GPX를 쓴다. 공중 점은 절대 수정하지 않는다.

단계: 저속 스파이크 필터 → 정지 산란 붕괴(실이동 보존) → 스냅(이동 중 주기장
제외, hysteresis) → 같은 라인 우회 재스냅 → 오염 run 폐기 + 네트워크 경로 삽입.
근거: 2026-08-19 CJU→GMP 실측 검증 (공중 불변 assert, 지그재그 0).

실행: python3 tools/postprocess_track.py in.gpx [out.gpx]
"""
import json
import math
import re
import statistics
import sys
import heapq
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "docs" / "data"
R = 6371000


def hav(lon1, lat1, lon2, lat2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(h))


def load_track(path):
    text = Path(path).read_text()
    if path.endswith(".json"):
        return json.loads(text)
    pts = []
    for m in re.finditer(r'<trkpt lat="([\d.\-]+)" lon="([\d.\-]+)">(.*?)</trkpt>', text, re.S):
        lat, lon, body = float(m.group(1)), float(m.group(2)), m.group(3)
        ele = re.search(r"<ele>([\d.\-]+)</ele>", body)
        tm = re.search(r"<time>([^<]+)</time>", body)
        t = int(datetime.fromisoformat(tm.group(1).replace("Z", "+00:00")).timestamp() * 1000) if tm else None
        pts.append({"t": t, "lon": lon, "lat": lat,
                    "alt": float(ele.group(1)) if ele else None, "spd": None})
    # GPX에는 speed가 없다 — 연속점에서 유도
    for i in range(1, len(pts)):
        dt = (pts[i]["t"] - pts[i - 1]["t"]) / 1000
        if dt > 0:
            pts[i]["spd"] = hav(pts[i - 1]["lon"], pts[i - 1]["lat"], pts[i]["lon"], pts[i]["lat"]) / dt
    if pts:
        pts[0]["spd"] = pts[1]["spd"] if len(pts) > 1 else 0
    return pts


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else re.sub(r"(\.\w+)$", r"-matched.gpx", src_path)
    track = load_track(src_path)

    airports = [(f["properties"]["icao"], *f["geometry"]["coordinates"][:2])
                for f in json.loads((D / "airports.geojson").read_text())["features"]]
    twy = [f for f in json.loads((D / "taxiways.geojson").read_text())["features"]
           if f["geometry"]["type"] == "LineString"]
    rwy = json.loads((D / "runways.geojson").read_text())["features"]
    stands = [f for f in json.loads((D / "ground.geojson").read_text())["features"]
              if f["properties"]["k"] == "stand"]

    def nearest_airport(lon, lat):
        best = None
        for icao, alon, alat in airports:
            d = hav(lon, lat, alon, alat)
            if d < 6000 and (best is None or d < best[3]):
                best = (icao, alon, alat, d)
        return best

    nets = {}

    def build_net(apt):
        icao, alon, alat, _ = apt
        if icao in nets:
            return nets[icao]
        kx = 111320 * math.cos(math.radians(alat)); ky = 111320
        segs = []; nodes = []
        idx = [0]

        def in_r(cs):
            return any(hav(c[0], c[1], alon, alat) < 6000 for c in cs)

        def add_line(cs, lid, stand):
            for i in range(1, len(cs)):
                segs.append((lid, tuple(cs[i - 1]), tuple(cs[i]), stand))
            nodes.append(tuple(cs[0])); nodes.append(tuple(cs[-1]))

        for f in twy:
            cs = f["geometry"]["coordinates"]
            if not in_r(cs):
                continue
            add_line(cs, f["properties"].get("ref") or f"t{idx[0]}", False)
            idx[0] += 1
        for f in stands:
            cs = f["geometry"]["coordinates"]
            if not in_r(cs):
                continue
            add_line(cs, f"s{idx[0]}", True)
            idx[0] += 1
        for f in rwy:
            cs = f["geometry"]["coordinates"]
            if not in_r(cs):
                continue
            a, b = tuple(cs[0]), tuple(cs[-1])
            ax, ay = a[0] * kx, a[1] * ky; bx, by = b[0] * kx, b[1] * ky
            dx, dy = bx - ax, by - ay; L2 = dx * dx + dy * dy
            feet = [(0.0, a), (1.0, b)]
            for tn in nodes:
                px, py = tn[0] * kx, tn[1] * ky
                t = ((px - ax) * dx + (py - ay) * dy) / L2
                if t <= 0 or t >= 1:
                    continue
                fx, fy = ax + t * dx, ay + t * dy
                if math.hypot(px - fx, py - fy) < 25:
                    feet.append((t, (fx / kx, fy / ky)))
            feet.sort()
            rid = "r" + f["properties"].get("icao", "")
            for k in range(1, len(feet)):
                if feet[k][0] - feet[k - 1][0] > 1e-9:
                    segs.append((rid, feet[k - 1][1], feet[k][1], False))
            for t, fpt in feet[1:-1]:
                for tn in nodes:
                    if math.hypot((tn[0] - fpt[0]) * kx, (tn[1] - fpt[1]) * ky) < 25:
                        segs.append((rid + "-knit", tn, fpt, False))
        adj = {}

        def key(c):
            return (round(c[0], 6), round(c[1], 6))

        for lid, a, b, st in segs:
            w = hav(a[0], a[1], b[0], b[1])
            adj.setdefault(key(a), []).append((key(b), w))
            adj.setdefault(key(b), []).append((key(a), w))
        nets[icao] = (segs, adj, key, alat)
        return nets[icao]

    def project(lon, lat, a, b, latc):
        kx = 111320 * math.cos(math.radians(latc)); ky = 111320
        px, py = lon * kx, lat * ky
        ax, ay = a[0] * kx, a[1] * ky; bx, by = b[0] * kx, b[1] * ky
        dx, dy = bx - ax, by - ay; L2 = dx * dx + dy * dy
        t = 0 if L2 == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L2))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy)), (ax + t * dx) / kx, (ay + t * dy) / ky

    gs = lambda p: (p["spd"] or 0) * 1.9438

    def on_runway(apt, lon, lat):
        segs, adj, key, latc = build_net(apt)
        for lid, a, b, st in segs:
            if not lid.startswith("r"):
                continue
            d, sl, sp = project(lon, lat, a, b, latc)
            if d < 50:
                return True
        return False

    def ground_of(p):
        apt = nearest_airport(p["lon"], p["lat"])
        if not apt:
            return None
        if gs(p) < 175:
            return apt
        # 회전·부양·접지 활주: 고속이라도 활주로 라인 위면 아직 지상
        if gs(p) < 250 and on_runway(apt, p["lon"], p["lat"]):
            return apt
        return None

    # 1) 저속 스파이크 필터
    out = [track[0]]; spike = 0
    for q in track[1:]:
        last = out[-1]
        dt = (q["t"] - last["t"]) / 1000
        vrep = max(q["spd"] or 0, last["spd"] or 0)
        if 0 < dt < 30 and vrep < 15:
            d = hav(last["lon"], last["lat"], q["lon"], q["lat"])
            if d > vrep * dt + 25 + 12 and d / dt > 25:
                spike += 1
                if spike < 3:
                    continue
        spike = 0
        out.append(q)
    track = out

    ground = [ground_of(p) for p in track]

    # 2) 정지 산란 붕괴 (실이동 보존)
    t1 = []; i = 0
    while i < len(track):
        if ground[i] and gs(track[i]) < 5:
            j = i
            while j < len(track) and ground[j] and gs(track[j]) < 5:
                j += 1
            if track[j - 1]["t"] - track[i]["t"] >= 10000 and j - i >= 4:
                run = track[i:j]; h = len(run) // 2
                m1 = (statistics.median(q["lon"] for q in run[:h]), statistics.median(q["lat"] for q in run[:h]))
                m2 = (statistics.median(q["lon"] for q in run[h:]), statistics.median(q["lat"] for q in run[h:]))
                if hav(m1[0], m1[1], m2[0], m2[1]) < 25:
                    mlon = statistics.median(q["lon"] for q in run)
                    mlat = statistics.median(q["lat"] for q in run)
                    for q in (dict(run[0]), dict(run[-1])):
                        q["lon"], q["lat"] = mlon, mlat
                        t1.append(q)
                    i = j
                    continue
        t1.append(dict(track[i])); i += 1
    track = t1
    ground = [ground_of(p) for p in track]

    # 3) 스냅
    recs = []; prev_lid = None
    for i, p in enumerate(track):
        if not ground[i]:
            recs.append({"p": p, "flag": "air"}); prev_lid = None
            continue
        segs, adj, key, latc = build_net(ground[i])
        best = None
        for lid, a, b, stand in segs:
            if stand and gs(p) >= 8:
                continue
            d, sl, sp = project(p["lon"], p["lat"], a, b, latc)
            eff = d * (0.5 if lid == prev_lid else 1.0)
            if best is None or eff < best[0]:
                best = (eff, d, sl, sp, lid, (a, b))
        lim = 120 if gs(p) < 3 else 50
        if best and best[1] < lim:
            prev_lid = best[4]
            recs.append({"p": {**p, "lon": best[2], "lat": best[3]}, "flag": "snap",
                         "lid": best[4], "seg": best[5], "apt": ground[i]})
        else:
            prev_lid = None
            recs.append({"p": dict(p), "flag": "raw", "apt": ground[i]})

    # 4) 같은 라인 사이 우회 재스냅
    i = 0
    while i < len(recs):
        if recs[i]["flag"] == "snap":
            L = recs[i]["lid"]; j = i + 1
            while j < len(recs) and recs[j]["flag"] == "snap" and recs[j]["lid"] != L:
                j += 1
            if (j < len(recs) and recs[j].get("lid") == L and 1 <= j - i - 1 <= 16):
                segs, adj, key, latc = build_net(recs[i]["apt"])
                Ls = [(a, b) for lid, a, b, st in segs if lid == L]
                np_, ok = [], bool(Ls)
                for k in range(i + 1, j):
                    best = None
                    for a, b in Ls:
                        d, sl, sp = project(recs[k]["p"]["lon"], recs[k]["p"]["lat"], a, b, latc)
                        if best is None or d < best[0]:
                            best = (d, sl, sp, (a, b))
                    if best is None or best[0] > 45:
                        ok = False; break
                    np_.append((k, best))
                if ok and np_:
                    for k, b in np_:
                        recs[k]["p"]["lon"], recs[k]["p"]["lat"] = b[1], b[2]
                        recs[k]["lid"], recs[k]["seg"] = L, b[3]
                    i = j
                    continue
        i += 1

    # 5) 조립: 오염 run 폐기 + 네트워크 경로 삽입
    def net_path(apt, A, segA, B, segB):
        segs, adj, key, latc = build_net(apt)
        if segA == segB:
            return []
        dist = lambda c1, c2: hav(c1[0], c1[1], c2[0], c2[1])
        dvals = {key(segA[0]): dist(A, segA[0]), key(segA[1]): dist(A, segA[1])}
        prev = {}
        pq = [(w, k) for k, w in dvals.items()]
        heapq.heapify(pq)
        gA, gB = key(segB[0]), key(segB[1])
        seen = set()
        while pq:
            w, u = heapq.heappop(pq)
            if u in seen:
                continue
            seen.add(u)
            if gA in seen and gB in seen:
                break
            for v, ew in adj.get(u, []):
                nw = w + ew
                if nw < dvals.get(v, float("inf")):
                    dvals[v] = nw; prev[v] = u
                    heapq.heappush(pq, (nw, v))
        best = None
        for g in (gA, gB):
            if g in dvals:
                tot = dvals[g] + dist(g, B)
                if best is None or tot < best[0]:
                    best = (tot, g)
        if not best:
            return None
        straight = dist(A, B)
        if best[0] > max(2.5 * straight, straight + 400):
            return None
        chain = []; u = best[1]
        while u in prev:
            chain.append(u); u = prev[u]
        chain.append(u); chain.reverse()
        return chain

    final = []; prev_snap = None; pending = []
    for r in recs:
        if r["flag"] == "air":
            final += [x["p"] for x in pending]; pending = []
            final.append(r["p"]); prev_snap = None
            continue
        if r["flag"] == "raw":
            pending.append(r); continue
        if pending:
            if not (len(pending) <= 12 and prev_snap and prev_snap["apt"][0] == r["apt"][0]):
                final += [x["p"] for x in pending]
            pending = []
        if prev_snap and prev_snap["apt"][0] == r["apt"][0]:
            A = (prev_snap["p"]["lon"], prev_snap["p"]["lat"])
            B = (r["p"]["lon"], r["p"]["lat"])
            straight = hav(A[0], A[1], B[0], B[1])
            if straight > 25:
                path = net_path(r["apt"], A, prev_snap["seg"], B, r["seg"])
                if path:
                    t0, tt = prev_snap["p"]["t"], r["p"]["t"]
                    pts = [A] + path + [B]
                    cum = [0]
                    for k in range(1, len(pts)):
                        cum.append(cum[-1] + hav(pts[k - 1][0], pts[k - 1][1], pts[k][0], pts[k][1]))
                    for k in range(1, len(pts) - 1):
                        frac = cum[k] / cum[-1] if cum[-1] > 0 else 0
                        final.append({"t": int(t0 + (tt - t0) * frac), "lon": pts[k][0], "lat": pts[k][1],
                                      "alt": r["p"]["alt"], "spd": r["p"]["spd"]})
        final.append(r["p"]); prev_snap = r
    final += [x["p"] for x in pending]

    # 공중 불변 검증
    fmap = {}
    for p in final:
        fmap.setdefault(p["t"], p)
    for r in recs:
        if r["flag"] == "air" and r["p"]["t"] in fmap:
            assert fmap[r["p"]["t"]]["lon"] == r["p"]["lon"], "airborne modified!"

    def iso(t):
        return datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def trkpt(p):
        ele = f"<ele>{p['alt']:.1f}</ele>" if p.get("alt") is not None else ""
        return f'<trkpt lat="{p["lat"]:.6f}" lon="{p["lon"]:.6f}">{ele}<time>{iso(p["t"])}</time></trkpt>'

    segs2, cur = [], []
    for i, p in enumerate(final):
        if i and p["t"] - final[i - 1]["t"] > 120000 and cur:
            segs2.append(cur); cur = []
        cur.append(p)
    if cur:
        segs2.append(cur)
    body = "".join(f"<trkseg>{''.join(trkpt(p) for p in sg)}</trkseg>" for sg in segs2)
    name = Path(src_path).stem + " (matched)"
    gpx = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<gpx version="1.1" creator="AIRTRACK" xmlns="http://www.topografix.com/GPX/1/1">'
           f"<trk><name>{name}</name>{body}</trk></gpx>")
    Path(out_path).write_text(gpx)
    print(f"{len(track)} → {len(final)} pts → {out_path}")


if __name__ == "__main__":
    main()
