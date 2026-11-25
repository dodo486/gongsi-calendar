// 🏭 인더스트리 액션 탭 — data/out/industry_action.json (derive/industry.py)
tab("industry", {label: "🏭 인더스트리 액션", css: true, files: ["industry_action.json"],
                 load: () => loadIndustry(), render: () => renderIndustry()});

let IA = null, IA_SORT = "value", IA_SEL = null;   // data/out/industry_action.json
async function loadIndustry(){
  try{ IA = await (await fetch("data/out/industry_action.json?_="+Date.now())).json(); }catch(_){ IA = null; }
  document.getElementById("meta").textContent = IA
    ? `🏭 묶음 ${IA.groups.length}개 · 갱신 ${IA.generated_at} · 시세 ${IA.source} · 묶은 날 ${IA.groups_built_at.slice(0,10)} (일봉 ${IA.window[0]}~${IA.window[1]})`
    : "인더스트리 데이터 없음";
  renderIndustry();
}
function iaGroups(){
  const q = (filters.q||"").trim();
  let gs = IA.groups.filter(g=> !q || g.name.includes(q) || g.themes.some(t=>t.includes(q)) || g.members.some(m=>m.name.includes(q)));
  const key = {value:g=>-g.value, up:g=>-g.rate, down:g=>g.rate}[IA_SORT];
  return gs.slice().sort((a,b)=>key(a)-key(b));
}
function renderIndustry(){
  const box = document.getElementById("industryView");
  if(!IA){ box.innerHTML = `<div class="empty">인더스트리 데이터가 없습니다 — python -m derive.industry 실행 후 다시 여세요.</div>`; return; }
  const gs = iaGroups();
  if(!IA_SEL || !gs.some(g=>g.name===IA_SEL)) IA_SEL = gs.length ? gs[0].name : null;
  const vmax = Math.max(1, ...IA.groups.map(g=>g.value));
  const chip = (k,t)=>`<span class="chip${IA_SORT===k?" on":""}" data-sort="${k}">${t}</span>`;
  const rows = gs.map(g=>`<div class="ia-row${g.name===IA_SEL?" sel":""}" data-g="${esc(g.name)}">
      <span class="bar" style="width:${(g.value/vmax*100).toFixed(1)}%"></span>
      <span title="${esc(g.themes.join(", "))}">${esc(g.name)}</span>
      <span class="num">${won(Math.round(g.value/1e8))}억</span>
      <span class="num ${sgn(g.rate)}">${signedPct(g.rate)}</span>
      <span class="num">${g.up}/${g.n}</span>
      <span class="ld">${esc(g.lead.name)} <span class="${sgn(g.lead.rate)}">${signedPct(g.lead.rate)}</span></span></div>`).join("");
  box.innerHTML = `<div class="ia-wrap"><div>
      <div class="ia-sort">정렬 ${chip("value","거래대금")}${chip("up","강한 순")}${chip("down","약한 순")}</div>
      <div class="ia-row head"><span>묶음</span><span class="num">거래대금</span><span class="num">평균 등락</span><span class="num">상승</span><span class="ld">대장(거래대금 1위)</span></div>
      ${rows || `<div class="empty">조건에 맞는 묶음이 없습니다.</div>`}</div>
    <div class="ia-detail" id="iaDetail"></div></div>`;
  renderIndustryDetail();
}
function renderIndustryDetail(){
  const box = document.getElementById("iaDetail");
  const g = IA && IA.groups.find(x=>x.name===IA_SEL);
  if(!box) return;
  if(!g){ box.innerHTML = `<div class="empty">묶음을 고르세요.</div>`; return; }
  const ms = g.members.filter(m=>filters[m.market] !== false);
  box.innerHTML = `<h3>${esc(g.name)}</h3>
    <div class="ia-sub">${won(Math.round(g.value/1e8))}억 · 평균 ${signedPct(g.rate)} · 상승 ${g.up}/${g.n} · 합쳐진 테마 ${g.themes.length}개</div>
    <div class="ia-themes">${g.subs.map(s=>`<span title="${s.n}종목 · 대장 ${esc(s.lead.name)}">${esc(s.name)} <b class="${sgn(s.rate)}">${signedPct(s.rate)}</b></span>`).join("")}</div>
    ${ms.map(m=>`<div class="ia-m" data-code="${esc(m.code)}"><span>${esc(m.name)}</span>
      <span class="num ${sgn(m.rate)}">${signedPct(m.rate)}</span><span class="num v">${won(Math.round((m.value||0)/1e8))}억</span></div>`).join("")}`;
}
document.getElementById("industryView").addEventListener("click", e=>{
  const s = e.target.closest("[data-sort]");
  if(s){ IA_SORT = s.dataset.sort; IA_SEL = null; renderIndustry(); return; }
  const m = e.target.closest(".ia-m");
  if(m){ openChart(m.dataset.code); return; }
  const r = e.target.closest(".ia-row[data-g]");
  if(r){ IA_SEL = r.dataset.g; document.querySelectorAll(".ia-row.sel").forEach(x=>x.classList.remove("sel")); r.classList.add("sel"); renderIndustryDetail(); }
});
