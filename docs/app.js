/* AIRTRACK — 국내선 기내 오프라인 moving map
 * 데이터: eAIP ROK (AIRAC 2026-08-05) 파싱 GeoJSON + Natural Earth 해안선
 * 전 기능 오프라인 동작. GPS 상실 시 마지막 snap된 항공로를 따라 DR 추정.
 */
'use strict';

const AIRAC = '2026-08-05';
const DEMO = new URLSearchParams(location.search).get('demo') === '1';
const BASE = location.href.replace(/[^/]*(\?.*)?$/, '');

const COLORS = {
  bg: '#0a1620',
  // 육지 색은 ato-engine LF 그대로
  landSK: '#16232e', landNK: '#251a1e', landCN: '#231a1c', landJP: '#121a20', landRU: '#12181d',
  border: '#2c4356', grat: '#11202b',
  rnav: '#35c4e7', conv: '#5b7d94',
  fix: '#cfe3ee', navaid: '#7fe3c3', airport: '#9ad0e8',
  bndProv: '#3a5a74', bndMuni: '#31506a',
  track: '#ff2fd6',
  own: '#35c4e7', ownEst: '#ffb347',
};
// 공역 색 — ato-engine CHARTC 그대로 (ICAO ENR 6 항공로도 범례에서 뽑은 값)
//   P 금지 적색 · R 제한 녹색 · D 위험 청록 · A 경보 갈색
//   M 군 운용(MOA) 분홍 · C 민간훈련(CATA) 남색 · I ACMI 회색
const CHARTC = { P: '#d81820', R: '#50a830', D: '#0098b0', A: '#907050', M: '#f070a8', C: '#205088', I: '#888890' };

const $ = (id) => document.getElementById(id);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const fmtNM = (m) => (m / 1852).toFixed(1);
// 항로 구분용 팔레트 (어두운 배경에서 상호 구분되는 8색, RNAV route에 순환 배정)
const ROUTE_PALETTE = ['#35c4e7', '#5b8def', '#2fd6b5', '#9d7bff', '#e0c060', '#ff9f5a', '#ff7ab8', '#7ed957'];
const R_EARTH = 6371000;
const D2R = Math.PI / 180;

/* 거리(m): equirectangular 근사 — 수백 km 스케일에서 충분 */
function distM(lon1, lat1, lon2, lat2) {
  const x = (lon2 - lon1) * D2R * Math.cos(((lat1 + lat2) / 2) * D2R);
  const y = (lat2 - lat1) * D2R;
  return Math.hypot(x, y) * R_EARTH;
}
function bearingDeg(lon1, lat1, lon2, lat2) {
  const x = (lon2 - lon1) * Math.cos(((lat1 + lat2) / 2) * D2R);
  const y = lat2 - lat1;
  return ((Math.atan2(x, y) / D2R) + 360) % 360;
}
function angDiff(a, b) {
  let d = Math.abs(a - b) % 360;
  return d > 180 ? 360 - d : d;
}

/* ---------------- 상태 ---------------- */
const S = {
  pos: null,          // {lon,lat,alt,spd,course,acc,t} 마지막 실제 fix
  est: null,          // DR 추정 위치 {lon,lat,course}
  hdg: null,          // 기기 나침반 heading (deviceorientation)
  lastOwn: null,      // 마지막 own-ship 렌더 인자 (나침반 회전 재렌더용)
  fixLog: [],         // 최근 fix 수신 시각 (갱신률 계산용)
  snap: null,         // {routeId, coords, cum, s, nextName, nextDist}
  follow: true,
  recording: false,
  track: [],          // {t,lon,lat,alt,spd}
  wake: null,
  demo: { on: DEMO, s: 0, path: null },
};

let map, segs = [], routesById = {};

/* ---------------- 데이터 로드 + 지도 ---------------- */
const DATA = {};
const files = ['land', 'airways', 'fixes', 'navaids', 'areas', 'airports', 'boundaries', 'bnd_labels', 'ctr', 'rivers'];

Promise.all([
  Promise.all(files.map((f) =>
    fetch(`data/${f}.geojson`).then((r) => r.json()).then((j) => { DATA[f] = j; })
  )),
  new Promise((res) => {
    map = new maplibregl.Map({
      container: 'map',
      style: {
        version: 8,
        glyphs: BASE + 'vendor/fonts/{fontstack}/{range}.pbf',
        sources: {},
        layers: [{ id: 'bg', type: 'background', paint: { 'background-color': COLORS.bg } }],
      },
      center: [126.9, 35.6],
      zoom: 6.1,
      minZoom: 4.5,
      maxZoom: 14,
      maxBounds: [[119.0, 26.0], [138.0, 45.8]],
      dragRotate: false,
      pitchWithRotate: false,
      attributionControl: { compact: true, customAttribution: `eAIP ROK AIRAC ${AIRAC} · Natural Earth` },
    });
    map.touchZoomRotate.disableRotation();
    map.touchPitch && map.touchPitch.disable();
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 90, unit: 'nautical' }), 'bottom-left');
    map.on('load', res);
  }),
]).then(() => {
  addIcons();
  addLayers();
  buildSegments();
  bindUI();
  restoreTrack();
  startWatchdog();
  // hidden 로드 등으로 ResizeObserver를 놓친 경우 대비한 크기 감시
  setInterval(() => {
    const el = map.getContainer(), c = map.getCanvas();
    if (Math.abs(el.clientWidth - c.clientWidth) > 2 || Math.abs(el.clientHeight - c.clientHeight) > 2) {
      map.resize();
    }
  }, 2000);
  if (DEMO) startDemo(); else startGPS();
  // localhost 개발 중엔 SW 미등록(캐시가 수정사항을 가림). ?sw=1로 강제 가능.
  const wantSW = location.hostname !== 'localhost'
    || new URLSearchParams(location.search).get('sw') === '1';
  if (wantSW && 'serviceWorker' in navigator && location.protocol !== 'file:') {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  }
}).catch((e) => {
  toast('초기화 실패: ' + e.message);
  console.error(e);
});

