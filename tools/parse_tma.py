#!/usr/bin/env python3
"""eAIP ENR 2.1 → TMA 섹터(tma.geojson) + 인천 FIR 경계(fir.geojson).

TMA: §1.3 표의 행마다 "T NN" 섹터 + DDMMSS 좌표열 + "FL 185 / 1 000 ft AGL" +
     Class. 같은 섹터 id가 두 TMA 표에 실리는 경우가 12개 있는데 이는 중복이
     아니라 같은 수평 경계의 수직 적층 볼륨이다(예: T02 = Seoul FL185/FL175 층
     + Wonju FL175/1000ft 층). 볼륨을 병합해 한 feature에 담고, vert는 전체
     span(최고 상단/최저 하단), cls는 합집합으로 요약한다.
FIR: §1.1의 DDMM 좌표열. "along the NLL and MDL to ..." 구간은 ato-engine의
     NLL_WEST·MDL 폴리라인으로 채운다 (P518과 같은 소스 → 지도상 일관).
실행: python3 tools/parse_tma.py
"""
import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "raw" / "ENR-2.1.html"
OUT = ROOT / "docs" / "data"

sys.path.insert(0, "/Users/junsu/ato-engine")
from atoengine.airspace import MDL, NLL_WEST  # noqa: E402  (lat, lon) 서→동

COORD6 = re.compile(r"(\d{6})\s*N\s*(\d{7})\s*E")            # DDMMSS
COORD4 = re.compile(r"(\d{4})\s*N\s*(\d{5})\s*E")            # DDMM (FIR)
SECTOR = re.compile(r"\bT ?(\d{2})\b")
NAME = re.compile(r"([A-Z][A-Za-z]+(?: [A-Z][a-z]+)?)\s+Terminal Control Area")
VERT = re.compile(
    r"(FL ?\d+|UNL|\d[\d ]*ft(?: AGL| AMSL)?)\s*/\s*"
    r"(FL ?\d+|\d[\d ]*ft(?: AGL| AMSL)?|SFC|GND)", re.I)
CLS = re.compile(r"Class\s+([A-G](?:\s*,\s*[A-G])*)")


def dms6(lat_raw, lon_raw):
    la = int(lat_raw[:2]) + int(lat_raw[2:4]) / 60 + int(lat_raw[4:6]) / 3600
    lo = int(lon_raw[:3]) + int(lon_raw[3:5]) / 60 + int(lon_raw[5:7]) / 3600
    return [round(lo, 5), round(la, 5)]


def dm4(lat_raw, lon_raw):
    la = int(lat_raw[:2]) + int(lat_raw[2:4]) / 60
    lo = int(lon_raw[:3]) + int(lon_raw[3:5]) / 60
    return [round(lo, 5), round(la, 5)]


def alt_ft(s):
    """상·하단 문자열 → ft 근사 (span 계산용)."""
    s = s.strip()
    if re.match(r"UNL", s, re.I):
        return 10 ** 9
    if re.match(r"SFC|GND", s, re.I):
        return 0
    m = re.match(r"FL ?(\d+)", s, re.I)
    if m:
        return int(m.group(1)) * 100
    m = re.match(r"([\d ]+)\s*ft", s, re.I)
    if m:
        return int(m.group(1).replace(" ", ""))
    return None


def merge_volumes(feat):
    """volumes 목록 → 요약 vert(전체 span)·cls(합집합)·tma(연결)."""
    vols = feat["properties"]["volumes"]
    ups, los = [], []
    for v in vols:
        if not v["vert"]:
            continue
        up, lo = [x.strip() for x in v["vert"].split("/", 1)]
        if alt_ft(up) is not None:
            ups.append((alt_ft(up), up))
        if alt_ft(lo) is not None:
            los.append((alt_ft(lo), lo))
    p = feat["properties"]
    p["vert"] = f"{max(ups)[1]} / {min(los)[1]}" if ups and los else (vols[0]["vert"] if vols else None)
    letters = sorted({c for v in vols if v["cls"] for c in v["cls"].split(",")})
    p["cls"] = ",".join(letters) or None
    p["tma"] = "·".join(dict.fromkeys(v["tma"] for v in vols if v["tma"]))


