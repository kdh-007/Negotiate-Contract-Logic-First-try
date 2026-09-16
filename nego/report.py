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
    "첨부파일 자격확인",
    "참가가능지역",
    "첨부파일수",
    "공고번호",
    "차수",
    "원문URL",
]


def _fmt_money(value: float | None) -> str:
    if not value:
        return ""
    if value >= 100_000_000:
        return f"{value / 100_000_000:.1f}억"
    return f"{value:,.0f}원"


def _fmt_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


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
        "첨부파일 자격확인": c.qualification_note or "",
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
        if c.qualification_note:
            lines.append(f"     {c.qualification_note}")
        if c.qualification.missing_groups:
            names = c.qualification.missing_groups[0].allowed_names[:3]
            lines.append(f"     미충족 그룹 예: {', '.join(names)}")
        lines.append("")

    return "\n".join(lines)


_HTML_HEAD = """<meta charset="utf-8">
<title>협상에 의한 계약 — 검토 후보</title>
<style>
  :root { --bg:#fbfbfa; --fg:#1f1e1c; --muted:#6b6a66; --line:#e3e1dc; --accent:#8a5a2b; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font-family:-apple-system,'Segoe UI','Malgun Gothic',sans-serif; line-height:1.55; }
  .wrap { max-width:1100px; margin:0 auto; padding:32px 20px 64px; }
  h1 { font-size:1.45rem; margin:0 0 4px; letter-spacing:-0.01em; }
  .sub { color:var(--muted); font-size:0.85rem; margin-bottom:24px; }
  .stats { background:#fff; border:1px solid var(--line); border-radius:10px;
           padding:14px 18px; margin-bottom:24px; font-size:0.85rem; color:var(--muted); }
  .stats b { color:var(--fg); }
  .warn { color:#9a3412; }
  .card { background:#fff; border:1px solid var(--line); border-radius:10px;
          padding:16px 18px; margin-bottom:12px; }
  .card h2 { font-size:1rem; margin:0 0 8px; font-weight:600; }
  .card h2 a { color:inherit; text-decoration:none; }
  .card h2 a:hover { color:var(--accent); text-decoration:underline; }
  .meta { display:flex; flex-wrap:wrap; gap:6px 14px; font-size:0.82rem; color:var(--muted); }
  .meta b { color:var(--fg); font-weight:600; }
  .tags { margin-top:10px; display:flex; flex-wrap:wrap; gap:6px; }
  .tag { font-size:0.72rem; padding:2px 9px; border-radius:999px;
         border:1px solid var(--line); background:#f6f5f2; color:var(--muted); }
  .tag.d { border-color:#e6c9a8; background:#fdf3e7; color:#8a5a2b; }
  .tag.s { border-color:#bfd8c4; background:#eef6f0; color:#2f6b45; }
  .tag.r { border-color:#c9d4e6; background:#eef2f8; color:#2f4f78; }
  .empty { text-align:center; color:var(--muted); padding:48px 0; }
  @media (max-width:520px) { .wrap { padding:20px 16px 48px; } }
</style>
"""


def render_html(candidates: list[Candidate], stats: RunStats, generated_at: datetime) -> str:
    def esc(text: str | None) -> str:
        return html.escape(str(text or ""))

    parts = [_HTML_HEAD, '<div class="wrap">']
    parts.append("<h1>협상에 의한 계약 — 검토 후보</h1>")
    parts.append(f'<div class="sub">{generated_at.strftime("%Y-%m-%d %H:%M")} 생성</div>')

    warn = ""
    if stats.failed_operations:
        warn += f'<br><span class="warn">⚠ 조회 실패: {esc(", ".join(stats.failed_operations))}</span>'
    if stats.license_error:
        warn += '<br><span class="warn">⚠ 면허제한정보 조회 실패 → 자격 게이트 미적용(fail-open)</span>'

    parts.append(
        '<div class="stats">'
        f"수집 <b>{stats.fetched}</b>건 → 협상 <b>{stats.negotiated}</b>건 → "
        f"업역 <b>{stats.screened_in}</b>건 → 후보 <b>{stats.candidates}</b>건<br>"
        f"취소공고 제외 {stats.cancelled} · 협상 아님 {stats.not_negotiated} · "
        f"이전 차수 {stats.old_ordinal} · 자격 미달 {stats.gate_excluded}"
        f"{warn}</div>"
    )

    if not candidates:
        parts.append('<div class="empty">조건에 맞는 공고가 없습니다.</div>')
    else:
        for c in candidates:
            earliest = c.schedule.earliest
            title = esc(c.notice.title)
            if c.notice.detail_url:
                title = f'<a href="{esc(c.notice.detail_url)}" target="_blank" rel="noopener">{title}</a>'

            tags = []
            if c.days_left is not None:
                tags.append(f'<span class="tag d">D-{c.days_left} {esc(earliest[0]) if earliest else ""}</span>')
            if c.screen_result.confidence == "강력추천":
                tags.append('<span class="tag s">강력추천</span>')
            if c.is_re_notice:
                tags.append('<span class="tag r">재공고 — 직전 회차 유찰</span>')
            if c.notice.bid_method and "직찰" in c.notice.bid_method:
                tags.append('<span class="tag">직찰(비전자)</span>')
            if not c.joint.allowed:
                tags.append('<span class="tag">공동수급 불허</span>')
            if c.qualification_note:
                cls = "s" if "확인됨" in c.qualification_note else "d"
                tags.append(f'<span class="tag {cls}">{esc(c.qualification_note)}</span>')

            parts.append('<div class="card">')
            parts.append(f"<h2>{title}</h2>")
            parts.append(
                '<div class="meta">'
                f"<span>{esc(c.notice.notice_institution)}</span>"
                f"<span>{esc(c.notice.work_type)}</span>"
                f"<span><b>{esc(_fmt_money(c.notice.budget))}</b></span>"
                f"<span>공동수급 {esc(c.joint.label)}</span>"
                f"<span>{esc(c.qualification.summary)}</span>"
                f"<span>첨부 {len(c.notice.attachments)}건</span>"
                "</div>"
            )
            if tags:
                parts.append(f'<div class="tags">{"".join(tags)}</div>')
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
