// 공용 유틸 — 공시캘린더(index.html)·배당(dividend/) 이 같이 쓴다.

// HTML 이스케이프 (텍스트·속성 공용, 작은따옴표까지 처리)
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const won = n => Number(n).toLocaleString("ko-KR");
const pctTxt = v => (v===null||v===undefined) ? "-" : `${v>0?"+":""}${v}%`;
const signedPct = v => v==null ? "-" : (v>0?"+":"")+v.toFixed(2)+"%";   // +1.23% (core.fmt.signed_pct 와 같은 모양)
const sgn = v => v>0 ? "pos" : v<0 ? "neg" : "";   // 등락 색 클래스
function toISO(d){ return d.includes("-") ? d : `${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)}`; }
const dISO = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;

// 자동 갱신으로 다시 그려도 보던 스크롤 위치 유지 — 다시 그리기 전 모든 스크롤 영역 위치를 저장했다 되돌림
function keepScroll(load){
  const els = [document.scrollingElement, ...document.querySelectorAll("*")].filter(el=> el.scrollTop > 0);
  const ys = els.map(el=> el.scrollTop);
  return load().catch(console.error).finally(()=> els.forEach((el,i)=> el.scrollTop = ys[i]));
}

// 종목 차트 열기 — 알파스퀘어, 항상 같은 'alphachart' 탭 재사용(종목만 교체, 창이 안 늘어남).
// 유효한 6자리 코드면 열고 true, 아니면 아무것도 안 하고 false 반환.
function openChart(code){
  if(!/^\d{6}$/.test(code || "")) return false;
  window.open("https://alphasquare.co.kr/home/stock-summary?code=" + code, "alphachart");
  return true;
}