def parse_tma():
    soup = BeautifulSoup(SRC.read_text(encoding="utf-8", errors="replace"), "html.parser")
    sectors = {}   # id → feature (좌표 많은 쪽 유지)
    order = []
    cur_tma = None
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        c0 = re.sub(r"\s+", " ", tds[0].get_text(" "))
        if not SECTOR.search(c0) or not COORD6.search(c0.replace(" ", "")):
            continue
        nm = NAME.search(c0)
        if nm:
            cur_tma = nm.group(1).title()
        # 셀 안을 섹터 토큰 위치로 분할
        marks = list(SECTOR.finditer(c0))
        for k, m in enumerate(marks):
            seg = c0[m.end(): marks[k + 1].start() if k + 1 < len(marks) else len(c0)]
            coords = [dms6(a, b) for a, b in COORD6.findall(seg.replace(" ", ""))]
            if len(coords) < 3:
                continue
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            sid = "T" + m.group(1)
            vm = VERT.search(seg)
            km = CLS.search(seg)
            vol = {
                "tma": cur_tma,
                "vert": f"{vm.group(1)} / {vm.group(2)}".replace("  ", " ") if vm else None,
                "cls": km.group(1).replace(" ", "") if km else None,
            }
            old = sectors.get(sid)
            if old is None:
                sectors[sid] = {
                    "type": "Feature",
                    "properties": {"sector": sid, "volumes": [vol]},
                    "geometry": {"type": "Polygon", "coordinates": [coords]},
                }
                order.append(sid)
            else:
                # 수직 적층 볼륨 (수평 경계 동일 — 검증 완료). 기하는 유지, 볼륨만 추가.
                if not any(v["vert"] == vol["vert"] and v["tma"] == vol["tma"]
                           for v in old["properties"]["volumes"]):
                    old["properties"]["volumes"].append(vol)
    feats = [sectors[s] for s in order]
    for f in feats:
        merge_volumes(f)
    return feats


FREQLINE = re.compile(r"^\d{3}\.\d{1,3}(?:\s*,\s*\d{3}\.\d{1,3})*$")


def parse_acc_sectors(html):
    """§1.1의 관제기관별 섹터 주파수. eAIP 표는 '주파수 줄' 다음 '/ 섹터명' 줄이
    한 항목이다 (주파수가 '뒤에 오는' 섹터 소속 — HTML <p> 줄 구조로 확정)."""
    fir_i = html.find("Incheon FIR")
    fir_j = html.find("1.2", fir_i)
    seg = html[fir_i:fir_j if fir_j > fir_i else fir_i + 20000]
    lines = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()
             for m in re.finditer(r"<p[^>]*>(.*?)</p>", seg, re.S)]
    lines = [l for l in lines if l]
    units, cur = [], None
    k = 0
    while k < len(lines):
        l = lines[k]
        um = re.match(r"(Daegu ACC|Incheon ACC|Daegu FIC)$", l)
        if um:
            cur = {"unit": um.group(1), "callsign": None, "sectors": [], "common": None, "emerg": None}
            units.append(cur)
            k += 1
            continue
        if cur is not None:
            if cur["callsign"] is None and re.match(r"(Daegu|Incheon) (Control|Information)$", l):
                cur["callsign"] = l
            elif FREQLINE.match(l) and k + 1 < len(lines) and lines[k + 1].startswith("/"):
                name = lines[k + 1].lstrip("/ ").strip()
                freqs = [f.strip() for f in l.split(",")]
                if name.upper() == "EMERG":
                    cur["emerg"] = freqs
                else:
                    cur["sectors"].append({"name": re.sub(r"\s*Sector$", "", name), "freqs": freqs})
                k += 2
                continue
            elif l.startswith("COMMON"):
                cm = re.search(r":\s*([\d., ]+)$", l)
                if cm:
                    cur["common"] = [f.strip() for f in cm.group(1).split(",") if f.strip()]
        k += 1
    return [u for u in units if u["sectors"] or u["callsign"]]


def parse_fir():
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ",
                                      SRC.read_text(encoding="utf-8", errors="replace")))
    i = text.find("Incheon FIR")
    j = text.find("UNL", i)
    seg = text[i:j]
    pts = [dm4(a, b) for a, b in COORD4.findall(seg)]
    k = seg.find("along")
    # "along ..." 앞의 좌표 수 = 서쪽 시작점 개수
    n_before = len(COORD4.findall(seg[:k]))
    west, east = pts[:n_before], pts[n_before:]
    join_lon = west[-1][0]                      # 12451E — 여기서 NLL 합류
    nll = [[round(lo, 5), round(la, 5)] for la, lo in NLL_WEST if lo >= join_lon]
    mdl = [[round(lo, 5), round(la, 5)] for la, lo in MDL]
    ring = west + nll + mdl + east
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    ring = [p for i, p in enumerate(ring) if i == 0 or p != ring[i - 1]]  # 연속 중복점 제거
    return {
        "type": "Feature",
        "properties": {
            "name": "INCHEON FIR",
            "units": parse_acc_sectors(SRC.read_text(encoding="utf-8", errors="replace")),
        },
        "geometry": {"type": "LineString", "coordinates": ring},
    }


def main():
    tma = parse_tma()
    fir = parse_fir()
    (OUT / "tma.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": tma}, ensure_ascii=False))
    (OUT / "fir.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": [fir]}, ensure_ascii=False))
    print(f"tma: {len(tma)} sectors, {(OUT/'tma.geojson').stat().st_size} bytes")
    from collections import Counter
    print("  TMA별:", dict(Counter(f["properties"]["tma"] for f in tma)))
    missing_vert = [f["properties"]["sector"] for f in tma if not f["properties"]["vert"]]
    print("  vert 없음:", missing_vert or "none")
    n = len(fir["geometry"]["coordinates"])
    print(f"fir: {n} pts, {(OUT/'fir.geojson').stat().st_size} bytes")


if __name__ == "__main__":
    main()