/* ---------------- 아이콘 (런타임 캔버스) ---------------- */
function drawIcon(size, fn) {
  const c = document.createElement('canvas');
  c.width = c.height = size * 2;
  const ctx = c.getContext('2d');
  ctx.scale(2, 2);
  fn(ctx, size);
  return { img: ctx.getImageData(0, 0, size * 2, size * 2), ratio: 2 };
}
function addIcons() {
  const dart = (color) => drawIcon(30, (ctx, s) => {
    const cx = s / 2, cy = s / 2;
    ctx.beginPath();
    ctx.moveTo(cx, cy - 12);
    ctx.lineTo(cx - 8.5, cy + 10);
    ctx.lineTo(cx, cy + 5.5);
    ctx.lineTo(cx + 8.5, cy + 10);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = 1.6;
    ctx.strokeStyle = '#fff';
    ctx.stroke();
  });
  const tri = drawIcon(14, (ctx, s) => {
    ctx.beginPath();
    ctx.moveTo(s / 2, 2);
    ctx.lineTo(2, s - 2.5);
    ctx.lineTo(s - 2, s - 2.5);
    ctx.closePath();
    ctx.lineWidth = 1.7;
    ctx.strokeStyle = COLORS.fix;
    ctx.stroke();
  });
  const hex = drawIcon(16, (ctx, s) => {
    const cx = s / 2, cy = s / 2, r = s / 2 - 2;
    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
      const a = Math.PI / 3 * i - Math.PI / 6;
      const x = cx + r * Math.cos(a), y = cy + r * Math.sin(a);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    ctx.closePath();
    ctx.lineWidth = 1.7;
    ctx.strokeStyle = COLORS.navaid;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx, cy, 1.6, 0, 7);
    ctx.fillStyle = COLORS.navaid;
    ctx.fill();
  });
  const apt = drawIcon(15, (ctx, s) => {
    const cx = s / 2, cy = s / 2;
    ctx.beginPath();
    ctx.arc(cx, cy, s / 2 - 2.4, 0, 7);
    ctx.lineWidth = 1.8;
    ctx.strokeStyle = COLORS.airport;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx, cy, 1.7, 0, 7);
    ctx.fillStyle = COLORS.airport;
    ctx.fill();
  });
  // 군기지: 마름모 (색은 ato-engine NATC.ROK #3d9bff)
  const aptMil = drawIcon(15, (ctx, s) => {
    const cx = s / 2, cy = s / 2, r = s / 2 - 2.6;
    ctx.beginPath();
    ctx.moveTo(cx, cy - r);
    ctx.lineTo(cx + r, cy);
    ctx.lineTo(cx, cy + r);
    ctx.lineTo(cx - r, cy);
    ctx.closePath();
    ctx.lineWidth = 1.8;
    ctx.strokeStyle = '#3d9bff';
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx, cy, 1.7, 0, 7);
    ctx.fillStyle = '#3d9bff';
    ctx.fill();
  });
  map.addImage('apt-mil', aptMil.img, { pixelRatio: aptMil.ratio });
  const o = dart(COLORS.own), e = dart(COLORS.ownEst);
  map.addImage('own', o.img, { pixelRatio: o.ratio });
  map.addImage('own-est', e.img, { pixelRatio: e.ratio });
  map.addImage('fix-tri', tri.img, { pixelRatio: tri.ratio });
  map.addImage('navaid-hex', hex.img, { pixelRatio: hex.ratio });
  map.addImage('apt-ring', apt.img, { pixelRatio: apt.ratio });
  // 특수사용공역 45° 빗금 타일 (ato-engine 패턴 그대로: 바탕 .06 + 선 1.3px .5)
  for (const [cls, color] of Object.entries(CHARTC)) {
    const h = drawIcon(7, (ctx, s) => {
      ctx.globalAlpha = 0.06;
      ctx.fillStyle = color;
      ctx.fillRect(0, 0, s, s);
      ctx.globalAlpha = 0.5;
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.3;
      ctx.beginPath();
      ctx.moveTo(0, s);
      ctx.lineTo(s, 0);
      ctx.stroke();
    });
    map.addImage('hx' + cls, h.img, { pixelRatio: h.ratio });
  }
}

/* ---------------- 레이어 ---------------- */
function graticule() {
  const lines = [];
  for (let lon = 120; lon <= 137; lon++) lines.push([[lon, 26], [lon, 45.8]]);
  for (let lat = 27; lat <= 45; lat++) lines.push([[119, lat], [138, lat]]);
  return { type: 'Feature', properties: {}, geometry: { type: 'MultiLineString', coordinates: lines } };
}
const emptyFC = () => ({ type: 'FeatureCollection', features: [] });

