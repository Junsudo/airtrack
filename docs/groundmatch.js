/* 지상 트랙 정제 (ground snap) — 실측 검증된 후처리 파이프라인의 앱 내장판.
 *
 * 원칙 (2026-08-19 CJU→GMP 실측으로 확정):
 * - 기록 원본(S.track)은 절대 수정하지 않는다. 정제는 표시·내보내기 뷰 전용.
 * - 공중 점은 그대로 통과. 지상 판정은 공항 6 km 이내 + GS<175 kt.
 * - 정지 산란(hold·게이트 멀티패스)은 median 한 점으로, 실이동(푸시백)은 보존
 *   — 구간 전·후반 median이 25 m 이상 벌어지면 실이동.
 * - 이동 중(GS≥8 kt)에는 주기장 리드인에 스냅하지 않는다.
 * - 스냅 실패한 짧은 오염 run은 버리고 네트워크 최단경로로 대체.
 *
 * 전역 의존: DATA(taxiways/runways/ground/airports), distM.
 */
const GM = (() => {
  const nets = {};   // icao → {segs:[{a,b,lid,stand}], adj:Map, latc}

  function nearestAirport(lon, lat) {
    let best = null;
    for (const f of DATA.airports.features) {
      const c = f.geometry.coordinates;
      const d = distM(lon, lat, c[0], c[1]);
      if (d < 6000 && (!best || d < best.d)) best = { icao: f.properties.icao, lon: c[0], lat: c[1], d };
    }
    return best;
  }

  function buildNet(apt) {
    if (nets[apt.icao]) return nets[apt.icao];
    const latc = apt.lat, kx = 111320 * Math.cos(latc * D2R), ky = 111320;
    const inR = (cs) => cs.some((c) => distM(c[0], c[1], apt.lon, apt.lat) < 6000);
    const segs = []; const nodes = [];
    let idx = 0;
    const addLine = (cs, lid, stand) => {
      for (let i = 1; i < cs.length; i++) segs.push({ a: cs[i - 1], b: cs[i], lid, stand });
      nodes.push(cs[0], cs[cs.length - 1]);
    };
    for (const f of DATA.taxiways.features) {
      if (f.geometry.type !== 'LineString' || !inR(f.geometry.coordinates)) continue;
      addLine(f.geometry.coordinates, f.properties.ref || ('t' + idx++), false);
    }
    for (const f of DATA.ground.features) {
      if (f.properties.k !== 'stand' || !inR(f.geometry.coordinates)) continue;
      addLine(f.geometry.coordinates, 's' + idx++, true);
    }
    for (const f of DATA.runways.features) {
      const cs = f.geometry.coordinates;
      if (!inR(cs)) continue;
      // 활주로를 근접 유도로 노드의 수선발로 분할해 유도로망과 잇는다
      const a = cs[0], b = cs[cs.length - 1];
      const ax = a[0] * kx, ay = a[1] * ky, bx = b[0] * kx, by = b[1] * ky;
      const dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy;
      const feet = [[0, a], [1, b]];
      for (const tn of nodes) {
        const px = tn[0] * kx, py = tn[1] * ky;
        const t = ((px - ax) * dx + (py - ay) * dy) / L2;
        if (t <= 0 || t >= 1) continue;
        const fx = ax + t * dx, fy = ay + t * dy;
        if (Math.hypot(px - fx, py - fy) < 25) feet.push([t, [fx / kx, fy / ky]]);
      }
      feet.sort((p, q) => p[0] - q[0]);
      const rid = 'r' + (f.properties.icao || '');
      for (let i = 1; i < feet.length; i++) {
        if (feet[i][0] - feet[i - 1][0] > 1e-9) segs.push({ a: feet[i - 1][1], b: feet[i][1], lid: rid, stand: false });
      }
      for (let i = 1; i < feet.length - 1; i++) {
        for (const tn of nodes) {
          if (distM(tn[0], tn[1], feet[i][1][0], feet[i][1][1]) < 25) {
            segs.push({ a: tn, b: feet[i][1], lid: rid + '-knit', stand: false });
          }
        }
      }
    }
    const adj = new Map();
    const key = (c) => c[0].toFixed(6) + ',' + c[1].toFixed(6);
    for (const s of segs) {
      const w = distM(s.a[0], s.a[1], s.b[0], s.b[1]);
      if (!adj.has(key(s.a))) adj.set(key(s.a), []);
      if (!adj.has(key(s.b))) adj.set(key(s.b), []);
      adj.get(key(s.a)).push([key(s.b), w, s.b]);
      adj.get(key(s.b)).push([key(s.a), w, s.a]);
    }
    nets[apt.icao] = { segs, adj, key, latc };
    return nets[apt.icao];
  }

  function project(lon, lat, a, b, latc) {
    const kx = 111320 * Math.cos(latc * D2R), ky = 111320;
    const px = lon * kx, py = lat * ky;
    const ax = a[0] * kx, ay = a[1] * ky, bx = b[0] * kx, by = b[1] * ky;
    const dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy;
    const raw = L2 === 0 ? 0 : ((px - ax) * dx + (py - ay) * dy) / L2;
    const t = Math.max(0, Math.min(1, raw));
    const qx = ax + t * dx, qy = ay + t * dy;
    return { d: Math.hypot(px - qx, py - qy), lon: qx / kx, lat: qy / ky, cl: raw <= 0 || raw >= 1 };
  }

  function snapPoint(net, lon, lat, gsKt, prevLid) {
    let best = null;
    for (const s of net.segs) {
      if (s.stand && gsKt >= 8) continue;
      if (gsKt >= 100 && s.lid[0] !== 'r') continue;   // 고속은 활주로 위뿐 — 유도로에 붙지 않는다
      const pr = project(lon, lat, s.a, s.b, net.latc);
      if (pr.cl && pr.d > 30) continue;   // 선분 범위 밖 클램프 — 후보 제외
      const eff = pr.d * (s.lid === prevLid ? 0.5 : 1.0);
      if (!best || eff < best.eff) best = { eff, d: pr.d, lon: pr.lon, lat: pr.lat, lid: s.lid, seg: s };
    }
    return best;
  }

  /* 고속(≥175 kt)이라도 활주로 라인 50 m 이내면 아직 활주로 위 —
   * 회전·부양·접지 활주 구간을 centerline에 붙여 이탈 단차를 없앤다. */
  function onRunway(net, lon, lat) {
    for (const s of net.segs) {
      if (s.lid[0] !== 'r') continue;
      const pr = project(lon, lat, s.a, s.b, net.latc);
      if (!pr.cl && pr.d < 50) return true;
    }
    return false;
  }

  /* 실시간: 표시 위치용 스냅. 저속(<60 kt)에서만, 40 m(정지 120 m) 이내일 때. */
  let liveLid = null;
  function liveSnap(lon, lat, spdMs) {
    const gs = (spdMs || 0) * 1.9438;
    if (gs >= 60) { liveLid = null; return null; }
    const apt = nearestAirport(lon, lat);
    if (!apt) { liveLid = null; return null; }
    const net = buildNet(apt);
    const s = snapPoint(net, lon, lat, gs, liveLid);
    const lim = gs < 3 ? 120 : 40;
    if (s && s.d < lim) { liveLid = s.lid; return { lon: s.lon, lat: s.lat }; }
    liveLid = null;
    return null;
  }

  function netPath(net, A, segA, B, segB) {
    if (segA === segB) return [];
    const dist = (c1, c2) => distM(c1[0], c1[1], c2[0], c2[1]);
    const dvals = new Map(); const prev = new Map(); const seen = new Set();
    const kA0 = net.key(segA.a), kA1 = net.key(segA.b);
    dvals.set(kA0, dist(A, segA.a)); dvals.set(kA1, dist(A, segA.b));
    const coordOf = new Map([[kA0, segA.a], [kA1, segA.b]]);
    const goalA = net.key(segB.a), goalB = net.key(segB.b);
    const pq = [[dvals.get(kA0), kA0], [dvals.get(kA1), kA1]];
    while (pq.length) {
      pq.sort((x, y) => x[0] - y[0]);
      const [w, u] = pq.shift();
      if (seen.has(u)) continue;
      seen.add(u);
      if (seen.has(goalA) && seen.has(goalB)) break;
      for (const [v, ew, vc] of net.adj.get(u) || []) {
        const nw = w + ew;
        if (nw < (dvals.get(v) ?? Infinity)) {
          dvals.set(v, nw); prev.set(v, u); coordOf.set(v, vc);
          pq.push([nw, v]);
        }
      }
    }
    let best = null;
    for (const g of [goalA, goalB]) {
      if (!dvals.has(g)) continue;
      const tot = dvals.get(g) + dist(coordOf.get(g), B);
      if (!best || tot < best.tot) best = { tot, g };
    }
    if (!best) return null;
    const straight = dist(A, B);
    if (best.tot > Math.max(2.5 * straight, straight + 400)) return null;
    const chain = [];
    let u = best.g;
    while (prev.has(u)) { chain.push(coordOf.get(u)); u = prev.get(u); }
    chain.push(coordOf.get(u));
    chain.reverse();
    return chain;
  }

  /* 배치: 트랙 배열 → 정제본 (원본 불변, 새 배열 반환). */
  function processTrack(track) {
    if (!track || track.length < 3) return track;
    const gs = (p) => (p.spd || 0) * 1.9438;
    // 0) 저속 스파이크 필터 — 실시간 필터(v31) 이전에 기록된 트랙과
    // 3회 연속 규칙으로 통과한 잔여 스파이크를 표시 전에 걸러낸다
    const filtered = [track[0]];
    let spikeN = 0;
    for (let i = 1; i < track.length; i++) {
      const q = track[i], last = filtered[filtered.length - 1];
      const dt = (q.t - last.t) / 1000;
      const vrep = Math.max(q.spd || 0, last.spd || 0);
      if (dt > 0 && dt < 30 && vrep < 15) {   // 저속(<15 m/s ≈ 30 kt)에서만 — 실시간 필터와 동일
        const d = distM(last.lon, last.lat, q.lon, q.lat);
        if (d > vrep * dt + 25 + 12 && d / dt > 25) {
          spikeN++;
          if (spikeN < 3) continue;
        }
      }
      spikeN = 0;
      filtered.push(q);
    }
    track = filtered;
    const groundOf = (p) => {
      const apt = nearestAirport(p.lon, p.lat);
      if (!apt) return null;
      if (gs(p) < 175) return apt;
      // 회전·부양·접지 활주: 고속이라도 활주로 라인 위면 아직 지상
      if (gs(p) < 250 && onRunway(buildNet(apt), p.lon, p.lat)) return apt;
      return null;
    };
    const ground = track.map(groundOf);
    // 1) dwell 붕괴 (실이동 보존)
    const med = (arr) => { const s = [...arr].sort((a, b) => a - b); return s[s.length >> 1]; };
    const t1 = [];
    for (let i = 0; i < track.length;) {
      if (ground[i] && gs(track[i]) < 5) {
        let j = i;
        while (j < track.length && ground[j] && gs(track[j]) < 5) j++;
        if (track[j - 1].t - track[i].t >= 10000 && j - i >= 4) {
          const run = track.slice(i, j), h = run.length >> 1;
          const m1 = [med(run.slice(0, h).map((p) => p.lon)), med(run.slice(0, h).map((p) => p.lat))];
          const m2 = [med(run.slice(h).map((p) => p.lon)), med(run.slice(h).map((p) => p.lat))];
          if (distM(m1[0], m1[1], m2[0], m2[1]) < 25) {
            const mlon = med(run.map((p) => p.lon)), mlat = med(run.map((p) => p.lat));
            t1.push({ ...run[0], lon: mlon, lat: mlat }, { ...run[run.length - 1], lon: mlon, lat: mlat });
            i = j; continue;
          }
        }
      }
      t1.push({ ...track[i] }); i++;
    }
    // 2) 스냅
    const g1 = t1.map(groundOf);
    const recs = [];
    let prevLid = null;
    for (let i = 0; i < t1.length; i++) {
      const p = t1[i];
      if (!g1[i]) { recs.push({ p, flag: 'air' }); prevLid = null; continue; }
      const net = buildNet(g1[i]);
      const s = snapPoint(net, p.lon, p.lat, gs(p), prevLid);
      // 고속은 측방 25 m 이내만 — 활주로를 떠나는 중(고속탈출)의 점을
      // 활주로로 끌어붙이지 않는다
      const lim = gs(p) < 3 ? 120 : (gs(p) >= 100 ? 25 : 50);
      if (s && s.d < lim) {
        prevLid = s.lid;
        recs.push({ p: { ...p, lon: s.lon, lat: s.lat }, flag: 'snap', lid: s.lid, seg: s.seg, apt: g1[i] });
      } else {
        recs.push({ p: { ...p }, flag: 'raw', apt: g1[i] });
      }
    }
    // 3) 같은 라인 사이 우회 run 재스냅
    for (let i = 0; i < recs.length; i++) {
      if (recs[i].flag !== 'snap') continue;
      const L = recs[i].lid;
      let j = i + 1;
      while (j < recs.length && recs[j].flag === 'snap' && recs[j].lid !== L) j++;
      if (j < recs.length && recs[j].flag === 'snap' && recs[j].lid === L && j - i - 1 >= 1 && j - i - 1 <= 16) {
        const net = buildNet(recs[i].apt);
        const Ls = net.segs.filter((s) => s.lid === L);
        const np = [];
        let ok = Ls.length > 0;
        for (let k = i + 1; k < j && ok; k++) {
          let best = null;
          for (const s of Ls) {
            const pr = project(recs[k].p.lon, recs[k].p.lat, s.a, s.b, net.latc);
            if (!best || pr.d < best.d) best = { ...pr, seg: s };
          }
          if (!best || best.d > 45) ok = false;
          else np.push([k, best]);
        }
        if (ok && np.length) {
          for (const [k, b] of np) {
            recs[k].p.lon = b.lon; recs[k].p.lat = b.lat;
            recs[k].lid = L; recs[k].seg = b.seg;
          }
          i = j - 1;
        }
      }
    }
    // 4) 조립: 오염 raw run 폐기 + 경로 삽입
    const out = [];
    let prevSnap = null; let pending = [];
    for (const r of recs) {
      if (r.flag === 'air') {
        out.push(...pending.map((x) => x.p)); pending = [];
        out.push(r.p); prevSnap = null; continue;
      }
      if (r.flag === 'raw') { pending.push(r); continue; }
      if (pending.length) {
        if (!(pending.length <= 12 && prevSnap && prevSnap.apt.icao === r.apt.icao)) {
          out.push(...pending.map((x) => x.p));
        }
        pending = [];
      }
      if (prevSnap && prevSnap.apt.icao === r.apt.icao) {
        const A = [prevSnap.p.lon, prevSnap.p.lat], B = [r.p.lon, r.p.lat];
        const straight = distM(A[0], A[1], B[0], B[1]);
        if (straight > 25) {
          const net = buildNet(r.apt);
          const path = netPath(net, A, prevSnap.seg, B, r.seg);
          if (path && path.length) {
            const t0 = prevSnap.p.t, tt = r.p.t;
            const pts = [A, ...path, B];
            const cum = [0];
            for (let k = 1; k < pts.length; k++) {
              cum.push(cum[k - 1] + distM(pts[k - 1][0], pts[k - 1][1], pts[k][0], pts[k][1]));
            }
            for (let k = 1; k < pts.length - 1; k++) {
              const frac = cum[cum.length - 1] > 0 ? cum[k] / cum[cum.length - 1] : 0;
              out.push({ t: Math.round(t0 + (tt - t0) * frac), lon: pts[k][0], lat: pts[k][1], alt: r.p.alt, spd: r.p.spd });
            }
          }
        }
      }
      out.push(r.p);
      prevSnap = r;
    }
    out.push(...pending.map((x) => x.p));
    // 니들 제거: 지상에서 150° 이상 되접히고 양쪽 다리가 짧은(<60 m) 점은
    // 전환 아티팩트 — 실제 유턴은 다리가 길거나 저속 dwell로 이미 처리됨
    let removed = true;
    while (removed) {
      removed = false;
      for (let i = 1; i < out.length - 1; i++) {
        const a = out[i - 1], b = out[i], c = out[i + 1];
        if (gs(b) < 3 || gs(b) > 175) continue;
        const d1 = distM(a.lon, a.lat, b.lon, b.lat);
        const d2 = distM(b.lon, b.lat, c.lon, c.lat);
        if (d1 > 60 || d2 > 60 || d1 < 0.5 || d2 < 0.5) continue;
        const b1 = Math.atan2(b.lon - a.lon, b.lat - a.lat);
        const b2 = Math.atan2(c.lon - b.lon, c.lat - b.lat);
        let dv = Math.abs((b2 - b1) * 180 / Math.PI) % 360;
        dv = Math.min(dv, 360 - dv);
        // 150° 되접힘(니들) 또는 짧은 다리의 급꺾임(전환 Z-단차) 제거
        if ((dv > 150 || (dv > 60 && d1 + d2 < 50)) && nearestAirport(b.lon, b.lat)) {
          out.splice(i, 1);
          removed = true;
          break;
        }
      }
    }
    return out;
  }

  return { liveSnap, processTrack };
})();
window.GM = GM;   // const 선언은 window 프로퍼티가 아니므로 명시 노출
