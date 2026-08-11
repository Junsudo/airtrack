#!/usr/bin/env python3
"""eAIP ENR 2.1 → 관제권(CTR) ctr.geojson.

<cite>XXX CONTROL ZONE</cite> 앵커가 있는 셀에서 경계(원 또는 좌표열)를 읽는다.
원: "A circle R NM radius centered at DDMMSSN DDDMMSSE"
폴리곤: 셀 안의 좌표열을 순서대로 잇는다(호 구간은 현으로 근사).
실행: python3 tools/parse_ctr.py
"""
import json
import math
import re
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "raw" / "ENR-2.1.html"
DST = ROOT / "docs" / "data" / "ctr.geojson"

COORD_RE = re.compile(r"(\d{6}(?:\.\d+)?)\s*([NS])\s*(\d{7}(?:\.\d+)?)\s*([EW])")
CIRCLE_RE = re.compile(
    r"circle[,]?\s*(?:of\s*)?([\d.]+)\s*NM\s*radius\s*cent(?:er|re)e?d?\s*(?:at|on)\s*"
    r"(\d{6}(?:\.\d+)?)\s*([NS])\s*(\d{7}(?:\.\d+)?)\s*([EW])", re.I)
VERT_RE = re.compile(r"(SFC|GND)\s*[-~]\s*([\d ,]+)\s*ft\s*(AGL|AMSL)?", re.I)
CLASS_RE = re.compile(r"Class\s+([A-G])\b", re.I)


def dms(raw, hemi):
    head, frac = (raw.split(".") + ["0"])[:2]
    sec = int(head[-2:]) + float("0." + frac)
    val = int(head[:-4]) + int(head[-4:-2]) / 60 + sec / 3600
    return -val if hemi in "SW" else val


def circle(lon, lat, r_nm, n=48):
    r = r_nm / 60.0
    return [[
        round(lon + r * math.sin(2 * math.pi * i / n) / max(0.2, math.cos(math.radians(lat))), 5),
        round(lat + r * math.cos(2 * math.pi * i / n), 5),
    ] for i in range(n + 1)]


AD_DIR = ROOT / "raw" / "ad"
RADIUS_RE = re.compile(  # "radius 5 NM" 과 "5 NM radius" / "5NM radius" 모두
    r"(?:radius[, ]*(?:of\s*)?([\d.]+)\s*NM|([\d.]+)\s*NM\s*radius)", re.I)
VERT2_RE = re.compile(r"(SFC|GND)\s*(?:to|-)\s*([\d ,]+)\s*ft\s*(AGL|AMSL)?", re.I)
CLASS2_RE = re.compile(r"classification\s+(?:Class\s+)?([A-G])\b", re.I)
DESIG_RE = re.compile(r"lateral limits?\s+(.{3,40}?CTR)\b", re.I)


def civil_from_ad(arp):
    """AD 2.17(ATS AIRSPACE) → 민항 CTR. 원 중심이 '(ARP)'로만 적힌 경우
    airports.geojson의 공항 좌표를 중심으로 쓴다."""
    out = []
    for f in sorted(AD_DIR.glob("*.html")):
        icao = f.stem
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", f.read_text(encoding="utf-8", errors="replace")))
        j = text.upper().find("ATS AIRSPACE")
        if j < 0:
            continue
        sec = text[j:j + 1200]
        dm = DESIG_RE.search(sec)
        name = (dm.group(1).strip().title().replace(' Ctr', ' CTR') if dm else f"{icao} CTR")
        rm = RADIUS_RE.search(sec)
        cm = COORD_RE.search(sec[:400])
        ring = None
        if rm:
            r_nm = float(rm.group(1) or rm.group(2))
            if cm:
                lat = dms(cm.group(1), cm.group(2))
                lon = dms(cm.group(3), cm.group(4))
            elif icao in arp:
                lon, lat = arp[icao]
            else:
                continue
            ring = circle(lon, lat, r_nm)
            kind = "circle"
        else:
            pts = [(dms(m.group(3), m.group(4)), dms(m.group(1), m.group(2)))
                   for m in COORD_RE.finditer(sec[:600])]
            pts = [p for p in pts if 118 < p[0] < 140 and 25 < p[1] < 45]
            if len(pts) < 3:
                continue
            ring = [[round(lon, 5), round(lat, 5)] for lon, lat in pts]
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            kind = "poly"
        vm = VERT2_RE.search(sec)
        km = CLASS2_RE.search(sec)
        out.append({
            "type": "Feature",
            "properties": {
                "name": name, "kind": kind,
                "vert": (f"{vm.group(1).upper()}-{vm.group(2).strip().replace(' ', '')}ft"
                         + (vm.group(3) or "")) if vm else None,
                "cls": km.group(1).upper() if km else None,
            },
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        })
    return out


def main():
    soup = BeautifulSoup(SRC.read_text(encoding="utf-8", errors="replace"), "html.parser")
    arp = {}
    apt_path = ROOT / "docs" / "data" / "airports.geojson"
    for f in json.loads(apt_path.read_text())["features"]:
        arp[f["properties"]["icao"]] = f["geometry"]["coordinates"]
    feats, seen = [], set()
    for f in civil_from_ad(arp):
        if f["properties"]["name"] not in seen:
            seen.add(f["properties"]["name"])
            feats.append(f)
    for cite in soup.find_all(["cite", "strong"]):
        label = re.sub(r"\s+", " ", cite.get_text()).strip()
        if not label.upper().endswith("CONTROL ZONE"):
            continue
        name = label[: -len("CONTROL ZONE")].strip().title()
        if name:
            name += " CTR"
        if not name or name in seen:
            continue
        td = cite.find_parent("td")
        if td is None:
            continue
        cell = re.sub(r"\s+", " ", td.get_text(" "))
        row = td.find_parent("tr")
        rowtext = re.sub(r"\s+", " ", row.get_text(" ")) if row else cell

        cm = CIRCLE_RE.search(cell)
        if cm:
            lat = dms(cm.group(2), cm.group(3))
            lon = dms(cm.group(4), cm.group(5))
            ring = circle(lon, lat, float(cm.group(1)))
            kind = "circle"
        else:
            pts = [(dms(m.group(3), m.group(4)), dms(m.group(1), m.group(2)))  # (lon, lat)
                   for m in COORD_RE.finditer(cell)]
            pts = [p for p in pts if 118 < p[0] < 140 and 25 < p[1] < 45]
            if len(pts) < 3:
                continue
            ring = [[round(lon, 5), round(lat, 5)] for lon, lat in pts]
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            kind = "poly"
        seen.add(name)
        vm = VERT_RE.search(rowtext)
        km = CLASS_RE.search(rowtext)
        feats.append({
            "type": "Feature",
            "properties": {
                "name": name, "kind": kind,
                "vert": (f"{vm.group(1).upper()}-{vm.group(2).strip().replace(' ', '')}ft"
                         + (vm.group(3) or "")) if vm else None,
                "cls": km.group(1).upper() if km else None,
            },
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        })
    DST.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False))
    print(f"ctr: {len(feats)} zones, {DST.stat().st_size} bytes")
    for f in feats:
        p = f["properties"]
        print(f"  {p['name']:<28} {p['kind']:<6} {p['vert']} {p['cls']}")


if __name__ == "__main__":
    main()
