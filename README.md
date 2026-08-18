# AIRTRACK

국내선 기내 오프라인 moving map. 대한민국 eAIP의 실제 항공로(airway) 위에 GPS 위치를 표시한다.
비행기 모드에서 완전 오프라인으로 동작하며, GPS 상실(서해 jamming 등) 시 마지막으로 snap된
항공로를 따라 dead reckoning으로 위치를 추정한다.

## 기능

- 항공로 차트: eAIP ENR 3.1/3.3 파싱 — RNAV/재래식 route 54개(route별 색 구분), fix 553개,
  en-route navaid 11개, 공항 15개. 거리 단위는 NM.
- 지도·공역: ato-engine(kto.geo.json + areas/airspace) — 해안선, P73/P518(MDL 기반),
  R/D 구역 등 199개. 색·빗금은 ato-engine CHARTC = ICAO ENR 6 항공로도 범례 그대로
  (P 적 · R 녹 · D 청록 · M 분홍 등, 45° hatch + 동색 외곽선). 훈련공역(M/A/C/I)은
  LYR에서 켜는 별도 레이어.
- 행정경계·정밀 해안선: KOSTAT 원본 TopoJSON을 arc 단위로 단순화(DP, 40m) —
  arc가 인접 구획의 공유 경계라 이웃끼리 절대 안 어긋나고, 남한 해안선은 사용횟수 1인
  arc를 이어붙여 만들어 행정경계와 좌표 단위로 일치(tools/admin.py). 경계는 선 피처로
  중복 없이 저장. LOD: 시도 z5.2+, 시군구 z7.8+, 라벨은 더 늦게. 한글 라벨은
  MapLibre local glyph 생성이라 폰트 파일 불필요.
- 하천: ato-engine kto.rivers (한강 등 폴리곤 49 + 지류 선) — 물색으로 육지를 파냄.
- 군기지: ato-engine korea.py의 K-사이트 기지 + ICAO 매핑 (RKSM/RKSW/RKSO/RKTP/RKTI/RKNN,
  민항 겹침 제외). 청색 마름모.
- 관제권(CTR) 31개 = eAIP ENR 2.1이 나열한 CONTROL ZONE 전수. 군 13곳은 ENR 2.1에
  좌표가 직접 적혀 있고, 나머지 18곳은 "See part 3 AERODROMES(AD)"라 각 공항 AD 2.17에서
  파싱했다(tools/parse_ctr.py). 전부 반경 5 NM 원이며 중심은 해당 문서 AD 2.2의 공식 ARP.
  간행물이 중복 제외를 명시한 Osan(수원)·Seongmu(청주)·Sokcho(P518+양양)는 상대 도형을
  실제로 빼서 경계를 만든다(shapely). 제거량은 해석해와 일치 검증: 오산 4.0%/이론 4.1%,
  성무 4.5%/4.6%, 속초 54.6%.
- 공항 좌표는 AD 2.2의 공식 ARP를 쓴다(하드코딩 목록은 인천 1.2km·김포 0.6km 어긋나 있었음).
- 활주로 심벌 39본(tools/parse_runways.py): 민항 29본은 AD 2.12 THR 좌표 왕복끝 쌍
  (방위 교차검증 최대 0.14°·길이 오차 최대 10m, RKJK·RKSM ft 변환), AD 페이지가 없는
  군기지 5곳(오산·수원·해미·중원·강릉)은 OSM 실측 지오메트리 10본(해미·오산·중원
  평행 2본 포함). OSM 캐시는 raw/osm_runways.json — 재생성 시 Overpass
  aeroway=runway around 각 기지 좌표.
- 비행 계획(FLT): 편명 조회(adsbdb, 온라인 시) 또는 공항쌍 선택 → 항로 그래프
  최단경로(Dijkstra, 완전 오프라인)로 예상 경로 생성·저장. 하단에 남은 거리·ETA.
- TMA 42개 섹터(적층 볼륨 병합)·인천 FIR 경계, 지도 탭 정보 패널(CTR/TMA/구역/FIR
  섹터 주파수). TRK는 자방위(°M, 편각 7~9°W 위도 보간).
- 성능: GeoJSON 소스 maxzoom 12 + geojson-vt 줌별 단순화, 레이어별 min/maxzoom 게이트.
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

### 갱신이 반영되지 않을 때

앱은 service worker로 모든 파일을 캐시하므로 push 직후 폰에서 바로 바뀌지 않는다.
LYR 패널의 **캐시 리셋**을 누르면 SW·캐시를 지우고 최신본을 다시 받는다.
GitHub Pages는 `max-age=600`을 주기 때문에 SW 설치와 리셋 모두 `cache: 'reload'`로
브라우저 HTTP 캐시를 우회하도록 해 두었다. 배포할 때는 `docs/sw.js`의 `VERSION`을
반드시 올려야 기존 설치본이 갱신을 인지한다.

## 네이티브 앱 (iOS — 백그라운드 트랙 기록)

PWA는 화면이 꺼지면 GPS가 멈추는 iOS 제약이 있어, `ios/`에 WKWebView 셸이 있다.
웹앱(docs/)을 통째로 번들하고(airtrack:// 커스텀 스킴 서빙 — SW 불필요, 오프라인
구조 보장), 위치는 native CLLocationManager가 공급한다. **백그라운드에서는 native가
트랙을 버퍼링(UserDefaults 보존)했다가 복귀 시 일괄 주입** — 화면이 꺼져 있어도
트랙이 끊기지 않는다. UIBackgroundModes=location, While-Using 허가로도 동작.

```bash
ios/reinstall.sh        # 웹 동기화 → 빌드 → 아이폰 설치 (7일마다 재실행)
```

- 무료 팀은 기기당 앱 3개 — CleanFeed·FujiRemote·FujiRecipe가 차 있으면 하나 비울 것.
- 웹을 고치면 `ios/sync-web.sh` 후 리빌드해야 앱에 반영된다 (reinstall.sh가 자동 수행).
- 시뮬레이터 검증: iOS 27 iPhone Air에서 위치 주입·백그라운드 5fix 버퍼링·복귀 일괄
  주입(트랙 16.8NM 복원)까지 확인됨.

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
tools/admin.py        시도/시군구 행정경계 + 라벨 중심점 GeoJSON
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
