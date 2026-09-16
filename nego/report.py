"""리포트 생성 — 콘솔 / CSV / HTML."""

from __future__ import annotations

import csv
import html
from datetime import datetime
from pathlib import Path

from .pipeline import Candidate, RunStats

CSV_COLUMNS = [
    "검토순서",
    "잔여일수",
    "실질마감",
    "마감종류",
    "공고명",
    "발주기관",
    "업무구분",
    "추정가격",
    "신뢰도",
    "협상유형",
    "재공고",
    "공동수급",
    "자격판정",
    "참가가능지역",
    "첨부파일수",
    "공고번호",
    "차수",
    "원문URL",
]


def _fmt_money(value: float | None) -> str:
    if not value:
        return ""
    return f"{value:,.0f}원"


def _fmt_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _fmt_kr_date(value: datetime | None) -> str:
    """'2026. 8. 24.' 형태 — 기존 수집 시스템 리포트와 같은 날짜 표기."""
    return f"{value.year}. {value.month}. {value.day}." if value else ""


def _fmt_kr_datetime(value: datetime | None) -> str:
    """'2026. 8. 31. AM 12:01:13' 형태 — 기존 수집 시스템 리포트와 같은 표기."""
    if not value:
        return ""
    hour = value.hour % 12 or 12
    ampm = "AM" if value.hour < 12 else "PM"
    return f"{_fmt_kr_date(value)} {ampm} {hour}:{value.minute:02d}:{value.second:02d}"


def _row(index: int, c: Candidate) -> dict[str, str]:
    earliest = c.schedule.earliest
    return {
        "검토순서": str(index),
        "잔여일수": "" if c.days_left is None else f"D-{c.days_left}" if c.days_left >= 0 else "마감",
        "실질마감": _fmt_dt(earliest[1]) if earliest else "",
        "마감종류": earliest[0] if earliest else "일정 미상",
        "공고명": c.notice.title,
        "발주기관": c.notice.notice_institution or "",
        "업무구분": c.notice.work_type,
        "추정가격": _fmt_money(c.notice.budget),
        "신뢰도": c.screen_result.confidence or "",
        "협상유형": c.variant or "",
        "재공고": "Y" if c.is_re_notice else "",
        "공동수급": c.joint.label,
        "자격판정": c.qualification.summary,
        "참가가능지역": ", ".join(c.regions),
        "첨부파일수": str(len(c.notice.attachments)),
        "공고번호": c.notice.notice_no,
        "차수": c.notice.notice_ord,
        "원문URL": c.notice.detail_url or "",
    }


