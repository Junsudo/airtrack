# AIRTRACK

국내선 기내 오프라인 moving map. 대한민국 eAIP의 실제 항공로(airway) 위에 GPS 위치를 표시한다.
비행기 모드에서 완전 오프라인으로 동작하며, GPS 상실(서해 jamming 등) 시 마지막으로 snap된
항공로를 따라 dead reckoning으로 위치를 추정한다.

## 기능

- 항공로 차트: eAIP ENR 3.1/3.3 파싱 — RNAV/재래식 route 54개(route별 색 구분), fix 553개,
  en-route navaid 11개, 공항 15개. 거리 단위는 NM.
- 지도·공역: ato-engine(kto.geo.json + areas/airspace) — 해안선, P73/P518(MDL 기반),
  R/D 구역 등 199개. 훈련공역(M/A/C/I)은 LYR에서 켜는 별도 레이어.
- GPS: 파란 dart(진행방향 회전) + 정확도 원. airway snap → "Y722 → OLMEN 18.4 km" 표시.
- DR 폴백: fix가 5초 이상 끊기면 마지막 속도로 항공로를 따라 추정 위치를 전진(주황 dart, "DR ·" 표시).
- 나침반: 우측 나침반 버튼 탭 → 권한 허용 → 기기 방향 표시.
- 트랙: REC로 기록, 마젠타 라인 표시, GPX 내보내기, localStorage 유지.
- 줌 버튼 + 핀치 줌, follow 모드(◎), 레이어 토글(LYR), 화면 항상 켜기(☀, Wake Lock).
- PWA: service worker cache-first — 한 번 로드하면 이후 완전 오프라인.

## 로컬 실행

```bash
python3 -m http.server 8734 --directory docs
```

- 데모 비행(가상 GPS, jamming 재현 포함): `http://localhost:8734/?demo=1`
- localhost에서는 service worker를 등록하지 않는다(개발 편의). SW까지 테스트하려면 `?sw=1`.

## 배포 (GitHub Pages)

앱 전체가 `docs/`에 있으므로:

1. GitHub에 repo 생성 후 push.
2. Settings → Pages → Source: `main` branch, `/docs` folder.
3. 발급된 `https://<계정>.github.io/airtrack/` 를 iPhone Safari로 열기 →
   공유 → **홈 화면에 추가**.
4. 첫 실행은 온라인 상태에서(캐시 채움). 이후 비행기 모드에서 동작.

위치 기능은 HTTPS 필수라 GitHub Pages 배포본으로 써야 한다(로컬 IP 접속으로는 GPS가 안 뜬다).

## 기내 사용 체크리스트

1. 창가 좌석. 탑승 전 온라인 상태에서 앱 한 번 열기.
2. 게이트에서 위치가 잡히는 걸 확인한 **다음** 비행기 모드 (ephemeris 확보 → 기내 warm start).
3. 위치 서비스는 끄지 말 것. 기내에서는 폰을 창문에 밀착하고 1~3분 대기.
4. 서해 구간에서 "DR 추정"으로 바뀌는 것은 정상(jamming 가능성) — 항공로 기반 추정으로 이어진다.

## 데이터 갱신 (AIRAC 28일 주기, 연 1~2회면 충분)

```bash
# 1) 최신 패키지 날짜 탐색 (AIRAC 발효일 하루 전 날짜 + "-AIRAC")
for d in 2026-09-02 2026-09-30 2026-10-28; do
  curl -s -o /dev/null -w "$d: %{http_code}\n" \
    "https://aim.koca.go.kr/eaipPub/Package/${d}-AIRAC/html/eAIP/KR-ENR-3.3-en-GB.html"
done

# 2) HTML 재다운로드 (PKG 날짜를 위에서 찾은 것으로)
PKG="https://aim.koca.go.kr/eaipPub/Package/<날짜>-AIRAC/html/eAIP"
for sec in ENR-3.3 ENR-3.1 ENR-4.1 ENR-4.4 ENR-5.1; do
  curl -s "$PKG/KR-$sec-en-GB.html" -o "raw/$sec.html"
done

# 3) GeoJSON 재생성 + SW 캐시 버전 갱신
python3 tools/parse_eaip.py   # 항공로/fix/navaid/공항
python3 tools/from_ato.py     # 지도/공역 (ato-engine 데이터가 바뀐 경우에만)
# docs/sw.js 의 VERSION 문자열을 새 날짜로 바꾼 뒤 push
```

`docs/app.js`의 `AIRAC` 상수(화면 attribution 표기)도 같이 바꿔주면 좋다.

## 구조

```
tools/parse_eaip.py   eAIP HTML → GeoJSON 파서 (항공로/fix/navaid/공항)
tools/from_ato.py     ato-engine → 해안선(land)/공역(areas) GeoJSON
tools/clip_land.py    (구) Natural Earth clip — from_ato.py로 대체됨
raw/                  다운로드 원본 (gitignore)
docs/                 배포되는 앱 전체 (GitHub Pages 소스)
  data/*.geojson      항공로/fix/navaid/구역/공항/해안선
  vendor/             MapLibre GL JS 5.24 + 글리프 폰트 (완전 오프라인용 번들)
```

## 한계

- PWA 특성상 화면이 켜져 있는 동안만 GPS 기록이 쌓인다(잠금 중 공백은 gap으로 분리 처리).
- iOS가 저장공간 압박 시 캐시를 지울 수 있다 — 출발 전 온라인에서 한 번 열어 재캐시 권장.
- DR 추정은 항공로에 snap된 상태에서만 이어진다(항로 이탈 비행은 추정 불가).
- 항공용 항법 장비가 아니다. 참고용.
