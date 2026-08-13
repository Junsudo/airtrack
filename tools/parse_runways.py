#!/usr/bin/env python3
"""AD 2.12 → 활주로 심벌 runways.geojson.

- raw/ad/*.html의 RUNWAY PHYSICAL CHARACTERISTICS: 활주로 끝마다
  "14R 135.00° 3 200 × 60 ... THR좌표 반대끝좌표" 행 → LineString.
  같은 활주로가 양끝에서 두 번 나오므로 좌표쌍(무순서)으로 dedupe.
- AD 페이지가 없는 순수 군기지 5곳(RKSO/RKSW/RKTP/RKTI/RKNN)은 ato-engine
  korea.py의 rwy_deg로 근사 심벌(길이 2 743 m 고정, src='ato-approx').
실행: python3 tools/parse_runways.py
"""
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AD = ROOT / "raw" / "ad"
DST = ROOT / "docs" / "data" / "runways.geojson"
ATO = Path("/Users/junsu/ato-engine")

# 활주로 끝 항목: "14R 135.00° 3 200 × 60 ..." ("(Displaced)" 변형은 제외)
ENTRY = re.compile(r"\b(\d{2}[LRC]?)(\s*\(Displaced\))?\s+(\d{1,3}\.\d{2})\s*°?\s+([\d ]+)\s*[×x]\s*(\d+)")
COORD = re.compile(r"(\d{6}(?:\.\d+)?)N\s*(\d{7}(?:\.\d+)?)E")

# ato korea.py의 K-사이트 → (ICAO, 자방위 근사 rwy_deg)
ATO_RWY = {"K-13": "RKSW", "K-55": "RKSO", "K-77": "RKTP", "K-75": "RKTI", "K-18": "RKNN"}
NOMINAL_M = 2743  # 9 000 ft


def dms(raw):
    head, frac = (raw.split(".") + ["0"])[:2]
    sec = int(head[-2:]) + float("0." + frac)
    if len(head) == 6:
        return int(head[:2]) + int(head[2:4]) / 60 + sec / 3600
    return int(head[:3]) + int(head[3:5]) / 60 + sec / 3600


def recip(d):
    n = (int(d[:2]) + 18) % 36 or 36
    s = d[2:] if len(d) > 2 else ""
    s = {"L": "R", "R": "L", "C": "C"}.get(s, s)
    return f"{n:02d}{s}"


def parse_eaip_runways():
    """활주로 끝(designator)마다 첫 좌표 = THR. 왕복 끝의 THR끼리 이어 심벌 생성.
    RWY end 칸은 대부분 비어 있으므로 쓰지 않는다. RKJK는 치수가 ft."""
    feats = []
    for f in sorted(AD.glob("*.html")):
        icao = f.stem
        t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", f.read_text(encoding="utf-8", errors="replace")))
        i = t.find("RUNWAY PHYSICAL")
        if i < 0:
            continue
        j = t.find("2.13", i)
        sec = t[i:j if j > i else i + 6000]
        feet = "(FT)" in sec[:400].upper()
        marks = list(ENTRY.finditer(sec))
        ends = {}
        for k, m in enumerate(marks):
            if m.group(2):          # (Displaced) 변형 행
                continue
            window = sec[m.end(): marks[k + 1].start() if k + 1 < len(marks) else len(sec)]
            cm = COORD.search(window)
            if not cm:
                continue
            ln = int(m.group(4).replace(" ", ""))
            wd = int(m.group(5))
            if feet:
                ln, wd = round(ln * 0.3048), round(wd * 0.3048)
            desig = m.group(1)
            if desig not in ends:   # 같은 끝이 중복 수록되면 첫 행 유지
                ends[desig] = {
                    "coord": [round(dms(cm.group(2)), 6), round(dms(cm.group(1)), 6)],
                    "brg": float(m.group(3)), "len": ln, "wid": wd,
                }
        done = set()
        for desig, e in ends.items():
            r = recip(desig)
            if r not in ends or desig in done:
                continue
            done.add(desig)
            done.add(r)
            lo = min(desig, r, key=lambda d: int(d[:2]))
            feats.append({
                "type": "Feature",
                "properties": {"icao": icao, "rwy": f"{lo}/{recip(lo)}",
                               "len_m": e["len"], "wid_m": e["wid"],
                               "brg": ends[lo]["brg"], "src": "eaip"},
                "geometry": {"type": "LineString",
                             "coordinates": [ends[desig]["coord"], ends[r]["coord"]]},
            })
    return feats


