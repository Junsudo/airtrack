#!/usr/bin/env python3
"""KR eAIP ENR 섹션 → GeoJSON 변환기.

입력: raw/ENR-3.3.html (RNAV routes), raw/ENR-3.1.html (conventional routes),
      raw/ENR-4.1.html (navaids), raw/ENR-4.4.html (significant points),
      raw/ENR-5.1.html (prohibited/restricted/danger areas)
출력: web/data/airways.geojson, fixes.geojson, navaids.geojson,
      areas.geojson, airports.geojson

사용: python3 tools/parse_eaip.py
데이터 갱신 시 raw/ HTML만 새 AIRAC 패키지로 다시 받고 재실행하면 된다.
"""
import json
import math
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
OUT = ROOT / "docs" / "data"

COORD_RE = re.compile(r"(\d{6,7}(?:\.\d+)?)\s*([NS])\s+(\d{6,7}(?:\.\d+)?)\s*([EW])")
IDENT_RE = re.compile(r"\(([A-Z]{2,4})\)")
NAVAID_WORDS = ("VORTAC", "VOR/DME", "VOR", "DME", "NDB", "TACAN")


def dms_to_dec(raw: str, hemi: str) -> float:
    """'370540' → 37.0944..., '1270154' → 127.0316... (DDMMSS / DDDMMSS, 초 소수 허용)"""
    if "." in raw:
        head, frac = raw.split(".")
    else:
        head, frac = raw, "0"
    sec = int(head[-2:]) + float("0." + frac)
    minute = int(head[-4:-2])
    deg = int(head[:-4])
    val = deg + minute / 60 + sec / 3600
    if hemi in "SW":
        val = -val
    return round(val, 6)


def parse_coord(text: str):
    m = COORD_RE.search(text)
    if not m:
        return None
    lat = dms_to_dec(m.group(1), m.group(2))
    lon = dms_to_dec(m.group(3), m.group(4))
    return (lon, lat)