function addLayers() {
  const FONT = ['Open Sans Semibold'];
  // maxzoom 12: GeoJSON 내부 타일링(geojson-vt)의 최대 분할 깊이 제한 — 그 이상은
  // overzoom으로 재사용해 고배율 성능을 아낀다. LOD 단순화는 geojson-vt가 줌별로 수행.
  const big = { type: 'geojson', maxzoom: 12, tolerance: 0.5 };
  map.addSource('grat', { type: 'geojson', data: graticule() });
  map.addSource('land', { ...big, data: DATA.land });
  map.addSource('boundaries', { ...big, data: DATA.boundaries });
  map.addSource('rivers', { ...big, data: DATA.rivers });
  map.addSource('bnd-labels', { type: 'geojson', data: DATA.bnd_labels });
  map.addSource('areas', { ...big, data: DATA.areas });
  map.addSource('airways', { ...big, tolerance: 0.2, data: DATA.airways });
  map.addSource('fixes', { type: 'geojson', data: DATA.fixes });
  map.addSource('navaids', { type: 'geojson', data: DATA.navaids });
  map.addSource('airports', { type: 'geojson', data: DATA.airports });
  map.addSource('ctr', { type: 'geojson', data: DATA.ctr });
  map.addSource('track', { type: 'geojson', data: emptyFC() });
  map.addSource('own', { type: 'geojson', data: emptyFC() });
  map.addSource('acc', { type: 'geojson', data: emptyFC() });

  const isRNAV = ['match', ['slice', ['get', 'id'], 0, 1], ['Y', 'Z', 'L'], true, false];
  // RNAV route별 고유색: 정렬된 id 순서로 팔레트 순환 배정 → 인접/교차 항로 구분
  const rnavIds = DATA.airways.features
    .map((f) => f.properties.id)
    .filter((id) => ['Y', 'Z', 'L'].includes(id[0]))
    .sort();
  const routeColor = ['match', ['get', 'id']];
  rnavIds.forEach((id, i) => routeColor.push(id, ROUTE_PALETTE[i % ROUTE_PALETTE.length]));
  routeColor.push(COLORS.conv);

  map.addLayer({ id: 'grat', type: 'line', source: 'grat', paint: { 'line-color': COLORS.grat, 'line-width': 0.6 } });
  map.addLayer({
    id: 'land-fill', type: 'fill', source: 'land',
    paint: {
      'fill-color': ['match', ['get', 'name'],
        'South Korea', COLORS.landSK,
        'North Korea', COLORS.landNK,
        'China', COLORS.landCN,
        'Russia', COLORS.landRU,
        COLORS.landJP],
    },
  });
  map.addLayer({ id: 'land-line', type: 'line', source: 'land', paint: { 'line-color': COLORS.border, 'line-width': 0.8 } });
  // 하천 (ato-engine kto.rivers): 폴리곤은 물색으로 육지를 파내고, 지류는 선
  map.addLayer({
    id: 'river-poly', type: 'fill', source: 'rivers',
    filter: ['==', ['get', 'kind'], 'poly'],
    paint: { 'fill-color': COLORS.bg },
  });
  map.addLayer({
    id: 'river-line', type: 'line', source: 'rivers', minzoom: 6.5,
    filter: ['==', ['get', 'kind'], 'line'],
    paint: {
      'line-color': '#14304a',
      'line-width': ['interpolate', ['linear'], ['zoom'], 7, 0.8, 12, 2.2],
      'line-opacity': 0.9,
    },
  });
  // 행정경계: 시도는 넓은 줌부터, 시군구는 확대해야 등장 (LOD)
  map.addLayer({
    id: 'bnd-muni-line', type: 'line', source: 'boundaries', minzoom: 7.8,
    filter: ['==', ['get', 'level'], 2],
    paint: {
      'line-color': COLORS.bndMuni,
      'line-width': ['interpolate', ['linear'], ['zoom'], 8, 0.6, 12, 1.1],
      'line-opacity': ['interpolate', ['linear'], ['zoom'], 8, 0.7, 12, 1],
      'line-dasharray': [2, 2],
    },
  });
  map.addLayer({
    id: 'bnd-prov-line', type: 'line', source: 'boundaries', minzoom: 5.2,
    filter: ['==', ['get', 'level'], 1],
    paint: {
      'line-color': COLORS.bndProv,
      'line-width': ['interpolate', ['linear'], ['zoom'], 6, 0.9, 12, 1.6],
      'line-opacity': 0.9,
    },
  });
  map.addLayer({
    id: 'bnd-prov-label', type: 'symbol', source: 'bnd-labels', minzoom: 5.8, maxzoom: 10.5,
    filter: ['==', ['get', 'level'], 1],
    layout: { 'text-field': ['get', 'name'], 'text-font': FONT, 'text-size': 10.5 },
    paint: { 'text-color': '#5c7e97', 'text-opacity': 0.85, 'text-halo-color': COLORS.bg, 'text-halo-width': 1.3 },
  });
  map.addLayer({
    id: 'bnd-muni-label', type: 'symbol', source: 'bnd-labels', minzoom: 8.6,
    filter: ['==', ['get', 'level'], 2],
    layout: {
      'text-field': ['get', 'name'], 'text-font': FONT,
      'text-size': ['interpolate', ['linear'], ['zoom'], 9, 9.5, 13, 13],
    },
    paint: { 'text-color': '#7fa3bd', 'text-opacity': 0.95, 'text-halo-color': COLORS.bg, 'text-halo-width': 1.4 },
  });

  // 특수사용공역 — ato-engine 스타일 그대로: ENR 6 범례 색 + 45° 빗금 + 같은 색 외곽선.
  // P/R/D는 기본 표시, 훈련·기타(M/A/C/I)는 LYR에서 켬.
  const areaColor = ['match', ['get', 'cls'],
    'P', CHARTC.P, 'R', CHARTC.R, 'D', CHARTC.D,
    'A', CHARTC.A, 'M', CHARTC.M, 'C', CHARTC.C, 'I', CHARTC.I,
    CHARTC.D];
  const hatch = ['concat', 'hx', ['get', 'cls']];
  const isPRD = ['match', ['get', 'cls'], ['P', 'R', 'D'], true, false];
  // 확대할수록 빗금 채움을 걷는다. 도시 줌에서 공역이 화면을 통째로 칠하면
  // 그 아래 구 경계·하천·지명을 읽을 수 없다. 외곽선과 라벨은 그대로 둔다.
  const fillLOD = ['interpolate', ['linear'], ['zoom'], 9, 1, 12, 0.12];
  map.addLayer({
    id: 'areas-fill', type: 'fill', source: 'areas', filter: isPRD,
    paint: { 'fill-pattern': hatch, 'fill-opacity': fillLOD },
  });
  map.addLayer({
    id: 'areas-line', type: 'line', source: 'areas', filter: isPRD,
    paint: { 'line-color': areaColor, 'line-opacity': 0.85, 'line-width': 1.1 },
  });
  map.addLayer({
    id: 'areas-label', type: 'symbol', source: 'areas', filter: isPRD, minzoom: 6.6,
    layout: { 'text-field': ['get', 'id'], 'text-font': FONT, 'text-size': 10 },
    paint: { 'text-color': areaColor, 'text-opacity': 0.9, 'text-halo-color': COLORS.bg, 'text-halo-width': 1.2 },
  });
  // ato-engine과 동일하게 훈련·기타(M/A/C/I)도 기본 표시. LYR에서 끌 수 있음.
  map.addLayer({
    id: 'areas2-fill', type: 'fill', source: 'areas', filter: ['!', isPRD],
    paint: { 'fill-pattern': hatch, 'fill-opacity': fillLOD },
  });
  map.addLayer({
    id: 'areas2-line', type: 'line', source: 'areas', filter: ['!', isPRD],
    paint: { 'line-color': areaColor, 'line-opacity': 0.85, 'line-width': 0.9 },
  });
  map.addLayer({
    id: 'areas2-label', type: 'symbol', source: 'areas', filter: ['!', isPRD], minzoom: 7.5,
    layout: { 'text-field': ['get', 'id'], 'text-font': FONT, 'text-size': 9.5 },
    paint: { 'text-color': areaColor, 'text-opacity': 0.9, 'text-halo-color': COLORS.bg, 'text-halo-width': 1.2 },
  });

  map.addLayer({
    id: 'awy-conv', type: 'line', source: 'airways', filter: ['!', isRNAV],
    paint: { 'line-color': COLORS.conv, 'line-width': 1, 'line-opacity': 0.65 },
  });
  map.addLayer({
    id: 'awy-rnav', type: 'line', source: 'airways', filter: isRNAV,
    paint: { 'line-color': routeColor, 'line-width': 1.4, 'line-opacity': 0.9 },
  });
  map.addLayer({
    id: 'awy-label', type: 'symbol', source: 'airways',
    layout: {
      'symbol-placement': 'line', 'text-field': ['get', 'id'], 'text-font': FONT,
      'text-size': 11, 'symbol-spacing': 190, 'text-padding': 4,
    },
    paint: {
      'text-color': routeColor,
      'text-halo-color': COLORS.bg, 'text-halo-width': 1.6,
    },
  });

  map.addLayer({
    id: 'fixes-on', type: 'symbol', source: 'fixes', minzoom: 5.8,
    filter: ['get', 'onRoute'],
    layout: {
      'icon-image': 'fix-tri', 'icon-allow-overlap': true,
      'text-field': ['get', 'name'], 'text-font': FONT, 'text-size': 9.5,
      'text-offset': [0, 0.9], 'text-anchor': 'top', 'text-optional': true,
    },
    paint: { 'text-color': COLORS.fix, 'text-opacity': 0.85, 'text-halo-color': COLORS.bg, 'text-halo-width': 1.4 },
  });
  map.addLayer({
    id: 'fixes-off', type: 'symbol', source: 'fixes', minzoom: 8.4,
    filter: ['!', ['get', 'onRoute']],
    layout: {
      'icon-image': 'fix-tri',
      'text-field': ['get', 'name'], 'text-font': FONT, 'text-size': 9,
      'text-offset': [0, 0.9], 'text-anchor': 'top', 'text-optional': true,
    },
    paint: { 'icon-opacity': 0.5, 'text-color': COLORS.fix, 'text-opacity': 0.5, 'text-halo-color': COLORS.bg, 'text-halo-width': 1.2 },
  });
  map.addLayer({
    id: 'navaids', type: 'symbol', source: 'navaids', minzoom: 5.4,
    layout: {
      'icon-image': 'navaid-hex', 'icon-allow-overlap': true,
      'text-field': ['concat', ['get', 'ident'], ' ', ['coalesce', ['get', 'freq'], '']],
      'text-font': FONT, 'text-size': 9.5, 'text-offset': [0, 1.0], 'text-anchor': 'top', 'text-optional': true,
    },
    paint: { 'text-color': COLORS.navaid, 'text-halo-color': COLORS.bg, 'text-halo-width': 1.4 },
  });
  const isMil = ['==', ['get', 'mil'], true];
  map.addLayer({
    id: 'airports', type: 'symbol', source: 'airports', minzoom: 5.0,
    layout: {
      'icon-image': ['case', isMil, 'apt-mil', 'apt-ring'], 'icon-allow-overlap': true,
      'text-field': ['get', 'icao'], 'text-font': FONT, 'text-size': 10,
      'text-offset': [0, 1.0], 'text-anchor': 'top', 'text-optional': true,
    },
    paint: {
      'text-color': ['case', isMil, '#3d9bff', COLORS.airport],
      'text-halo-color': COLORS.bg, 'text-halo-width': 1.4,
    },
  });

  // 관제권(CTR): 차트 관례대로 파선 청색 링. 반경 5NM이라 확대해야 의미 있음.
  map.addLayer({
    id: 'ctr-line', type: 'line', source: 'ctr', minzoom: 6.6,
    paint: { 'line-color': '#3d9bff', 'line-opacity': 0.6, 'line-width': 1, 'line-dasharray': [5, 3] },
  });
  map.addLayer({
    id: 'ctr-label', type: 'symbol', source: 'ctr', minzoom: 8.2,
    // note가 있으면 * — 간행물이 다른 구역과 겹치는 부분을 제외한다고 적은 곳.
    // 원은 제외 전 전체를 그리므로 실제 경계는 그보다 작다.
    layout: {
      'text-field': ['case', ['has', 'note'], ['concat', ['get', 'name'], ' *'], ['get', 'name']],
      'text-font': FONT, 'text-size': 9,
    },
    paint: { 'text-color': '#3d9bff', 'text-opacity': 0.75, 'text-halo-color': COLORS.bg, 'text-halo-width': 1.2 },
  });
  map.addLayer({
    id: 'acc', type: 'fill', source: 'acc',
    paint: { 'fill-color': COLORS.own, 'fill-opacity': 0.08 },
  });
  map.addLayer({
    id: 'track-glow', type: 'line', source: 'track',
    paint: { 'line-color': COLORS.track, 'line-width': 6, 'line-opacity': 0.25, 'line-blur': 2 },
  });
  map.addLayer({
    id: 'track', type: 'line', source: 'track',
    paint: { 'line-color': COLORS.track, 'line-width': 2.8, 'line-opacity': 0.95 },
  });
  map.addLayer({
    id: 'own', type: 'symbol', source: 'own',
    layout: {
      'icon-image': ['case', ['get', 'est'], 'own-est', 'own'],
      'icon-rotate': ['get', 'course'],
      'icon-rotation-alignment': 'map',
      'icon-allow-overlap': true,
    },
  });
}