def write_csv(candidates: list[Candidate], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for i, candidate in enumerate(candidates, start=1):
            writer.writerow(_row(i, candidate))
    return path


def render_console(candidates: list[Candidate], stats: RunStats) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("협상에 의한 계약 — 검토 후보")
    lines.append("=" * 78)
    lines.append(
        f"수집 {stats.fetched}건 → 협상 {stats.negotiated}건 → 업역 {stats.screened_in}건 → 후보 {stats.candidates}건"
    )
    lines.append(
        f"  (취소공고 제외 {stats.cancelled} / 협상 아님 {stats.not_negotiated} / "
        f"이전 차수 {stats.old_ordinal} / 자격 미달 {stats.gate_excluded})"
    )
    if stats.screened_out:
        detail = " · ".join(f"{k} {v}" for k, v in sorted(stats.screened_out.items()))
        lines.append(f"  업역 스크리닝 제외: {detail}")
    if stats.failed_operations:
        lines.append(f"  ⚠ 조회 실패: {', '.join(stats.failed_operations)}")
    if stats.license_error:
        lines.append("  ⚠ 면허제한정보 조회 실패 → 자격 게이트 미적용 (fail-open)")
    lines.append("")

    if not candidates:
        lines.append("조건에 맞는 공고가 없습니다.")
        return "\n".join(lines)

    for i, c in enumerate(candidates, start=1):
        flags = []
        if c.is_re_notice:
            flags.append("재공고")
        if c.notice.bid_method and "직찰" in c.notice.bid_method:
            flags.append("직찰(비전자)")

        earliest = c.schedule.earliest
        deadline = f"D-{c.days_left}" if c.days_left is not None else "일정 미상"
        deadline_kind = f" ({earliest[0]})" if earliest else ""

        lines.append(f"[{i:2d}] {c.notice.title}")
        lines.append(
            f"     {deadline}{deadline_kind}  ·  {c.notice.notice_institution or '발주기관 미상'}"
            f"  ·  {_fmt_money(c.notice.budget) or '금액 미상'}"
        )
        lines.append(
            f"     {c.screen_result.confidence}"
            f"  ·  공동수급: {c.joint.label}  ·  {c.qualification.summary}"
        )
        if flags:
            lines.append(f"     {' · '.join(flags)}")
        if c.qualification.missing_groups:
            names = c.qualification.missing_groups[0].allowed_names[:3]
            lines.append(f"     미충족 그룹 예: {', '.join(names)}")
        lines.append("")

    return "\n".join(lines)


_HTML_HEAD = """<meta charset="utf-8">
<title>나라장터 입찰 모니터링 주간 리포트</title>
<style>
  :root { --bg:#fbfbfa; --fg:#1f1e1c; --muted:#6b6a66; --line:#e3e1dc; --accent:#2554c7; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font-family:-apple-system,'Segoe UI','Malgun Gothic',sans-serif; line-height:1.55; }
  .wrap { max-width:1100px; margin:0 auto; padding:32px 20px 64px; }
  h1 { font-size:1.45rem; margin:0 0 6px; letter-spacing:-0.01em; }
  .sub { color:var(--muted); font-size:0.85rem; margin-bottom:20px; }
  .warn { color:#9a3412; font-size:0.85rem; display:block; margin-bottom:16px; }
  .section { font-size:1.05rem; font-weight:700; margin:28px 0 14px;
             padding-bottom:8px; border-bottom:2px solid var(--fg); }
  .card { background:#fff; border:1px solid var(--line); border-radius:10px;
          padding:16px 18px; margin-bottom:12px; }
  .badges { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px; }
  .badge { font-size:0.72rem; padding:2px 10px; border-radius:6px; font-weight:600;
           border:1px solid var(--line); background:#f6f5f2; color:var(--muted); }
  .badge.confidence-strong { border-color:#bfd8c4; background:#eef6f0; color:#2f6b45; }
  .card h2 { font-size:1.02rem; margin:0 0 10px; font-weight:700; }
  .card h2 a { color:var(--accent); text-decoration:none; }
  .card h2 a:hover { text-decoration:underline; }
  .fields { display:flex; flex-direction:column; gap:3px; font-size:0.86rem; color:var(--fg); }
  .fields .label { color:var(--muted); }
  .kwtags { margin-top:10px; display:flex; flex-wrap:wrap; gap:6px; }
  .kwtag { font-size:0.72rem; padding:2px 9px; border-radius:6px;
           border:1px solid #c9d4e6; background:#eef2f8; color:#2f4f78; }
  .empty { text-align:center; color:var(--muted); padding:48px 0; }
  @media (max-width:520px) { .wrap { padding:20px 16px 48px; } }
</style>
"""


def render_html(candidates: list[Candidate], stats: RunStats, generated_at: datetime) -> str:
    def esc(text: str | None) -> str:
        return html.escape(str(text or ""))

    parts = [_HTML_HEAD, '<div class="wrap">']
    parts.append("<h1>나라장터 입찰 모니터링 주간 리포트</h1>")

    period = ""
    if stats.period_begin and stats.period_end:
        period = f"조회 기간: {_fmt_kr_date(stats.period_begin)} ~ {_fmt_kr_date(stats.period_end)} · "
    parts.append(f'<div class="sub">{period}생성 시각: {_fmt_kr_datetime(generated_at)}</div>')

    if stats.failed_operations:
        parts.append(f'<span class="warn">⚠ 조회 실패: {esc(", ".join(stats.failed_operations))}</span>')
    if stats.license_error:
        parts.append('<span class="warn">⚠ 면허제한정보 조회 실패 → 자격 게이트 미적용(fail-open)</span>')

    parts.append(f'<div class="section">본공고 ({len(candidates)}건)</div>')

    if not candidates:
        parts.append('<div class="empty">조건에 맞는 공고가 없습니다.</div>')
    else:
        for c in candidates:
            earliest = c.schedule.earliest
            title = esc(c.notice.title)
            if c.notice.detail_url:
                title = f'<a href="{esc(c.notice.detail_url)}" target="_blank" rel="noopener">{title}</a>'

            confidence_cls = " confidence-strong" if c.screen_result.confidence == "강력추천" else ""

            parts.append('<div class="card">')
            parts.append(
                '<div class="badges">'
                f'<span class="badge{confidence_cls}">{esc(c.screen_result.confidence)}</span>'
                f'<span class="badge">[{esc(c.notice.work_type)}]</span>'
                "</div>"
            )
            parts.append(f"<h2>{title}</h2>")
            parts.append(
                '<div class="fields">'
                f'<div><span class="label">기관:</span> {esc(c.notice.notice_institution) or "미상"}</div>'
                f'<div><span class="label">예산:</span> {esc(_fmt_money(c.notice.budget)) or "미상"}</div>'
                f'<div><span class="label">마감/일정:</span> {esc(_fmt_dt(earliest[1])) if earliest else "일정 미상"}</div>'
                f'<div><span class="label">자격판정:</span> {esc(c.qualification.summary)}</div>'
                f'<div><span class="label">공고번호:</span> {esc(c.notice.notice_no)}</div>'
                "</div>"
            )

            if c.screen_result.matched_keywords:
                kwtags = "".join(f'<span class="kwtag">키워드:{esc(k)}</span>' for k in c.screen_result.matched_keywords)
                parts.append(f'<div class="kwtags">{kwtags}</div>')

            parts.append("</div>")

    parts.append("</div>")
    return "\n".join(parts)


def save_reports(
    candidates: list[Candidate], stats: RunStats, output_dir: Path, generated_at: datetime
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = generated_at.strftime("%Y%m%d_%H%M")

    csv_path = write_csv(candidates, output_dir / f"협상공고_{stamp}.csv")
    html_path = output_dir / f"협상공고_{stamp}.html"
    html_path.write_text(render_html(candidates, stats, generated_at), encoding="utf-8")

    return {"csv": csv_path, "html": html_path}
