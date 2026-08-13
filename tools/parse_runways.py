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
ENTRY = re.compile(r"\b(\d{2}[LRC]?)(\s*\(Displaced\))?\s+(\d{1,3}\.\d{2})°?\s+([\d ]+)\s*[×x]\s*(\d+)")
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


def ato_runways():
    src = (ATO / "atoengine" / "korea.py").read_text()
    apt = {f["properties"]["icao"]: f["geometry"]["coordinates"]
           for f in json.loads((ROOT / "docs" / "data" / "airports.geojson").read_text())["features"]}
    feats = []
    for m in re.finditer(r'AirBase\("(K-\d+)"([^\n]{0,200})', src):
        k = m.group(1)
        dm_ = re.search(r"rwy_deg=(\d+)", m.group(2))
        if not dm_:
            continue
        deg = int(dm_.group(1))
        icao = ATO_RWY.get(k)
        if icao is None or icao not in apt:
            continue
        lon, lat = apt[icao]
        rad = math.radians(deg)
        half = NOMINAL_M / 2
        dlat = half * math.cos(rad) / 111320
        dlon = half * math.sin(rad) / (111320 * math.cos(math.radians(lat)))
        n = round(deg / 10) % 36 or 36
        feats.append({
            "type": "Feature",
            "properties": {"icao": icao, "rwy": f"{n:02d}/{(n + 18) % 36 or 36:02d}",
                           "len_m": NOMINAL_M, "wid_m": 45, "brg": deg, "src": "ato-approx"},
            "geometry": {"type": "LineString", "coordinates": [
                [round(lon - dlon, 6), round(lat - dlat, 6)],
                [round(lon + dlon, 6), round(lat + dlat, 6)],
            ]},
        })
    return feats


def main():
    feats = parse_eaip_runways() + ato_runways()
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