const LAYER_GROUPS = {
  airways: ['awy-conv', 'awy-rnav', 'awy-label'],
  fixes: ['fixes-on', 'fixes-off'],
  navaids: ['navaids'],
  areas: ['areas-fill', 'areas-line', 'areas-label'],
  areas2: ['areas2-fill', 'areas2-line', 'areas2-label'],
  airports: ['airports'],
  boundaries: ['bnd-prov-line', 'bnd-muni-line', 'bnd-prov-label', 'bnd-muni-label'],
  ctr: ['ctr-line', 'ctr-label'],
  track: ['track-glow', 'track'],
};

/* ---------------- 항공로 snap ---------------- */
function buildSegments() {
  segs = [];
  for (const f of DATA.airways.features) {
    const id = f.properties.id;
    const cs = f.geometry.coordinates;
    const names = f.properties.fixes || [];
    routesById[id] = { coords: cs, names, cum: cumdist(cs) };
    for (let i = 0; i < cs.length - 1; i++) {
      segs.push({ id, i, a: cs[i], b: cs[i + 1], an: names[i], bn: names[i + 1] });
    }
  }
}
function cumdist(cs) {
  const cum = [0];
  for (let i = 1; i < cs.length; i++) {
    cum.push(cum[i - 1] + distM(cs[i - 1][0], cs[i - 1][1], cs[i][0], cs[i][1]));
  }
  return cum;
}

