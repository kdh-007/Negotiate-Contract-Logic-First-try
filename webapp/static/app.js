"use strict";
// 입찰 공고 싱크로율 탐색기 — 화면 스크립트. 서버(webapp/server.py) JSON API만 부른다.

const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d = null) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* 저장 불가여도 동작 */ } },
};

// ── 접속 토큰(?t=) — 서버를 사내망에 열었을 때만 쓰인다 ──
const urlToken = new URLSearchParams(location.search).get("t");
if (urlToken) store.set("token", urlToken);
const token = urlToken || store.get("token") || "";

async function api(path, body) {
  const opt = { headers: { "X-App-Token": token } };
  if (body !== undefined) { opt.method = "POST"; opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

// ── 테마 ──
function applyTheme(mode) {
  if (mode === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", mode);
  document.querySelectorAll("[data-theme-set]").forEach((b) => b.classList.toggle("on", b.dataset.themeSet === mode));
  store.set("theme", mode);
}
document.querySelectorAll("[data-theme-set]").forEach((b) => b.addEventListener("click", () => applyTheme(b.dataset.themeSet)));
applyTheme(store.get("theme", "system"));

// ── 내 이름 ──
const nameInput = $("#myName");
nameInput.value = store.get("myName", "");
nameInput.addEventListener("input", () => { store.set("myName", nameInput.value.trim()); render(); });
const myName = () => nameInput.value.trim();
function needName() {
  if (myName()) return false;
  alert("상단 '내 이름'을 먼저 입력하세요.");
  nameInput.focus();
  return true;
}

// ── 상태 ──
let META = null, RUN = null, STATES = {}, COUNTS = {}, PAST = null;
const F = {
  conf: store.get("f.conf", "전체"),
  kind: "본공고+사전규격",
  cat: new Set(JSON.parse(store.get("f.cat", "[]"))),
  part: new Set(),
  ai: new Set(),
  sync: new Set(),
};

// ── 탭 ──
document.querySelectorAll(".tabs [data-tab]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  document.querySelectorAll(".tabs [data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((s) => { s.hidden = s.id !== `tab-${name}`; });
  store.set("tab", name);
  if (name === "past") loadPast();
}

// ── 필터 칩 ──
function chipRow(label, key, options, multi, disabled = {}) {
  const row = document.createElement("div");
  row.className = "frow";
  row.innerHTML = `<span class="lbl">${esc(label)}</span>`;
  for (const opt of options) {
    const b = document.createElement("button");
    b.className = "chip";
    b.textContent = opt;
    const on = multi ? F[key].has(opt) : F[key] === opt;
    b.classList.toggle("on", on);
    if (disabled[opt]) { b.disabled = true; b.title = disabled[opt]; }
    b.addEventListener("click", () => {
      if (multi) { F[key].has(opt) ? F[key].delete(opt) : F[key].add(opt); }
      else F[key] = opt;
      if (key === "conf") store.set("f.conf", F.conf);
      if (key === "cat") store.set("f.cat", JSON.stringify([...F.cat]));
      render();
    });
    row.appendChild(b);
  }
  return row;
}
function renderFilters() {
  const box = $("#filters");
  box.replaceChildren(
    chipRow("추천", "conf", ["전체", "강력추천", "참고용"], false),
    chipRow("공고", "kind", ["본공고+사전규격", "본공고", "사전규격"], false),
    chipRow("낙찰방법", "cat", META ? META.categories : ["협상", "규격가격동시입찰", "입찰"], true),
    chipRow("참가여부", "part", ["미지정", ...(META ? META.statuses : [])], true),
    chipRow("AI 판단", "ai", ["적합", "검토필요", "부적합", "미판정"], true),
    chipRow("싱크로율", "sync", ["높음", "경계선", "낮음", "판정 불가"], true),
  );
}

function stateOf(c) { return STATES[c.key] || {}; }
function passes(c) {
  if (F.conf !== "전체" && c.confidence !== F.conf) return false;
  if (F.kind !== "본공고+사전규격" && c.kind !== F.kind) return false;
  if (F.cat.size && !F.cat.has(c.category)) return false;
  if (F.part.size && !F.part.has(stateOf(c).status || "미지정")) return false;
  if (F.ai.size && !F.ai.has(c.ai ? c.ai.label : "미판정")) return false;
  if (F.sync.size && !F.sync.has(c.sync.level)) return false;
  return true;
}

// ── 표시 도우미 ──
const won = (v) => v == null ? "금액 정보 없음" : (v >= 1e8 ? `${(v / 1e8).toFixed(v >= 1e9 ? 1 : 2).replace(/\.?0+$/, "")}억원` : `${Math.round(v / 1e4).toLocaleString()}만원`);
const fmtDt = (s) => s ? s.replace("T", " ").slice(0, 16) : "일정 미상";
const pct = (s) => s == null ? "–" : `${Math.round(s * 100)}%`;
function dday(c) {
  if (c.days_left == null) return `<span class="b" title="마감 정보 없음">일정 미상</span>`;
  const t = c.days_left < 0 ? "마감" : c.days_left === 0 ? "D-day" : `D-${c.days_left}`;
  return `<span class="b dday${c.days_left > 7 ? " far" : ""}" title="${esc(c.deadline_label)} ${esc(fmtDt(c.deadline))}">마감 ${t}</span>`;
}
const QUAL_NOTE = "이름 뒤 괄호 숫자는 세부품명번호·업종코드입니다. 코드가 없는 항목은 면허제한정보 API에 코드 필드 자체가 없어 이름만 표시됩니다.";
function tipList(title, cls, names) {
  if (!names.length) return "";
  return `<div class="tip-title ${cls}">${esc(title)} ${names.length}건</div>` + names.map((n) => `<div class="tip-item">${esc(n)}</div>`).join("");
}
function qualBadge(q) {
  // 커서를 대거나(포커스·탭 포함) 누르면 미보유·충족 자격 목록 팝업
  let label, cls, body;
  if (!q.checked) {
    label = "자격 미확인"; cls = "";
    body = `<div class="tip-title">자격정보 미확인 (통과)</div><div class="tip-note">API·첨부파일 모두 판정 근거가 없어 통과 처리됩니다.</div>`;
  } else {
    // 미보유 그룹이 하나라도 있으면 "미달" — q.passes는 미보유 1개까지 통과시키는 옛 게이트 값이라 쓰지 않는다
    const ok = q.satisfied === q.total;
    label = `자격 ${q.satisfied}/${q.total} ${ok ? "충족" : "미달"}`;
    cls = ok ? "good" : "bad";
    body = tipList("미보유 자격", "bad", q.missing) + tipList("충족된 자격", "good", q.satisfied_names);
    if (!body) body = `<div class="tip-title ${cls}">${ok ? "자격 충족" : "자격 미달"}</div>`;
    body += `<div class="tip-note">${esc(QUAL_NOTE)}</div>`;
  }
  return `<button type="button" class="b qual ${cls}" aria-label="${esc(q.summary)}">${esc(label)}<span class="tip" role="tooltip">${body}</span></button>`;
}
function badges(c) {
  const out = [dday(c)];
  if (c.confidence === "강력추천") out.push(`<span class="b star">강력추천</span>`);
  else if (c.confidence) out.push(`<span class="b">${esc(c.confidence)}</span>`);
  out.push(`<span class="b">${esc(c.work_type)}</span>`);
  if (c.kind === "사전규격") {
    out.push(`<span class="b prespec" title="입찰공고 전 규격 공개 단계 — 의견등록 마감까지 규격 의견을 낼 수 있습니다">사전규격</span>`);
    if (c.linked_bid_notices && c.linked_bid_notices.length)
      out.push(`<span class="b good" title="${esc(c.linked_bid_notices.join(", "))}">본공고 게시됨</span>`);
  } else if (c.category) out.push(`<span class="b info" title="${esc(c.award_method || "")}">${esc(c.category)}</span>`);
  if (c.is_re_notice) out.push(`<span class="b warn">재공고</span>`);
  if (c.ai) out.push(`<span class="b ${c.ai.label === "적합" ? "good" : c.ai.label === "부적합" ? "bad" : "warn"}" title="${esc(c.ai.reason)}">AI ${esc(c.ai.label)}</span>`);
  return out.join("");
}
function badges2(c) {
  const out = [qualBadge(c.qualification)];
  // 사전규격엔 참가가능지역·공동수급 정보가 없다 — "제한 없음"으로 오해하지 않게 아예 표시하지 않는다
  if (c.kind === "사전규격") return out.join("");
  out.push(c.regions.length
    ? `<span class="b warn" title="${esc(c.regions.join(", "))}">지역제한 ${esc(c.regions.slice(0, 2).join("·"))}${c.regions.length > 2 ? " 외" : ""}</span>`
    : `<span class="b" title="참가가능지역 정보 없음 = 제한 없음 또는 미등록">지역제한 없음</span>`);
  out.push(`<span class="b ${c.joint.allowed ? "info" : ""}">공동수급 ${esc(c.joint.label)}</span>`);
  return out.join("");
}

function card(c, withActions = true) {
  const st = stateOf(c);
  const el = document.createElement("article");
  el.className = "card" + (st.assignee && st.assignee === myName() ? " mine" : "");
  const s = c.sync;
  el.innerHTML = `
    <div class="badges">${badges(c)}</div>
    <div class="badges">${badges2(c)}</div>
    <div class="title">${c.detail_url ? `<a href="${esc(c.detail_url)}" target="_blank" rel="noopener">${esc(c.title)}</a>` : esc(c.title)}</div>
    <dl class="meta">
      <dt>${c.kind === "사전규격" ? "의견 마감" : "입찰 마감"}</dt><dd>${esc(fmtDt(c.deadline))}${c.deadline_label && !["입찰 마감", "의견등록 마감"].includes(c.deadline_label) ? ` <span class="why">(${esc(c.deadline_label)})</span>` : ""}</dd>
      <dt>수요기관</dt><dd>${esc(c.demand_institution || "-")}</dd>
      <dt>사업금액</dt><dd>${esc(won(c.budget))}</dd>
      ${c.excluded_reason ? `<dt>제외 사유</dt><dd>${esc(c.excluded_reason)}</dd>` : ""}
    </dl>
    <div class="syncrow lv-${esc(s.level)}" title="눌러서 비슷한 과거 실적 보기">
      <span class="why">싱크로율</span>
      <span class="bar"><i class="lv-${esc(s.level)}" style="width:${Math.round((s.score || 0) * 100)}%"></i></span>
      <span class="pct">${pct(s.score)}</span>
    </div>
    <div class="why">${esc(s.level)} · 근거: ${esc(s.basis)}${s.top[0] ? ` · 최다 유사: (${esc(s.top[0].year)}) ${esc(s.top[0].title)}` : ""}</div>`;
  $(".syncrow", el).addEventListener("click", () => openDetail(c));
  if (withActions) {
    const part = document.createElement("div");
    part.className = "part";
    for (const status of META.statuses) {
      const b = document.createElement("button");
      b.dataset.s = status;
      b.textContent = status;
      b.classList.toggle("on", st.status === status);
      b.addEventListener("click", () => setState(c.key, st.status === status ? { clear_status: true } : { status }));
      part.appendChild(b);
    }
    const foot = document.createElement("div");
    foot.className = "foot";
    const mine = st.assignee && st.assignee === myName();
    foot.innerHTML = `<span>담당: <b>${esc(st.assignee || "없음")}</b>${st.updated_by ? ` · ${esc(st.updated_by)} ${esc(fmtDt(st.updated_at))}` : ""}</span>`;
    const take = document.createElement("button");
    take.textContent = mine ? "담당 해제" : "내가 맡기";
    take.addEventListener("click", () => setState(c.key, mine ? { release: true } : { take: true }));
    const talk = document.createElement("button");
    talk.textContent = `대화 ${COUNTS[c.key] || 0}건`;
    talk.addEventListener("click", () => openThread(c));
    const right = document.createElement("span");
    right.append(take, " ", talk);
    foot.appendChild(right);
    el.append(part, foot);
  }
  return el;
}

function grid(list, withActions) {
  const g = document.createElement("div");
  g.className = "grid";
  list.forEach((c) => g.appendChild(card(c, withActions)));
  return g;
}
function emptyBox(text) {
  const d = document.createElement("div");
  d.className = "empty";
  d.textContent = text;
  return d;
}

// ── 렌더 ──
function render() {
  if (!META) return;
  renderFilters();
  const all = RUN ? RUN.candidates : [];
  const shown = all.filter(passes);
  const lv = (l) => shown.filter((c) => c.sync.level === l).length;
  const strong = shown.filter((c) => c.confidence === "강력추천").length;
  $("#summary").innerHTML = RUN
    ? `후보 <b>${all.length}</b>건 중 <b>${shown.length}</b>건 표시 · 강력추천 ${strong} · 싱크로율 높음 <b>${lv("높음")}</b> / 경계선 ${lv("경계선")} / 낮음 ${lv("낮음")}`
      + ` · 조회 ${esc(fmtDt(RUN.stats.period_begin))} ~ ${esc(fmtDt(RUN.stats.period_end))} · 수집 ${esc(fmtDt(RUN.finished_at))}`
      + (RUN.params.attachments ? "" : " · <span title='첨부 참가자격 미반영'>첨부 자격판정 안 함</span>")
      + (RUN.stats.license_error ? ` · <span class="job err">면허제한정보 조회 실패</span>` : "")
      + (RUN.stats.prespec_requested ? ` · 사전규격 ${RUN.stats.prespec_fetched}건 수집` : " · 사전규격 미수집")
      + (RUN.stats.prespec_error ? ` · <span class="job err" title="${esc(RUN.stats.prespec_error)}">사전규격 조회 실패 (공공데이터포털 활용신청 확인)</span>` : "")
    : "아직 수집 결과가 없습니다. 기간을 고르고 '나라장터에서 불러오기'를 누르세요.";
  const live = $("#liveList");
  if (!RUN) live.replaceChildren();
  else if (!shown.length) live.replaceChildren(emptyBox("조건에 맞는 공고가 없습니다."));
  else {
    const parts = [];
    for (const kind of ["본공고", "사전규격"]) {
      const list = shown.filter((c) => c.kind === kind);
      if (!list.length) continue;
      if (parts.length) {
        const hr = document.createElement("hr");
        hr.className = "section-divider";
        parts.push(hr);
      }
      const h = document.createElement("h2");
      h.className = "section";
      h.innerHTML = `${kind} ${list.length}건<small>강력추천 ${list.filter((c) => c.confidence === "강력추천").length}건</small>`;
      parts.push(h, grid(list, true));
    }
    live.replaceChildren(...parts);
  }

  const rej = RUN ? RUN.rejected : [];
  const reasons = {};
  rej.forEach((c) => { const r = (c.excluded_reason || "").split(" (")[0]; reasons[r] = (reasons[r] || 0) + 1; });
  $("#rejSummary").textContent = RUN
    ? `수집 범위(수의계약 제외) ${RUN.stats.in_scope}건 중 필터에서 빠진 ${rej.length}건 — ` + Object.entries(reasons).map(([k, v]) => `${k} ${v}`).join(" · ")
    : "수집 결과가 없습니다.";
  $("#rejList").replaceChildren(rej.length ? grid(rej, false) : emptyBox("제외된 공고가 없습니다."));
  renderSearch();
}

function renderSearch() {
  const q = $("#qText").value.trim().toLowerCase();
  const box = $("#searchList");
  if (!q) { box.replaceChildren(); return; }
  const pool = RUN ? [...RUN.candidates, ...RUN.rejected] : [];
  const hit = pool.filter((c) => [c.title, c.demand_institution, c.notice_institution, c.notice_no].some((v) => (v || "").toLowerCase().includes(q)));
  box.replaceChildren(hit.length ? grid(hit.slice(0, 60), true) : emptyBox("수집된 공고 중 일치하는 공고가 없습니다."));
}
$("#qText").addEventListener("input", renderSearch);

// ── 서버 호출 ──
async function loadResults() {
  const data = await api("/api/results");
  RUN = data.run; STATES = data.states; COUNTS = data.comment_counts;
  showJob(data.job);
  render();
}
async function setState(key, patch) {
  if (needName()) return;
  try {
    const r = await api("/api/state", { key, name: myName(), ...patch });
    STATES[key] = r.state;
    render();
  } catch (e) { alert(e.message); }
}

let pollTimer = null;
function showJob(job) {
  const st = $("#jobStatus"), lg = $("#jobLog"), btn = $("#btnCollect");
  btn.disabled = job.running;
  st.classList.toggle("err", !!job.error);
  if (job.running) st.textContent = `수집 중… (${fmtDt(job.started_at)} 시작, 최근 ${job.params.days}일)`;
  else if (job.error) st.textContent = `실패: ${job.error}`;
  else if (job.finished_at) st.textContent = `완료 ${fmtDt(job.finished_at)}`;
  else st.textContent = "";
  lg.hidden = !(job.running || job.error);
  lg.textContent = job.log.join("\n");
  lg.scrollTop = lg.scrollHeight;
  if (job.running && !pollTimer) pollTimer = setInterval(pollJob, 2000);
  if (!job.running && pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}
async function pollJob() {
  const job = await api("/api/job").catch(() => null);
  if (!job) return;
  showJob(job);
  if (!job.running) loadResults();
}
$("#btnCollect").addEventListener("click", async () => {
  if (needName()) return;
  try {
    const job = await api("/api/collect", {
      days: Number($("#period").value), attachments: $("#optAttach").checked, ai: $("#optAi").checked,
      prespec: $("#optPrespec").checked,
    });
    showJob(job);
  } catch (e) { alert(e.message); }
});
$("#btnReload").addEventListener("click", () => loadResults().catch((e) => alert(e.message)));

// ── 대화 ──
let threadKey = null;
async function openThread(c) {
  threadKey = c.key;
  $("#threadTitle").textContent = c.title;
  $("#thread").showModal();
  await loadThread();
}
async function loadThread(data) {
  const list = data || (await api(`/api/comments?key=${encodeURIComponent(threadKey)}`)).comments;
  COUNTS[threadKey] = list.length;
  const body = $("#threadBody");
  body.replaceChildren(...(list.length ? list.map((m) => {
    const d = document.createElement("div");
    d.className = "msg";
    d.innerHTML = `<span class="who">${esc(m.author)}</span><span class="when">${esc(fmtDt(m.created_at))}</span><div class="txt">${esc(m.body)}</div>`;
    return d;
  }) : [emptyBox("아직 대화가 없습니다.")]));
  body.scrollTop = body.scrollHeight;
}
async function sendThread() {
  const text = $("#threadInput").value.trim();
  if (!text || needName()) return;
  try {
    const r = await api("/api/comments", { key: threadKey, name: myName(), body: text });
    $("#threadInput").value = "";
    await loadThread(r.comments);
  } catch (e) { alert(e.message); }
}
$("#threadSend").addEventListener("click", sendThread);
$("#threadInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) sendThread(); });
$("#thread").addEventListener("close", render);

// ── 싱크로율 근거 ──
function syncDetailHtml(s) {
  const tags = Object.entries(s.tags || {}).map(([f, ts]) => `<div><span class="why">${esc(f)}</span><div class="tags">${ts.map((t) => `<span>${esc(t)}</span>`).join("")}</div></div>`).join("");
  const top = (s.top || []).map((r) => `<li><span class="sc">${pct(r.score)}</span>(${esc(r.year)}) ${esc(r.title)}<div class="tags">${r.shared.map((t) => `<span>${esc(t)}</span>`).join("")}</div></li>`).join("");
  return `<p>싱크로율 <b class="lv-${esc(s.level)}">${pct(s.score)} (${esc(s.level)})</b> — 근거: ${esc(s.basis)}`
    + `${s.basis === "공고명만" ? " <span class='why'>(첨부 원문 없이 공고명만으로 계산해 정확도가 낮습니다)</span>" : ""}</p>`
    + `<h3>비슷한 과거 실적</h3><ul class="toplist">${top || "<li>없음</li>"}</ul>`
    + `<h3>이 공고의 과업 프로필</h3>${tags || "<p class='why'>추출된 항목 없음</p>"}`;
}
function openDetail(c) {
  $("#detailTitle").textContent = c.title;
  const ai = c.ai ? `<p><b>AI 판단: ${esc(c.ai.label)}</b> (${esc(c.ai.overall)})${c.ai.best ? ` · 가장 비슷: ${esc(c.ai.best)}` : ""}<br><span class="why">${esc(c.ai.reason)}</span></p>` : "";
  const q = c.qualification;
  const qual = `<p><b>자격</b>: ${esc(q.summary)}${q.satisfied_names.length ? `<br><span class="why">충족: ${esc(q.satisfied_names.join(", "))}</span>` : ""}</p>`;
  $("#detailBody").innerHTML = qual + ai + syncDetailHtml(c.sync);
  $("#detail").showModal();
}

// ── 직접 계산 ──
$("#btnMatch").addEventListener("click", async () => {
  try {
    const s = await api("/api/match", { title: $("#mTitle").value, text: $("#mText").value });
    $("#matchResult").innerHTML = syncDetailHtml(s);
  } catch (e) { $("#matchResult").textContent = e.message; }
});

// ── 과거사업 목록 ──
async function loadPast() {
  if (!PAST) {
    const d = await api("/api/past");
    PAST = d.projects;
    if (d.error) { $("#pastList").replaceChildren(emptyBox(d.error)); return; }
    const years = [...new Set(PAST.map((p) => p.year))].sort();
    $("#pastYear").append(...years.map((y) => new Option(`${y}년`, y)));
  }
  renderPast();
}
function renderPast() {
  if (!PAST) return;
  const q = $("#pastQ").value.trim().toLowerCase(), y = $("#pastYear").value;
  const rows = PAST.filter((p) => (!y || String(p.year) === y) && (!q || [p.title, p.overview, p.exhibition].some((v) => (v || "").toLowerCase().includes(q))));
  $("#pastList").replaceChildren(...(rows.length ? rows.map((p) => {
    const d = document.createElement("div");
    d.className = "pastrow";
    d.innerHTML = `<div><span class="y">${esc(p.year)}</span><b>${esc(p.title)}</b></div>`
      + (p.overview ? `<p><span class="lab">과업</span> ${esc(p.overview)}</p>` : "")
      + (p.exhibition ? `<p><span class="lab">전시내용</span> ${esc(p.exhibition)}</p>` : "");
    return d;
  }) : [emptyBox("일치하는 과거 사업이 없습니다.")]));
}
$("#pastQ").addEventListener("input", renderPast);
$("#pastYear").addEventListener("change", renderPast);

// ── 시작 ──
(async function init() {
  try {
    META = await api("/api/meta");
  } catch (e) {
    $("#summary").textContent = e.message;
    return;
  }
  const years = Object.entries(META.past_years).sort().map(([y, n]) => `${String(y).slice(2)}년 ${n}`).join(" · ");
  $("#pastInfo").textContent = META.past_error ? META.past_error : `과거 실적 ${META.past_count}건 (${years})`;
  const period = $("#period");
  META.periods.forEach((d) => period.append(new Option(`최근 ${d}일`, d)));
  period.value = store.get("period", "7");
  period.addEventListener("change", () => store.set("period", period.value));
  if (!META.has_ai_key) { $("#optAi").disabled = true; $("#optAiWrap").title = "서버에 ANTHROPIC_API_KEY가 없어 AI 판단을 쓸 수 없습니다"; }
  if (!META.has_service_key) $("#jobStatus").textContent = "서버에 NARA_SERVICE_KEY가 없어 불러오기가 실패합니다";
  showTab(store.get("tab", "live"));
  await loadResults();
  setInterval(() => { if (!pollTimer && !document.hidden && !$("#thread").open) loadResults().catch(() => {}); }, 60000);
})();
