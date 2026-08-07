// 공용 유틸 — 공시캘린더(index)·수급분석기(flow)·섹터로테이션(sectors) 3개 페이지가 공유.
// 중복 로직은 여기 한 곳에만 두고 각 페이지는 <script src="common.js"> 로 불러 재사용한다.

// HTML 이스케이프 (텍스트·속성 공용, 작은따옴표까지 처리)
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// 종목 차트 열기 — 알파스퀘어, 항상 같은 'alphachart' 탭 재사용(종목만 교체, 창이 안 늘어남).
// 유효한 6자리 코드면 열고 true, 아니면 아무것도 안 하고 false 반환.
function openChart(code){
  if(!/^\d{6}$/.test(code || "")) return false;
  window.open("https://alphasquare.co.kr/home/stock-summary?code=" + code, "alphachart");
  return true;
}

// EventSource 공통 리스너 부착: reload=페이지 자동 새로고침 / openchart=토스트 클릭 시 차트 열기.
// 각 페이지는 자기 EventSource 를 만들고 onmessage 만 따로 정한 뒤 이 함수로 공통 리스너를 붙인다.
function bindChartSSE(es){
  es.addEventListener("reload", () => location.reload());
  es.addEventListener("openchart", ev => openChart(ev.data));
}
