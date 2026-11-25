// ⚖️ 종가 차익 탭 — data/out/closearb.json (derive/closearb.py)
tab("closearb", {label: "⚖️ 종가 차익", css: true, files: ["closearb.json"],
                 load: () => loadClosearb(), render: () => renderClosearb()});

let CA = null, CA_SORT = "cap";   // data/out/closearb.json
async function loadClosearb(){
  try{ CA = await (await fetch("data/out/closearb.json?_="+Date.now())).json(); }catch(_){ CA = null; }
  document.getElementById("meta").textContent = CA
    ? `⚖️ ${CA.phase} · 갱신 ${CA.generated_at} · 오늘 기록 ${CA.marks.join("·")||"없음"}`
    : "종가 차익 데이터 없음";
  renderClosearb();
}
const CA_KEYS = {
  cap: r=> r.mktcap ?? -1,
  z: r=> r.rank,
  dev: r=> r.dev_pct!=null ? Math.abs(r.dev_pct) : -1,
  chg10: r=> r.chg10_pct!=null ? r.chg10_pct : -99,
  won: r=> r.dev_won!=null ? Math.abs(r.dev_won) : -1,
  jump: r=> r.jump_pct!=null ? Math.abs(r.jump_pct) : -1,
  imb: r=> r.imb_eok!=null ? Math.abs(r.imb_eok) : -1,
};
function renderClosearb(){
  const box = document.getElementById("closearbView");
  if(!CA){ box.innerHTML = `<div class="empty">종가 차익 데이터가 없습니다 — 거래일 08:00~20:00 에 폴러가 채웁니다.</div>`; return; }
  const q = (filters.q||"").trim();
  const rows = CA.rows.filter(r=> filters[r.market] !== false && (!q || r.name.includes(q) || r.code.includes(q)))
    .sort((a,b)=> CA_KEYS[CA_SORT](b) - CA_KEYS[CA_SORT](a));
  const chip = (k,t)=>`<span class="chip${CA_SORT===k?" on":""}" data-casort="${k}">${t}</span>`;
  const n0 = v => v==null ? "-" : won(Math.round(v));
  const td = (v, txt, extra="") => `<td class="${sgn(v)} ${extra}">${txt}</td>`;
  const END = CA.end_min, nowMin = CA.now_min;
  const spark = (r, W=150, H=26) => {
    if(!r.trend?.length) return "-";
    const ys = r.trend.map(p=>p[1]).concat(r.mean_pct, r.basis_pct==null ? [] : [r.basis_pct]);
    const lo = Math.min(...ys), hi = Math.max(...ys), Y = v => (H-2 - (v-lo)/((hi-lo)||1)*(H-4)).toFixed(1), X = m => (m/END*W).toFixed(1);
    const now = r.basis_pct==null ? "" : `<circle cx="${X(nowMin)}" cy="${Y(r.basis_pct)}" r="2.5" class="${sgn(r.dev_pct)}" fill="currentColor"/>`;
    return `<svg width="${W}" height="${H}" style="vertical-align:middle"><title>${signedPct(lo)} ~ ${signedPct(hi)}</title>
      <line x1="0" x2="${W}" y1="${Y(r.mean_pct)}" y2="${Y(r.mean_pct)}" stroke="var(--muted)" stroke-dasharray="2 2"/>
      <polyline points="${r.trend.map(p=>X(p[0])+","+Y(p[1])).join(" ")}" fill="none" stroke="var(--text)" stroke-width="1"/>${now}</svg>`;
  };
  const basket = (mkt, title) => {
    const b = CA.baskets?.[mkt];
    if(!b || !b.trend.length || filters[mkt] === false) return "";
    const W = 340, H = 110, pts = b.trend.concat(b.now ? [[nowMin, ...b.now]] : []);
    const ys = pts.flatMap(p=>p.slice(1)).concat(0);
    const lo = Math.min(...ys), hi = Math.max(...ys), Y = v => (H-4 - (v-lo)/((hi-lo)||1)*(H-8)).toFixed(1), X = m => (m/END*W).toFixed(1);
    const area = pts.map(p=>X(p[0])+","+Y(p[3])).concat(pts.slice().reverse().map(p=>X(p[0])+","+Y(p[1]))).join(" ");
    const last = b.now ? b.now[1] : null;
    return `<div class="ca-basket"><div class="ca-card-top"><b>${title}</b> <span class="dim">${b.n}종목</span></div>
      <div class="ca-card-dev ${sgn(last)}">${last==null ? "-" : (last>0?"+":"")+last.toFixed(2)+"%p"} <span>지금 중앙값 · 10분 <b class="pos">▲${b.up10}</b> <b class="neg">▼${b.down10}</b></span></div>
      <svg width="${W}" height="${H}"><polygon points="${area}" fill="var(--muted)" fill-opacity=".25"/>
        <line x1="0" x2="${W}" y1="${Y(0)}" y2="${Y(0)}" stroke="var(--muted)" stroke-dasharray="3 3"/>
        <polyline points="${pts.map(p=>X(p[0])+","+Y(p[2])).join(" ")}" fill="none" stroke="var(--text)" stroke-width="1.5"/>
        ${b.now ? `<circle cx="${X(nowMin)}" cy="${Y(b.now[1])}" r="3" class="${sgn(last)}" fill="currentColor"/>` : ""}</svg>
      <div class="dim">각 종목 베이시스 − 그 종목 오늘 평균 · 선 중앙값 · 띠 가운데 50% · 점선 평소</div></div>`;
  };
  const byCode = Object.fromEntries(rows.map(r=> [r.code, r]));
  const hot = CA.hot_codes.map(c=> byCode[c]).filter(Boolean);
  const cards = hot.map(r=>`<div class="ca-card" data-code="${esc(r.code)}">
      <div class="ca-card-top"><b>${esc(r.name)}</b> <span class="st">${esc(r.status)}</span><span class="dim">z ${r.z.toFixed(1)}</span></div>
      <div class="ca-card-dev ${sgn(r.dev_pct)}">${signedPct(r.dev_pct)} <span>${n0(r.dev_won)}원/계약</span></div>
      <div class="ca-card-act">${esc(r.side)}${r.no_fut_trade ? ` <span class="st">선물 15:20 뒤 체결 없음</span>` : ""}</div>
      ${spark(r, 220, 44)}
      <div class="dim">평균 ${signedPct(r.mean_pct)} → 지금 ${signedPct(r.basis_pct)}</div></div>`).join("");
  const body = rows.map(r=>`<tr class="${r.level}" data-code="${esc(r.code)}">
      <td class="nm">${esc(r.name)}</td><td class="st">${esc(r.status)}</td>
      <td>${spark(r)}</td><td class="dim">${r.mean_pct==null ? "-" : signedPct(r.mean_pct)+` (${r.n}분)`}</td>${td(r.basis_pct, signedPct(r.basis_pct))}
      ${td(r.chg10_pct, signedPct(r.chg10_pct))}${td(r.dev_pct, signedPct(r.dev_pct))}${td(r.z, r.z==null ? "-" : r.z.toFixed(1))}${td(r.dev_won, n0(r.dev_won))}
      <td class="sep">${n0(r.spot)} <span class="dim">${r.spot_kind}</span></td>${td(r.jump_pct, signedPct(r.jump_pct))}
      ${td(r.imb_eok, r.imb_eok==null ? "-" : won(r.imb_eok))}<td>${n0(r.expected_qty)}</td><td>${n0(r.ask_qty)}</td><td>${n0(r.bid_qty)}</td>
      <td class="sep">${n0(r.fut)} <span class="dim">${r.fut_month.slice(4)}월</span></td>
      <td>${r.fut_vol_after_auction==null ? "-" : won(r.fut_vol_after_auction)}</td></tr>`).join("");
  box.innerHTML = `<div class="ca-cards">${basket("KOSPI","코스피200 바스켓")}${basket("KOSDAQ","코스닥150 바스켓")}</div>
    <div class="ca-hot-title">튄 종목 — 오늘 장중 베이시스 대비 z ${CA.z_hot} 이상 (${CA.phase})</div>
    <div class="ca-cards">${cards || `<div class="empty">지금 튄 종목 없음</div>`}</div>
    <div class="ca-sort">정렬 ${chip("cap","시총")}${chip("z","z")}${chip("chg10","10분 상승순")}${chip("dev","괴리")}${chip("won","원/계약")}${chip("jump","현물 튐")}${chip("imb","잔량 불균형")}</div>
    <table class="ca-tab"><thead>
      <tr><th class="nm" colspan="2"></th><th class="grp" colspan="7">베이시스 (선물−현물)/현물</th><th class="grp sep" colspan="6">KRX 현물</th><th class="grp sep" colspan="2">주식선물</th></tr>
      <tr><th class="nm">종목</th><th class="nm">상태</th><th>오늘 추이 (점선 평균 · 점 지금)</th><th>장중 평균(표본)</th><th>지금</th><th>10분 전 대비</th><th>괴리</th><th>z</th><th>원/계약</th>
        <th class="sep">기준가</th><th>15:20 대비</th><th>잔량 불균형(억)</th><th>예상체결량</th><th>매도잔량</th><th>매수잔량</th>
        <th class="sep">현재가</th><th>15:20 뒤 체결</th></tr></thead>
    <tbody>${body || `<tr><td colspan="17" class="empty">조건에 맞는 종목이 없습니다.</td></tr>`}</tbody></table>`;
}
document.getElementById("closearbView").addEventListener("click", e=>{
  const s = e.target.closest("[data-casort]");
  if(s){ CA_SORT = s.dataset.casort; renderClosearb(); }
});
document.getElementById("closearbView").addEventListener("dblclick", e=>{   // 차트는 더블클릭
  const r = e.target.closest("[data-code]");
  if(r) openChart(r.dataset.code);
});
