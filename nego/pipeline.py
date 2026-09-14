"""수집 → 협상 스코프 → 스크리닝 → 자격 게이트 → 후보 산출.

오늘의 범위는 **API만으로 끝나는 구간**이다. 첨부파일 다운로드·파싱은 다음 단계다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import fields as F
from . import qualify, scope, screen
from .config import AppConfig
from .http_client import ApiError, DataGoKrClient
from .models import Notice, notice_from_raw

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    notice: Notice
    screen_result: screen.ScreenResult
    qualification: qualify.QualificationResult
    joint: qualify.JointSupply
    schedule: screen.Schedule
    regions: list[str] = field(default_factory=list)
    days_left: int | None = None
    is_re_notice: bool = False
    variant: str | None = None

    @property
    def gate_passed(self) -> bool:
        return self.qualification.passes

    @property
    def sort_key(self) -> tuple:
        """검토 순서. 마감 임박 → 강력추천 → 공고명(같은 순위일 때 순서를 고정하기 위함)."""
        days = self.days_left if self.days_left is not None else 9999
        confidence_rank = 0 if self.screen_result.confidence == "강력추천" else 1
        return (days, confidence_rank, self.notice.title)


@dataclass
class RunStats:
    fetched: int = 0
    failed_operations: list[str] = field(default_factory=list)
    cancelled: int = 0
    not_negotiated: int = 0
    old_ordinal: int = 0
    negotiated: int = 0
    screened_out: dict[str, int] = field(default_factory=dict)
    screened_in: int = 0
    gate_excluded: int = 0
    candidates: int = 0
    license_error: str | None = None
    region_error: str | None = None
    # 협상 공고인데 후보에서 빠진 것들 — (공고, 사유).
    # 나중에 "이 공고가 왜 안 떴지?"를 확인할 수 있도록 저장 단계로 넘긴다.
    rejected: list[tuple[Notice, str]] = field(default_factory=list)


def _api_window(now: datetime, lookback_days: int) -> tuple[str, str]:
    begin = now - timedelta(days=lookback_days)
    return begin.strftime("%Y%m%d0000"), now.strftime("%Y%m%d%H%M")


def collect_notices(
    client: DataGoKrClient, begin: str, end: str, stats: RunStats
) -> list[Notice]:
    """업무구분 3종을 각각 조회한다. 하나가 실패해도 나머지는 계속 진행한다."""
    collected: list[Notice] = []

    for work_type, operation in F.BID_NOTICE_OPERATIONS.items():
        label = f"본공고/{work_type}"
        try:
            raw_items = client.fetch_all_pages(
                F.BID_NOTICE_BASE_URL,
                operation,
                {"inqryDiv": "1", "inqryBgnDt": begin, "inqryEndDt": end},
                label,
            )
        except ApiError as err:
            log.error("%s 조회 실패: %s", label, err)
            stats.failed_operations.append(label)
            continue

        notices = [notice_from_raw(raw, work_type) for raw in raw_items]
        log.info("%s 조회 완료: %d건", label, len(notices))
        collected.extend(notices)

    stats.fetched = len(collected)
    return collected


def build_candidates(
    notices: list[Notice],
    config: AppConfig,
    license_groups: dict[str, list[qualify.LicenseGroup]],
    regions: dict[str, list[str]],
    now: datetime,
    stats: RunStats,
) -> list[Candidate]:
    scope_result = scope.apply_scope(notices)
    stats.cancelled = scope_result.dropped_cancelled
    stats.not_negotiated = scope_result.dropped_not_negotiated
    stats.old_ordinal = scope_result.dropped_old_ordinal
    stats.negotiated = len(scope_result.kept)

    candidates: list[Candidate] = []

    for notice in scope_result.kept:
        screen_result = screen.screen(notice, config.screen)
        if not screen_result.matched:
            reason = screen_result.excluded_by or "미매칭"
            stats.screened_out[reason] = stats.screened_out.get(reason, 0) + 1
            detail = screen_result.excluded_reason or ""
            stats.rejected.append((notice, f"{reason}{f' ({detail})' if detail else ''}"))
            continue
        stats.screened_in += 1

        groups = license_groups.get(notice.notice_no, [])
        qualification = qualify.evaluate(groups, config.held_names)
        if not qualification.passes:
            stats.gate_excluded += 1
            stats.rejected.append((notice, f"자격 미달 ({qualification.summary})"))
            continue

        schedule = screen.build_schedule(notice)
        candidates.append(
            Candidate(
                notice=notice,
                screen_result=screen_result,
                qualification=qualification,
                joint=qualify.parse_joint_supply(notice.joint_method_name),
                schedule=schedule,
                regions=regions.get(notice.notice_no, []),
                days_left=schedule.days_left(now),
                is_re_notice=scope.is_re_notice(notice),
                variant=scope.negotiation_variant(notice),
            )
        )

    candidates.sort(key=lambda c: c.sort_key)
    stats.candidates = len(candidates)
    return candidates


def run(config: AppConfig, now: datetime | None = None) -> tuple[list[Candidate], RunStats, list[Notice]]:
    now = now or datetime.now()
    stats = RunStats()
    client = DataGoKrClient(config.api)

    begin, end = _api_window(now, config.lookback_days)
    log.info("조회 기간: %s ~ %s", begin, end)

    notices = collect_notices(client, begin, end, stats)

    if len(stats.failed_operations) == len(F.BID_NOTICE_OPERATIONS):
        raise ApiError("전체조회", "업무구분 3종 조회가 모두 실패했습니다. API 키/네트워크를 확인하세요.")

    license_groups, license_error = qualify.fetch_license_groups(client, begin, end)
    stats.license_error = license_error
    if license_error:
        log.warning("면허제한정보 조회 실패 — 자격 게이트를 건너뜁니다 (fail-open): %s", license_error)

    region_map, region_error = qualify.fetch_region_limits(client, begin, end)
    stats.region_error = region_error

    candidates = build_candidates(notices, config, license_groups, region_map, now, stats)
    return candidates, stats, notices