def clean_name(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    # "SONGTAN VORTAC VORTAC (SOT)" 같은 단어 중복 제거
    words = t.split(" ")
    dedup = []
    for w in words:
        if not dedup or dedup[-1] != w:
            dedup.append(w)
    return " ".join(dedup)


def parse_routes(path: Path):
    """ENR 3.x 공용: Route-designator 행 이후의 Table-row-type-2 행들을 fix로 수집."""
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    routes = []
    cur = None
    for tr in soup.find_all("tr"):
        cls = " ".join(tr.get("class") or [])
        desig_p = tr.find("p", class_=re.compile(r"Route-designator"))
        if desig_p is not None:
            desig = re.sub(r"\s+", "", desig_p.get_text())
            if not desig:
                continue
            rtype = ""
            sib = desig_p.find_next_sibling("p")
            if sib is not None:
                rtype = re.sub(r"[()\s]+", " ", sib.get_text()).strip()
            # 같은 designator가 페이지 나뉨으로 다시 나오면 이어붙임
            if cur is not None and cur["id"] == desig:
                continue
            existing = next((r for r in routes if r["id"] == desig), None)
            if existing is not None:
                cur = existing
                continue
            cur = {"id": desig, "type": rtype, "points": []}
            routes.append(cur)
            continue
        if "Table-row-type-2" in cls and cur is not None:
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue
            coord = parse_coord(tr.get_text(" "))
            if coord is None:
                continue
            name = clean_name(tds[1].get_text(" "))
            if not name or name in ("", " "):
                continue
            if cur["points"] and cur["points"][-1]["name"] == name:
                continue  # 페이지 경계 중복
            m = IDENT_RE.search(name)
            cur["points"].append({
                "name": name,
                "ident": m.group(1) if m else None,
                "navaid": any(w in name for w in NAVAID_WORDS),
                "coord": coord,
            })
    return [r for r in routes if len(r["points"]) >= 2]


def parse_navaids(path: Path):
    """ENR 4.1: 행마다 name/ID/freq/coord."""
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    out = []
    for tr in soup.find_all("tr"):
        text = tr.get_text(" ")
        coord = parse_coord(text)
        if coord is None:
            continue
        tds = [re.sub(r"\s+", " ", td.get_text(" ")).strip() for td in tr.find_all("td")]
        if len(tds) < 4:
            continue
        name = clean_name(tds[0])
        if not name:
            continue
        ident = None
        freq = None
        for cell in tds[1:]:
            if ident is None and re.fullmatch(r"[A-Z]{2,4}", cell):
                ident = cell
            fm = re.search(r"(\d{2,3}\.\d+)\s*MHz|(\d{3,4})\s*kHz|CH\s*\d+", cell)
            if freq is None and fm:
                freq = re.sub(r"\s+", " ", fm.group(0))
        ntype = next((w for w in NAVAID_WORDS if w in name or w in " ".join(tds[:2])), "NAVAID")
        out.append({"name": name, "ident": ident, "type": ntype, "freq": freq, "coord": coord})
    return out


def parse_sigpoints(path: Path):
    """ENR 4.4: 5-letter name-code + coord (+ 소속 route 목록)."""
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    out = {}
    for tr in soup.find_all("tr"):
        tds = [re.sub(r"\s+", " ", td.get_text(" ")).strip() for td in tr.find_all("td")]
        if len(tds) < 2:
            continue
        name = tds[0]
        if not re.fullmatch(r"[A-Z]{5}", name):
            continue
        coord = parse_coord(" ".join(tds))
        if coord is None:
            continue
        routes = []
        for cell in tds[1:]:
            routes += re.findall(r"\b[A-Z]{1,2}\d{2,3}\b", cell)
        out[name] = {"name": name, "coord": coord, "routes": sorted(set(routes))}
    return list(out.values())


def circle_poly(center, radius_nm, n=48):
    lon0, lat0 = center
    r_deg_lat = radius_nm / 60.0
    pts = []
    for i in range(n + 1):
        a = 2 * math.pi * i / n
        lat = lat0 + r_deg_lat * math.cos(a)
        lon = lon0 + r_deg_lat * math.sin(a) / max(0.2, math.cos(math.radians(lat0)))
        pts.append([round(lon, 5), round(lat, 5)])
    return pts


def parse_areas(path: Path):
    """ENR 5.1 best-effort: 구역 id/이름 + 좌표열 폴리곤, 원형은 근사 폴리곤."""
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    out = []
    for tr in soup.find_all("tr"):
        text = re.sub(r"\s+", " ", tr.get_text(" "))
        idm = re.search(r"\bRK\s*([PRD])\s*-?\s*(\d+[A-Z]?)\b", text)
        if not idm:
            continue
        coords = [
            (dms_to_dec(m.group(3), m.group(4)), dms_to_dec(m.group(1), m.group(2)))  # (lon, lat)
            for m in COORD_RE.finditer(text)
        ]
        aid = f"{idm.group(1)}{idm.group(2)}"
        namem = re.search(r"\(([^)]{2,40})\)", text)
        circm = re.search(r"radius\s+of?\s+([\d.]+)\s*NM", text, re.I)
        poly = None
        if circm and coords:
            poly = circle_poly(coords[0], float(circm.group(1)))
        elif len(coords) >= 3:
            poly = [[round(lon, 5), round(lat, 5)] for lon, lat in coords]
            if poly[0] != poly[-1]:
                poly.append(poly[0])
        if poly is None:
            continue
        out.append({
            "id": aid,
            "cls": idm.group(1),
            "name": namem.group(1) if namem else aid,
            "poly": poly,
        })
    # 같은 id 중복 제거(첫 번째 유지)
    seen, dedup = set(), []
    for a in out:
        if a["id"] in seen:
            continue
        seen.add(a["id"])
        dedup.append(a)
    return dedup


AIRPORTS = [
    # (icao, name, lat, lon) — 국내 민항 주요 공항
    ("RKSI", "INCHEON", 37.4692, 126.4505),
    ("RKSS", "GIMPO", 37.5583, 126.7906),
    ("RKPC", "JEJU", 33.5113, 126.4930),
    ("RKPK", "GIMHAE", 35.1795, 128.9382),
    ("RKTN", "DAEGU", 35.8941, 128.6589),
    ("RKTU", "CHEONGJU", 36.7166, 127.4991),
    ("RKJJ", "GWANGJU", 35.1264, 126.8109),
    ("RKJB", "MUAN", 34.9914, 126.3828),
    ("RKJY", "YEOSU", 34.8423, 127.6157),
    ("RKPU", "ULSAN", 35.5935, 129.3517),
    ("RKTH", "POHANG-GYEONGJU", 35.9878, 129.4202),
    ("RKPS", "SACHEON", 35.0886, 128.0704),
    ("RKNY", "YANGYANG", 38.0613, 128.6690),
    ("RKNW", "WONJU", 37.4381, 127.9600),
    ("RKJK", "GUNSAN", 35.9038, 126.6158),
]


def fc(features):
    return {"type": "FeatureCollection", "features": features}


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    routes = parse_routes(RAW / "ENR-3.3.html") + parse_routes(RAW / "ENR-3.1.html")
    navaids = parse_navaids(RAW / "ENR-4.1.html")
    sigpoints = parse_sigpoints(RAW / "ENR-4.4.html")
    areas = parse_areas(RAW / "ENR-5.1.html")

    # airways.geojson
    aw_feats = []
    for r in routes:
        aw_feats.append({
            "type": "Feature",
            "properties": {
                "id": r["id"],
                "type": r["type"],
                "fixes": [p["ident"] or p["name"] for p in r["points"]],
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [list(p["coord"]) for p in r["points"]],
            },
        })

    # fixes: route 상의 점 + ENR 4.4 보강 (route 소속 정보 merge)
    fix_map = {}
    for r in routes:
        for p in r["points"]:
            key = p["name"] if not p["ident"] else p["ident"]
            f = fix_map.setdefault(key, {
                "name": p["name"], "ident": p["ident"], "navaid": p["navaid"],
                "coord": p["coord"], "routes": set(),
            })
            f["routes"].add(r["id"])
    for sp in sigpoints:
        f = fix_map.setdefault(sp["name"], {
            "name": sp["name"], "ident": None, "navaid": False,
            "coord": sp["coord"], "routes": set(),
        })
        f["routes"].update(sp["routes"])
    fix_feats = []
    for key, f in fix_map.items():
        if f["navaid"]:
            continue  # navaid는 별도 레이어
        fix_feats.append({
            "type": "Feature",
            "properties": {
                "name": key if re.fullmatch(r"[A-Z]{5}", key) else f["name"],
                "routes": sorted(f["routes"]),
                "onRoute": bool(f["routes"]),
            },
            "geometry": {"type": "Point", "coordinates": list(f["coord"])},
        })

    nav_feats = [{
        "type": "Feature",
        "properties": {"name": n["name"], "ident": n["ident"], "ntype": n["type"], "freq": n["freq"]},
        "geometry": {"type": "Point", "coordinates": list(n["coord"])},
    } for n in navaids]

    area_feats = [{
        "type": "Feature",
        "properties": {"id": a["id"], "cls": a["cls"], "name": a["name"]},
        "geometry": {"type": "Polygon", "coordinates": [a["poly"]]},
    } for a in areas]

    apt_feats = [{
        "type": "Feature",
        "properties": {"icao": i, "name": n},
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    } for i, n, lat, lon in AIRPORTS]

    (OUT / "airways.geojson").write_text(json.dumps(fc(aw_feats), ensure_ascii=False))
    (OUT / "fixes.geojson").write_text(json.dumps(fc(fix_feats), ensure_ascii=False))
    (OUT / "navaids.geojson").write_text(json.dumps(fc(nav_feats), ensure_ascii=False))
    (OUT / "airports.geojson").write_text(json.dumps(fc(apt_feats), ensure_ascii=False))
    # areas.geojson과 land.geojson은 tools/from_ato.py가 ato-engine 데이터로 생성
    # (eAIP ENR 5.1은 경계가 서술형이라 좌표 나열 파싱으로는 도형이 안 나옴)
    _ = area_feats

    # ---- 검증 출력 ----
    print(f"routes: {len(aw_feats)}  fixes: {len(fix_feats)}  navaids: {len(nav_feats)}  areas: {len(area_feats)}")
    bad = []
    for feat in aw_feats:
        for lon, lat in feat["geometry"]["coordinates"]:
            if not (118 < lon < 140 and 25 < lat < 45):
                bad.append((feat["properties"]["id"], lon, lat))
    print("out-of-bbox coords:", bad[:5] or "none")
    for want in ("Y711", "Y722"):
        r = next((x for x in routes if x["id"] == want), None)
        if r:
            names = [p["ident"] or p["name"] for p in r["points"]]
            print(f"{want}: {len(names)} pts | {' > '.join(names[:6])} ... {' > '.join(names[-2:])}")
        else:
            print(f"{want}: MISSING!")


if __name__ == "__main__":
    sys.exit(main())