/* 현재 위치를 최근접 airway segment에 투영. threshold 밖이면 null */
function snapToAirway(lon, lat, course) {
  let best = null;
  for (const sg of segs) {
    const scale = Math.cos(lat * D2R);
    const ax = sg.a[0] * scale, ay = sg.a[1];
    const bx = sg.b[0] * scale, by = sg.b[1];
    const px = lon * scale, py = lat;
    const dx = bx - ax, dy = by - ay;
    const L2 = dx * dx + dy * dy;
    const t = L2 ? clamp(((px - ax) * dx + (py - ay) * dy) / L2, 0, 1) : 0;
    const qx = ax + t * dx, qy = ay + t * dy;
    const d = Math.hypot(px - qx, py - qy) * D2R * R_EARTH;
    if (!best || d < best.d) best = { sg, t, d };
  }
  if (!best || best.d > 8000) return null;

  const { sg, t } = best;
  const segBrg = bearingDeg(sg.a[0], sg.a[1], sg.b[0], sg.b[1]);
  const fwd = course == null || angDiff(course, segBrg) <= 90;
  const nextName = fwd ? sg.bn : sg.an;
  const route = routesById[sg.id];
  const segLen = route.cum[sg.i + 1] - route.cum[sg.i];
  const sAlong = route.cum[sg.i] + t * segLen;         // route 시작점부터 진행 거리(m)
  const nextDist = fwd
    ? route.cum[sg.i + 1] - sAlong
    : sAlong - route.cum[sg.i];
  return { routeId: sg.id, s: sAlong, fwd, nextName, nextDist, d: best.d };
}

/* route 상 진행거리 s → 좌표/방위 */
function pointAlong(routeId, s, fwd) {
  const r = routesById[routeId];
  if (!r) return null;
  const total = r.cum[r.cum.length - 1];
  s = clamp(s, 0, total);
  let i = r.cum.findIndex((c) => c >= s);
  if (i <= 0) i = 1;
  const t = (s - r.cum[i - 1]) / Math.max(1, r.cum[i] - r.cum[i - 1]);
  const a = r.coords[i - 1], b = r.coords[i];
  const lon = a[0] + (b[0] - a[0]) * t, lat = a[1] + (b[1] - a[1]) * t;
  let brg = bearingDeg(a[0], a[1], b[0], b[1]);
  if (!fwd) brg = (brg + 180) % 360;
  return { lon, lat, course: brg, atEnd: s <= 0 || s >= total, segIdx: i - 1 };
}

/* ---------------- GPS ---------------- */
function startGPS() {
  if (!('geolocation' in navigator)) { setPill('bad', 'GPS 미지원'); return; }
  if (!window.isSecureContext) { setPill('bad', 'HTTPS 필요'); toast('위치 기능은 HTTPS(또는 localhost)에서만 동작합니다'); return; }
  navigator.geolocation.watchPosition(handleFix, (err) => {
    if (err.code === err.PERMISSION_DENIED) {
      setPill('bad', '위치 권한 거부');
      toast('설정 > Safari(또는 해당 브라우저) > 위치에서 허용해 주세요');
    }
  }, { enableHighAccuracy: true, maximumAge: 0, timeout: 30000 });
}