# AD 페이지가 없는 군기지: OSM 실측 활주로 (raw/osm_runways.json — Overpass
# aeroway=runway around 각 기지. ato-engine이 덕산비행장에 OSM을 쓴 전례를 따름)
MIL_BASES = {
    "RKSW": (37.2390, 127.0070), "RKSO": (37.0906, 127.0297),
    "RKTP": (36.7044, 126.4861), "RKTI": (37.0303, 127.8858),
    "RKNN": (37.7539, 128.9450),
    # CTR 중심 좌표 기준 (ENR 2.1) — 심벌은 parse_ctr.py가 추가
    "RKTE": (36.5681, 127.5000), "RKTY": (36.6317, 128.3550),
    "RKPE": (35.1447, 128.6942), "RKJM": (34.7589, 126.3811),
    "RKSG": (36.9600, 127.0333), "RKND": (38.1442, 128.6028),
    "Icheon": (37.2011, 127.4719), "Nonsan": (36.2694, 127.1139),
}


# OSM 오태깅 제외: 수원 way/792520454는 ref=16/34인데 실제 축이 144.4°로
# 15/33 쌍과 동일(16/34라면 진방위 ~168°여야 함). ref가 자기 지오메트리와
# 모순되는 단일 출처 데이터라 활주로로 인정하지 않는다. 수원은 15L·15R 2본.
OSM_EXCLUDE = {792520454}


def osm_runways():
    path = ROOT / "raw" / "osm_runways.json"
    if not path.exists():
        print("  ! raw/osm_runways.json 없음 — 군기지 활주로 생략 (README의 Overpass 쿼리로 재생성)")
        return []
    feats = []
    for e in json.loads(path.read_text()).get("elements", []):
        if e.get("id") in OSM_EXCLUDE:
            continue
        g = e.get("geometry")
        if not g or len(g) < 2:
            continue
        coords = [[round(p["lon"], 6), round(p["lat"], 6)] for p in g]
        clat = sum(c[1] for c in coords) / len(coords)
        clon = sum(c[0] for c in coords) / len(coords)
        icao = min(MIL_BASES, key=lambda i: math.hypot(
            (MIL_BASES[i][1] - clon) * math.cos(math.radians(clat)), MIL_BASES[i][0] - clat))
        d_km = math.hypot((MIL_BASES[icao][1] - clon) * math.cos(math.radians(clat)),
                          MIL_BASES[icao][0] - clat) * 111.32
        if d_km > 4:
            continue
        a, b = coords[0], coords[-1]
        L = math.hypot((b[0] - a[0]) * math.cos(math.radians(a[1])) * 111320,
                       (b[1] - a[1]) * 111320)
        if L < 800:                     # 헬리패드·소형 스트립 제외
            continue
        brg = (math.degrees(math.atan2(
            (b[0] - a[0]) * math.cos(math.radians(a[1])), b[1] - a[1])) + 360) % 360
        ref = e.get("tags", {}).get("ref")
        if not ref:                     # ref 미태깅이면 자방위(편각 8°W 근사)로 계산
            n = round(((brg - 8) % 360) / 10) % 36 or 36
            ref = f"{n:02d}/{(n + 18) % 36 or 36:02d}"
        feats.append({
            "type": "Feature",
            "properties": {"icao": icao, "rwy": ref,
                           "len_m": round(L), "wid_m": 45, "brg": round(brg, 1), "src": "osm"},
            "geometry": {"type": "LineString", "coordinates": coords},
        })
    return feats


def main():
    feats = parse_eaip_runways() + osm_runways()
    DST.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False))
    print(f"runways: {len(feats)}, {DST.stat().st_size} bytes")
    from collections import Counter
    print("  공항별:", dict(Counter(f["properties"]["icao"] for f in feats)))
    for f in feats:
        p = f["properties"]
        if p["icao"] in ("RKSS", "RKSI", "RKPC") or p["src"] != "eaip":
            a, b = f["geometry"]["coordinates"]
            L = math.hypot((b[0]-a[0])*math.cos(math.radians(a[1]))*111320, (b[1]-a[1])*111320)
            print(f"  {p['icao']} {p['rwy']:<8} brg={p['brg']:<7} 공칭 {p['len_m']}m 실측 {L:.0f}m [{p['src']}]")


if __name__ == "__main__":
    main()
