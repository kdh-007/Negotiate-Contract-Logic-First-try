"""「나라장터에서 불러오기」 — `nego` 수집·자격판정을 그대로 돌리고 화면용 JSON으로 바꾼다.

수집 로직은 CLI(`python -m nego --fetch-attachment-text`)와 같은 함수를 같은 순서로 부른다:
`pipeline.run` → (선택) `attachments.save_attachment_texts`(첨부 참가자격을 API 판정에
겹치고 일정 미상을 보충). 여기서 새로 더하는 것은 싱크로율·AI 판단 표시뿐이다.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from nego import qualify, scope
from nego.config import AppConfig, load_config, redact
from nego.report import _dedupe_names_preferring_code, _display_name

from .store import Store
from .sync import PastIndex

log = logging.getLogger(__name__)

AI_LABEL = {"유사": "적합", "부분 유사": "검토필요", "무관": "부적합"}
PERIODS = [1, 3, 7, 14, 30]


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="minutes") if dt else None


def _group_names(groups) -> list[str]:
    """HTML 리포트의 자격 팝업과 같은 목록 — "이름(코드)", 코드 없는 중복은 코드 있는 쪽을 남긴다."""
    names = list(dict.fromkeys(_display_name(n) for g in groups for n in g.allowed_names))
    return _dedupe_names_preferring_code(names)


def serialize(candidate, past: PastIndex) -> dict[str, Any]:
    """Candidate 1건 → 카드 1장. 후보/제외 공고 모두 같은 모양."""
    n = candidate.notice
    q = candidate.qualification
    earliest = candidate.schedule.earliest
    sync = past.score(n.title, candidate.attachment_text)
    ai = candidate.llm_similarity
    ai_row = None
    if ai is not None:
        best = ai.best
        ai_row = {
            "label": AI_LABEL.get(ai.overall, "미판정"),
            "overall": ai.overall,
            "reason": ai.error or ai.overall_reason,
            "best": getattr(best, "project_title", None) if best else None,
        }
    return {
        "key": f"{n.notice_no}-{n.notice_ord}",
        "notice_no": n.notice_no,
        "notice_ord": n.notice_ord,
        "kind": "본공고",
        "title": n.title,
        "work_type": n.work_type,
        "category": candidate.category,
        "award_method": n.award_method,
        "confidence": candidate.screen_result.confidence,
        "matched_keywords": candidate.screen_result.matched_keywords,
        "is_candidate": candidate.is_candidate,
        "excluded_reason": candidate.excluded_reason,
        "is_re_notice": candidate.is_re_notice,
        "demand_institution": n.demand_institution or n.notice_institution,
        "notice_institution": n.notice_institution,
        "budget": n.budget,
        "posted_at": n.posted_at,
        "deadline_label": earliest[0] if earliest else None,
        "deadline": _iso(earliest[1]) if earliest else None,
        "days_left": candidate.days_left,
        "detail_url": n.detail_url,
        "regions": candidate.regions,
        "joint": {"label": candidate.joint.label, "allowed": candidate.joint.allowed},
        "qualification": {
            "checked": q.checked,
            "passes": q.passes,
            "summary": q.summary,
            "satisfied": len(q.satisfied_groups),
            "total": len(q.satisfied_groups) + len(q.missing_groups),
            "missing": _group_names(q.missing_groups),
            "satisfied_names": _group_names(q.satisfied_groups),
        },
        "has_attachment_text": bool(candidate.attachment_text.strip()),
        "sync": sync,
        "ai": ai_row,
    }


def _judge_ai(candidates, past: PastIndex, max_candidates: int) -> int:
    """AI 판단 — `llm_similarity.judge_notice`에 싱크로율 상위 과거 실적 8건만 넘긴다
    (150건 전부를 매번 보내지 않으려고). ANTHROPIC_API_KEY 없으면 건너뛴다."""
    from nego import llm_similarity
    from nego.similarity import PastProject

    config = llm_similarity.LlmConfig.from_env()
    if config is None:
        log.warning("ANTHROPIC_API_KEY 없음 → AI 판단 건너뜀")
        return 0
    client = llm_similarity.make_client()
    done = 0
    for candidate in candidates[:max_candidates]:
        sync = past.score(candidate.notice.title, candidate.attachment_text, top=8)
        projects = [
            PastProject(
                title=f"({row['year']}) {row['title']}",
                summary_text=f"과업: {past.projects[row['index']].get('overview', '')}\n"
                f"전시내용: {past.projects[row['index']].get('exhibition', '')}",
            )
            for row in sync["top"]
        ]
        if not projects:
            continue
        judgement = llm_similarity.judge_notice(
            client, config, llm_similarity.NoticeInput.from_candidate(candidate), projects
        )
        candidate.llm_similarity = judgement
        done += 1
        log.info("AI 판단 %d/%d: %s → %s", done, min(len(candidates), max_candidates),
                 candidate.notice.title[:30], judgement.overall)
    return done


@dataclass
class Job:
    running: bool = False
    started_at: str | None = None
    finished_at: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)
    error: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {"running": self.running, "started_at": self.started_at, "finished_at": self.finished_at,
                "params": self.params, "log": self.log[-30:], "error": self.error}


class _JobLogHandler(logging.Handler):
    def __init__(self, job: Job):
        super().__init__(logging.INFO)
        self.job = job

    def emit(self, record: logging.LogRecord) -> None:
        self.job.log.append(f"{datetime.now():%H:%M:%S} {record.getMessage()}")


class Collector:
    """수집은 한 번에 하나만 돈다(나라장터 API 호출량 보호). 결과는 Store에 남겨 팀원이 같이 본다."""

    def __init__(self, store: Store, past: PastIndex,
                 runner: Callable[..., tuple[list, Any]] | None = None,
                 config_loader: Callable[[], AppConfig] = load_config):
        self.store = store
        self.past = past
        self.job = Job()
        self._lock = threading.Lock()
        self._runner = runner or self._run_nego
        self._config_loader = config_loader

    def start(self, days: int, attachments: bool, ai: bool, categories: list[str] | None) -> Job:
        if days not in PERIODS:
            raise ValueError(f"기간은 {PERIODS}일 중 하나")
        cats = scope.parse_categories(",".join(categories)) if categories else None
        with self._lock:
            if self.job.running:
                raise RuntimeError("이미 수집 중입니다")
            self.job = Job(running=True, started_at=datetime.now().isoformat(timespec="seconds"),
                           params={"days": days, "attachments": attachments, "ai": ai,
                                   "categories": sorted(cats) if cats else None})
        threading.Thread(target=self._work, args=(self.job, days, attachments, ai, cats), daemon=True).start()
        return self.job

    def _run_nego(self, config: AppConfig, days: int, attachments: bool, cats):
        from nego.pipeline import run

        config.lookback_days = days
        now = datetime.now()
        candidates, stats, _ = run(config, now, cats)
        if attachments:
            from nego.attachments import save_attachment_texts

            att = save_attachment_texts(
                candidates, config.output_dir,
                held_codes=qualify.load_held_codes(config.held_raw),
                held_code_names=qualify.load_held_code_names(config.held_raw),
                now=now, code_names=config.code_names,
            )
            log.info("첨부파일: 시도 %d → 성공 %d / 실패 %d · 자격판정 반영 %d건",
                     att["attempted"], att["ok"], att["failed"], att["qualification_determined"])
            if att["unnamed_codes"]:
                log.info("이름 미확인 코드 (config/code_names.json에 추가): %s", ", ".join(att["unnamed_codes"]))
        return candidates, stats

    def _work(self, job: Job, days: int, attachments: bool, ai: bool, cats) -> None:
        handler = _JobLogHandler(job)
        root = logging.getLogger()
        root.addHandler(handler)
        if root.level > logging.INFO:
            root.setLevel(logging.INFO)
        config = None
        try:
            config = self._config_loader()
            if not config.api.service_key:
                raise RuntimeError("NARA_SERVICE_KEY 환경변수가 없습니다 (서버를 띄운 창에 설정)")
            log.info("수집 시작: 최근 %d일%s", days, " + 첨부 자격판정" if attachments else "")
            candidates, stats = self._runner(config, days, attachments, cats)
            if ai:
                import os
                _judge_ai(candidates, self.past, int(os.environ.get("LLM_MAX_CANDIDATES", "30")))
            payload = {
                "candidates": [serialize(c, self.past) for c in candidates],
                "rejected": [serialize(c, self.past) for c in stats.rejected],
                "stats": {
                    "fetched": stats.fetched, "in_scope": stats.in_scope,
                    "private_contract": stats.private_contract, "cancelled": stats.cancelled,
                    "screened_out": stats.screened_out, "candidates": stats.candidates,
                    "qualification_flagged": stats.qualification_flagged,
                    "license_error": stats.license_error, "region_error": stats.region_error,
                    "period_begin": _iso(stats.period_begin), "period_end": _iso(stats.period_end),
                },
            }
            self.store.save_run(job.started_at, job.params, payload)
            log.info("수집 완료: 후보 %d건 · 제외 %d건", len(payload["candidates"]), len(payload["rejected"]))
        except Exception as err:  # 화면에 사유를 그대로 보여준다 (서비스키는 가림)
            key = config.api.service_key if config else ""
            job.error = redact(f"{type(err).__name__}: {err}", [key] if key else [])
            log.error("수집 실패: %s", job.error)
        finally:
            job.running = False
            job.finished_at = datetime.now().isoformat(timespec="seconds")
            root.removeHandler(handler)