function handleFix(p) {
  const c = p.coords;
  S.pos = {
    lon: c.longitude, lat: c.latitude,
    alt: c.altitude,
    // 일부 구현은 무효 speed/heading을 null 대신 -1로 준다
    spd: (c.speed != null && c.speed >= 0) ? c.speed : null,
    course: (c.heading != null && c.heading >= 0) ? c.heading : null,
    acc: c.accuracy, t: Date.now(),
  };
  S.est = null;
  updateSnap(S.pos.lon, S.pos.lat, S.pos.course, false);
  renderOwn(S.pos.lon, S.pos.lat, S.pos.course ?? 0, false, S.pos.acc);
  updateHUD(S.pos.spd, S.pos.alt, S.pos.course);
  S.fixLog.push(S.pos.t);
  while (S.fixLog.length && S.pos.t - S.fixLog[0] > 12000) S.fixLog.shift();
  setPill('ok', gpsPillText());
  if (S.recording) appendTrack(S.pos);
  if (S.follow) map.easeTo({ center: [S.pos.lon, S.pos.lat], duration: 800 });
}

function updateSnap(lon, lat, course, isDR) {
  const sn = snapToAirway(lon, lat, course);
  S.snap = sn;
  const el = $('snap');
  if (!sn) { el.textContent = ''; return; }
  el.textContent = `${isDR ? 'DR · ' : ''}${sn.routeId} → ${sn.nextName} ${fmtNM(sn.nextDist)} NM`;
}

/* ---------------- watchdog: fix 상실 감지 + DR ---------------- */
function startWatchdog() {
  setInterval(() => {
    if (!S.pos) return;
    const age = (Date.now() - S.pos.t) / 1000;
    if (age < 5) return;

    const spd = S.pos.spd;
    if (S.snap && spd != null && spd > 60) {
      // 마지막 snap 지점에서 항공로를 따라 시간 기반 전진 (DR)
      const s = S.snap.s + (S.snap.fwd ? 1 : -1) * spd * age;
      const pt = pointAlong(S.snap.routeId, s, S.snap.fwd);
      if (pt && !pt.atEnd) {
        S.est = pt;
        renderOwn(pt.lon, pt.lat, pt.course, true, null);
        const r = routesById[S.snap.routeId];
        const nextIdx = S.snap.fwd ? pt.segIdx + 1 : pt.segIdx;
        const nextName = r.names[nextIdx] || '';
        const nd = Math.abs(r.cum[nextIdx] - clamp(s, 0, r.cum[r.cum.length - 1]));
        $('snap').textContent = `DR · ${S.snap.routeId} → ${nextName} ${fmtNM(nd)} NM`;
        setPill('warn', `DR 추정 ${Math.round(age)}s`);
        if (S.follow) map.easeTo({ center: [pt.lon, pt.lat], duration: 900 });
        return;
      }
    }
    setPill(age > 15 ? 'bad' : 'warn', age > 15 ? `신호 없음 ${Math.round(age)}s` : `GPS 지연 ${Math.round(age)}s`);
  }, 1000);
}

/* ---------------- 렌더 ---------------- */
/* dart 회전: 이동 중(>8 m/s)엔 GPS course, 정지 상태에선 기기 나침반 heading */
function resolveHeading(course) {
  const spd = S.pos ? S.pos.spd : null;
  if (course != null && spd != null && spd > 8) return course;
  if (S.hdg != null) return S.hdg;
  return course ?? 0;
}

function renderOwn(lon, lat, course, est, accM) {
  S.lastOwn = { lon, lat, course, est, accM };
  map.getSource('own').setData({
    type: 'FeatureCollection',
    features: [{
      type: 'Feature',
      properties: { course: resolveHeading(course), est: !!est },
      geometry: { type: 'Point', coordinates: [lon, lat] },
    }],
  });
  const acc = map.getSource('acc');
  if (accM && accM > 40) {
    const n = 40, pts = [];
    for (let i = 0; i <= n; i++) {
      const a = 2 * Math.PI * i / n;
      pts.push([
        lon + (accM / (111320 * Math.cos(lat * D2R))) * Math.sin(a),
        lat + (accM / 111320) * Math.cos(a),
      ]);
    }
    acc.setData({ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [pts] } });
  } else {
    acc.setData(emptyFC());
  }
}

function updateHUD(spd, alt, course) {
  $('m-gs').textContent = spd != null ? Math.round(spd * 1.9438) : '—';
  $('m-alt').textContent = alt != null ? Math.round(alt * 3.2808).toLocaleString() : '—';
  $('m-trk').textContent = course != null ? String(Math.round(course)).padStart(3, '0') : '—';
}

/* iOS는 위성 개수를 앱에 노출하지 않는다(웹·네이티브 공통). 대신 관측 가능한
 * 지표로 신호 품질을 요약한다: 수평 accuracy → 4단계 바, fix 갱신률(저하 시만 표시). */
function gpsPillText() {
  const acc = S.pos.acc;
  const n = acc <= 8 ? 4 : acc <= 20 ? 3 : acc <= 50 ? 2 : acc <= 120 ? 1 : 0;
  const bars = '▮'.repeat(n) + '▯'.repeat(4 - n);
  let txt = `GPS ${bars} ±${Math.round(acc)}m`;
  if (S.fixLog.length >= 2) {
    const win = S.fixLog[S.fixLog.length - 1] - S.fixLog[0];
    if (win > 3000) {
      const hz = (S.fixLog.length - 1) / (win / 1000);
      if (hz < 0.8) txt += ` · ${hz.toFixed(1)}Hz`;
    }
  }
  return txt;
}

function setPill(cls, text) {
  const el = $('gps-pill');
  el.className = 'pill ' + cls;
  el.textContent = text;
}

let toastTimer;
function toast(msg) {
  const el = $('toast');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
}

/* ---------------- 트랙 기록 ---------------- */
const TRACK_KEY = DEMO ? 'airtrack.track.demo' : 'airtrack.track.v1'; // 데모 트랙은 실데이터와 격리

function appendTrack(pos) {
  const last = S.track[S.track.length - 1];
  if (last) {
    const d = distM(last.lon, last.lat, pos.lon, pos.lat);
    const dt = (pos.t - last.t) / 1000;
    if (d < 25 && dt < 5) return;
  }
  S.track.push({ t: pos.t, lon: pos.lon, lat: pos.lat, alt: pos.alt, spd: pos.spd });
  renderTrack();
  if (S.track.length % 5 === 0) saveTrack();
}

