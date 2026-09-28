"""수집 결과를 텔레그램 봇으로 발송한다.

"수집 완료" 알림이 아니라 **보고서 형태**(후보 목록·발주기관·금액·마감)로 보낸다
(2026-09 결정 — 카카오 알림톡은 채널 등록·템플릿 심사·건당 비용이 들어 보류).

  - 본문: 후보 공고 목록. 텔레그램 메시지 한 건은 4096자까지라 넘치면 나눠 보낸다.
  - 첨부: 전체 HTML 리포트 파일 (PC·핸드폰에서 눌러 바로 열림).
  - 후보가 0건이어도 "새 공고 없음"을 보낸다 — 매일 받는 사람이 "시스템이 돌았는지"를
    알 수 있게.

설정 (환경변수):
  TELEGRAM_BOT_TOKEN  @BotFather가 발급한 봇 토큰
  TELEGRAM_CHAT_ID    받을 대화방 ID. 여러 곳이면 쉼표로 구분 (개인 대화, 단체방 모두 가능)

발송 실패는 수집 결과(리포트/Supabase 저장)에 영향을 주지 않는다. 호출 쪽에서 오류를
로그로 남긴다 — 실패로 Run을 끝내면 워크플로의 자동 재시도가 수집부터 다시 돌려,
이미 받은 대화방에 같은 보고서가 한 번 더 가기 때문이다.
"""

from __future__ import annotations

import html
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from .report import _fmt_kr_date, report_title

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
# 텔레그램 한도는 4096자. 태그·이모지 여유를 두고 자른다.
MESSAGE_LIMIT = 3800
MAX_ATTEMPTS = 3


@dataclass
class TelegramConfig:
    token: str
    chat_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "TelegramConfig | None":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chats = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
        if not token or not chats:
            return None
        return cls(token=token, chat_ids=chats)


def _short_money(value: float | None) -> str:
    """핸드폰 한 줄에 들어가게 줄인다: 320,000,000 → 3.2억, 45,000,000 → 4,500만원."""
    if not value:
        return "금액 미상"
    if value >= 100_000_000:
        return f"{value / 100_000_000:.1f}억"
    return f"{value / 10_000:,.0f}만원"


def _deadline_text(c: Any) -> str:
    earliest = c.schedule.earliest
    if c.days_left is None:
        return "일정 미상"
    if c.days_left < 0:
        return "마감"
    kind = f" {earliest[0]}" if earliest else ""
    return f"D-{c.days_left}{kind}"


def _candidate_block(index: int, c: Any) -> str:
    esc = html.escape
    notice = c.notice
    title = esc(notice.title or "")
    if notice.detail_url:
        title = f'<a href="{esc(notice.detail_url, quote=True)}">{title}</a>'
    category = esc(c.category or "유형 미상")
    info = [
        esc(notice.demand_institution or notice.notice_institution or "기관 미상"),
        esc(_short_money(notice.budget)),
        esc(_deadline_text(c)),
    ]
    lines = [
        f"<b>{index}. [{category}]</b> {title}",
        "   " + " · ".join(info),
        f"   {esc(c.qualification.summary)} · 공동수급 {esc(c.joint.label)}",
    ]
    return "\n".join(lines)


def build_messages(candidates: list[Any], stats: Any) -> list[str]:
    """후보 목록을 메시지 한도에 맞춰 여러 개로 나눈다. 공고 하나가 두 메시지로
    갈라지지 않게 공고 단위로 자른다."""
    esc = html.escape
    header = [f"📋 <b>{esc(report_title(stats))}</b>"]
    if stats.period_begin and stats.period_end:
        header.append(f"조회 기간: {_fmt_kr_date(stats.period_begin)} ~ {_fmt_kr_date(stats.period_end)}")
    if stats.failed_operations:
        header.append(f"⚠️ 조회 실패: {esc(', '.join(stats.failed_operations))}")
    if stats.license_error:
        header.append("⚠️ 면허제한정보 조회 실패 — 자격 판정 미적용")

    if not candidates:
        header.append("")
        header.append("조건에 맞는 새 공고가 없습니다.")
        return ["\n".join(header)]

    header.append(f"후보 <b>{len(candidates)}건</b> (전체 표는 첨부 HTML 리포트)")

    messages: list[str] = []
    current = "\n".join(header)
    for i, c in enumerate(candidates, start=1):
        block = _candidate_block(i, c)
        if len(current) + 2 + len(block) > MESSAGE_LIMIT:
            messages.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}"
    messages.append(current)
    return messages


class TelegramError(Exception):
    pass


def _call(session: requests.Session, config: TelegramConfig, method: str, **kwargs: Any) -> None:
    """429(retry_after)·5xx·연결 오류는 몇 번 다시 시도한다. 오류 메시지에서 토큰은 가린다."""
    url = f"{API_BASE}/bot{config.token}/{method}"
    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            res = session.post(url, timeout=60, **kwargs)
        except requests.RequestException as err:
            last_error = f"연결 실패: {err}"
        else:
            if res.status_code == 200:
                return
            try:
                body = res.json()
            except ValueError:
                body = {}
            last_error = f"HTTP {res.status_code}: {body.get('description') or res.text[:200]}"
            if res.status_code == 429:
                wait = (body.get("parameters") or {}).get("retry_after", 5)
                time.sleep(min(float(wait), 60))
                continue
            if res.status_code < 500:
                break  # 토큰/대화방 ID 오류 같은 건 다시 해도 같다
        if attempt < MAX_ATTEMPTS:
            time.sleep(2 * attempt)
    raise TelegramError(f"{method} 실패: {last_error.replace(config.token, '***')}")


def send_report(
    config: TelegramConfig,
    candidates: list[Any],
    stats: Any,
    html_path: Path | None = None,
    session: requests.Session | None = None,
) -> list[str]:
    """대화방마다 메시지와 HTML 리포트를 보낸다. 실패한 대화방의 오류 목록을 돌려준다
    (빈 목록이면 전부 성공)."""
    session = session or requests.Session()
    messages = build_messages(candidates, stats)
    errors: list[str] = []
    for chat_id in config.chat_ids:
        try:
            for text in messages:
                _call(
                    session,
                    config,
                    "sendMessage",
                    data={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": "true",
                    },
                )
            if candidates and html_path is not None and html_path.exists():
                with html_path.open("rb") as handle:
                    _call(
                        session,
                        config,
                        "sendDocument",
                        data={"chat_id": chat_id},
                        files={"document": (html_path.name, handle, "text/html")},
                    )
        except TelegramError as err:
            errors.append(f"[{chat_id}] {err}")
    return errors