function trackSegments() {
  const lines = [];
  let cur = [];
  for (let i = 0; i < S.track.length; i++) {
    const p = S.track[i];
    if (i > 0 && p.t - S.track[i - 1].t > 120000 && cur.length) {
      if (cur.length > 1) lines.push(cur);
      cur = [];
    }
    cur.push([p.lon, p.lat]);
  }
  if (cur.length > 1) lines.push(cur);
  return lines;
}

function renderTrack() {
  const lines = trackSegments();
  map.getSource('track').setData(lines.length ? {
    type: 'Feature', properties: {},
    geometry: { type: 'MultiLineString', coordinates: lines },
  } : emptyFC());
  let dist = 0;
  for (let i = 1; i < S.track.length; i++) {
    if (S.track[i].t - S.track[i - 1].t > 120000) continue; // gap 구간은 거리 합산 제외
    dist += distM(S.track[i - 1].lon, S.track[i - 1].lat, S.track[i].lon, S.track[i].lat);
  }
  const dur = S.track.length > 1 ? (S.track[S.track.length - 1].t - S.track[0].t) / 1000 : 0;
  const hh = String(Math.floor(dur / 3600)).padStart(2, '0');
  const mm = String(Math.floor(dur / 60) % 60).padStart(2, '0');
  const ss = String(Math.floor(dur % 60)).padStart(2, '0');
  $('trk-stats').textContent = S.track.length
    ? `${fmtNM(dist)} NM · ${hh}:${mm}:${ss}` : '';
}

function saveTrack() {
  try { localStorage.setItem(TRACK_KEY, JSON.stringify(S.track)); } catch (e) { /* quota/private */ }
}
function restoreTrack() {
  try {
    const raw = localStorage.getItem(TRACK_KEY);
    if (raw) { S.track = JSON.parse(raw); renderTrack(); }
  } catch (e) { /* ignore */ }
}

function exportGPX() {
  if (!S.track.length) { toast('기록된 트랙이 없습니다'); return; }
  const segsOut = [];
  let cur = [];
  for (let i = 0; i < S.track.length; i++) {
    const p = S.track[i];
    if (i > 0 && p.t - S.track[i - 1].t > 120000 && cur.length) { segsOut.push(cur); cur = []; }
    cur.push(p);
  }
  if (cur.length) segsOut.push(cur);
  const trksegs = segsOut.map((sg) =>
    `<trkseg>${sg.map((p) =>
      `<trkpt lat="${p.lat.toFixed(6)}" lon="${p.lon.toFixed(6)}">` +
      (p.alt != null ? `<ele>${p.alt.toFixed(1)}</ele>` : '') +
      `<time>${new Date(p.t).toISOString()}</time></trkpt>`
    ).join('')}</trkseg>`
  ).join('');
  const gpx = `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<gpx version="1.1" creator="AIRTRACK" xmlns="http://www.topografix.com/GPX/1/1">` +
    `<trk><name>AIRTRACK ${new Date(S.track[0].t).toISOString().slice(0, 10)}</name>${trksegs}</trk></gpx>`;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([gpx], { type: 'application/gpx+xml' }));
  const d = new Date(S.track[0].t);
  a.download = `airtrack-${d.toISOString().slice(0, 16).replace(/[-:T]/g, '')}.gpx`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

/* ---------------- 나침반 ---------------- */
let compassOn = false;
function enableCompass() {
  const attach = () => {
    window.addEventListener('deviceorientation', (e) => {
      let hdg = null;
      if (typeof e.webkitCompassHeading === 'number') hdg = e.webkitCompassHeading;
      else if (e.absolute && e.alpha != null) hdg = (360 - e.alpha) % 360;
      if (hdg == null) return;
      compassOn = true;
      S.hdg = hdg;
      $('rose').setAttribute('transform', `rotate(${-hdg} 22 22)`);
      $('hdg-num').textContent = String(Math.round(hdg)).padStart(3, '0') + '°';
      // 정지 상태면 own-ship dart도 나침반 방향으로 회전 (150ms 스로틀)
      const now = performance.now();
      if (S.lastOwn && (!S.pos || S.pos.spd == null || S.pos.spd <= 8)
          && now - (enableCompass._t || 0) > 150) {
        enableCompass._t = now;
        renderOwn(S.lastOwn.lon, S.lastOwn.lat, S.lastOwn.course, S.lastOwn.est, S.lastOwn.accM);
      }
    }, { passive: true });
    setTimeout(() => { if (!compassOn) toast('나침반 데이터가 없습니다 (데스크톱?)'); }, 2500);
  };
  if (typeof DeviceOrientationEvent !== 'undefined' && DeviceOrientationEvent.requestPermission) {
    DeviceOrientationEvent.requestPermission()
      .then((st) => { if (st === 'granted') attach(); else toast('나침반 권한이 거부되었습니다'); })
      .catch(() => toast('나침반 권한 요청 실패'));
  } else {
    attach();
  }
}

/* ---------------- Wake Lock ---------------- */
async function toggleWake() {
  if (S.wake) {
    try { await S.wake.release(); } catch (e) { /* noop */ }
    S.wake = null;
    $('btn-wake').classList.remove('on');
    return;
  }
  try {
    S.wake = await navigator.wakeLock.request('screen');
    $('btn-wake').classList.add('on');
    S.wake.addEventListener('release', () => {
      if (S.wake) { S.wake = null; $('btn-wake').classList.remove('on'); }
    });
  } catch (e) {
    toast('화면 유지 미지원 (iOS 16.4+ 필요)');
  }
}
document.addEventListener('visibilitychange', async () => {
  if (document.visibilityState === 'visible' && $('btn-wake').classList.contains('on') && !S.wake) {
    try { S.wake = await navigator.wakeLock.request('screen'); } catch (e) { /* noop */ }
  }
  if (document.visibilityState === 'hidden') saveTrack();
});
window.addEventListener('pagehide', saveTrack);

/* ---------------- UI 바인딩 ---------------- */
function bindUI() {
  $('btn-zi').onclick = () => map.zoomIn();
  $('btn-zo').onclick = () => map.zoomOut();
  $('btn-follow').onclick = () => {
    S.follow = !S.follow;
    $('btn-follow').classList.toggle('on', S.follow);
    const p = S.est || S.pos;
    if (S.follow && p) map.easeTo({ center: [p.lon, p.lat] });
  };
  map.on('dragstart', () => { S.follow = false; $('btn-follow').classList.remove('on'); });

  $('compass').onclick = enableCompass;
  $('btn-wake').onclick = toggleWake;

  $('btn-rec').onclick = () => {
    S.recording = !S.recording;
    $('btn-rec').classList.toggle('on', S.recording);
    $('btn-rec').textContent = S.recording ? '■ REC' : '● REC';
    if (S.recording && !S.pos) toast('GPS fix 대기 중 — 잡히면 기록됩니다');
    if (!S.recording) saveTrack();
  };
  $('btn-gpx').onclick = exportGPX;
  $('btn-clear').onclick = () => {
    if (!S.track.length) return;
    if (confirm('기록된 트랙을 삭제할까요?')) {
      S.track = [];
      saveTrack();
      renderTrack();
    }
  };

  $('btn-layers').onclick = () => { $('layers-panel').hidden = !$('layers-panel').hidden; };
  $('btn-cache-reset').onclick = async () => {
    if (!navigator.onLine) { toast('오프라인 상태에서는 캐시를 지울 수 없습니다 (재다운로드 불가)'); return; }
    if (!confirm('캐시를 비우고 최신 버전을 다시 받을까요?\n(트랙 기록은 유지됩니다)')) return;
    try {
      if ('serviceWorker' in navigator) {
        const regs = await navigator.serviceWorker.getRegistrations();
        await Promise.all(regs.map((r) => r.unregister()));
      }
      if (window.caches) {
        const keys = await caches.keys();
        await Promise.all(keys.map((k) => caches.delete(k)));
      }
      // SW 캐시를 지워도 브라우저 HTTP 캐시(max-age=600)가 옛 파일을 준다.
      // 핵심 파일을 강제로 다시 받아 HTTP 캐시 항목을 갈아끼운 뒤 리로드한다.
      await Promise.all(['./', 'index.html', 'app.js', 'style.css', 'sw.js']
        .map((u) => fetch(new Request(u, { cache: 'reload' })).catch(() => null)));
    } catch (e) { /* 캐시 접근 실패해도 리로드는 진행 */ }
    location.reload();
  };
  document.querySelectorAll('#layers-panel input').forEach((cb) => {
    cb.onchange = () => {
      const ids = LAYER_GROUPS[cb.dataset.group] || [];
      ids.forEach((id) => map.setLayoutProperty(id, 'visibility', cb.checked ? 'visible' : 'none'));
    };
  });

  if (DEMO) $('demo-banner').hidden = false;
}

/* ---------------- 데모 모드 (?demo=1) ----------------
 * Y722를 따라 김포권(SOT)→제주까지 가상 비행. 실제 GPS 파이프라인(handleFix)에
 * 합성 fix를 흘려보낸다. 진행률 42~58% 구간은 fix를 끊어 jamming 상황을 재현
 * → watchdog의 DR 추정이 자동으로 이어받는 것을 확인할 수 있다.
 */
function startDemo() {
  const y722 = routesById['Y722'];
  if (!y722) { toast('데모: Y722 데이터 없음'); return; }
  const coords = y722.coords.filter((c) => c[1] >= 33.3);
  coords.push([126.493, 33.5113]); // RKPC
  const cum = cumdist(coords);
  const total = cum[cum.length - 1];
  const TIMESCALE = 14;
  let s = 0, lastT = performance.now();

  setPill('warn', 'DEMO 시작');
  S.track = [];                     // 데모는 매 실행 새 트랙으로 시작
  saveTrack();
  renderTrack();
  S.recording = true;               // 데모에서는 트랙 기록 자동 시작
  $('btn-rec').classList.add('on');
  $('btn-rec').textContent = '■ REC';
  setInterval(() => {
    const now = performance.now();
    const dt = ((now - lastT) / 1000) * TIMESCALE;
    lastT = now;
    const f = s / total;
    let v;
    if (f < 0.06) v = 85 + (235 - 85) * (f / 0.06);
    else if (f > 0.86) v = 235 - (235 - 75) * ((f - 0.86) / 0.14);
    else v = 235;
    s = Math.min(total, s + v * dt);
    let alt;
    const fc = s / total;
    if (fc < 0.25) alt = 250 + (9750 - 250) * (fc / 0.25);
    else if (fc > 0.7) alt = 9750 - (9750 - 120) * ((fc - 0.7) / 0.3);
    else alt = 9750;

    // 좌표 보간
    let i = cum.findIndex((c) => c >= s);
    if (i <= 0) i = 1;
    const t = (s - cum[i - 1]) / Math.max(1, cum[i] - cum[i - 1]);
    const a = coords[i - 1], b = coords[i];
    const lon = a[0] + (b[0] - a[0]) * t, lat = a[1] + (b[1] - a[1]) * t;
    const brg = bearingDeg(a[0], a[1], b[0], b[1]);

    // jamming 재현 구간: fix 미전달 → DR 폴백 확인
    if (fc > 0.42 && fc < 0.58) return;

    handleFix({
      coords: {
        longitude: lon, latitude: lat, altitude: alt,
        speed: v, heading: brg, accuracy: 30 + Math.random() * 15,
      },
      timestamp: Date.now(),
    });
    if (s >= total) setPill('ok', 'DEMO 착륙');
  }, 250);
}
