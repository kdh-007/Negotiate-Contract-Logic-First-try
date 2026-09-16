"""수집 → 협상 스코프 → 스크리닝 → 자격 게이트 → 후보 산출.

이 파이프라인 자체는 API 응답만으로 끝난다. 첨부파일 다운로드·텍스트 추출은
`attachments.py`가 별도로 맡고, `cli.py`의 `--fetch-attachment-text` 옵션을 줬을 때만
후보에 대해 추가로 실행된다 (기본 파이프라인에는 영향 없음).
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
    """협상 스코프에 든 공고 1건.

    후보로 살아남은 것과 걸러진 것을 **같은 자료구조로** 담는다.
    걸러진 공고도 마감·공동수급·자격 같은 파생값을 그대로 갖게 하려는 것이다.
    (예전에는 걸러진 공고를 별도 경로로 저장해서 파생 컬럼이 전부 비어 있었다)
    """

    notice: Notice
    screen_result: screen.ScreenResult
    qualification: qualify.QualificationResult
    joint: qualify.JointSupply
    schedule: screen.Schedule
    regions: list[str] = field(default_factory=list)
    days_left: int | None = None
    is_re_notice: bool = False
    variant: str | None = None
    is_candidate: bool = True
    excluded_reason: str | None = None
    # `--fetch-attachment-text`로 API에 자격정보가 없는(qualification.checked=False)
    # 공고의 첨부파일을 열어봤을 때의 보조 확인 결과. attachments.save_attachment_texts가 채운다.
    qualification_note: str | None = None

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
    # 협상 공고인데 후보에서 빠진 것들. 후보와 같은 Candidate 자료구조를 쓴다
    # (is_candidate=False, excluded_reason에 사유). 저장 단계에서 함께 기록된다.
    rejected: list["Candidate"] = field(default_factory=list)
    # 부가 API가 실제로 몇 건을 돌려줬는지. 0이면 "제한 없음"이 아니라 "정보 없음"이다.
    license_rows: int = 0
    region_rows: int = 0


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
        # 파생값은 후보/제외 가릴 것 없이 **모든 협상 공고에 대해** 먼저 계산한다.
        # 전부 로컬 계산이라 비용이 없고, 나중에 "왜 걸러졌지?"를 볼 때 이 값들이 필요하다.
        schedule = screen.build_schedule(notice)
        screen_result = screen.screen(notice, config.screen)
        qualification = qualify.evaluate(license_groups.get(notice.notice_no, []), config.held_names)

        record = Candidate(
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

        if not screen_result.matched:
            reason = screen_result.excluded_by or "미매칭"
            detail = screen_result.excluded_reason or ""
            stats.screened_out[reason] = stats.screened_out.get(reason, 0) + 1
            record.is_candidate = False
            record.excluded_reason = f"{reason}{f' ({detail})' if detail else ''}"
            stats.rejected.append(record)
            continue

        stats.screened_in += 1

        if not qualification.passes:
            stats.gate_excluded += 1
            record.is_candidate = False
            record.excluded_reason = f"자격 미달 ({qualification.summary})"
            stats.rejected.append(record)
            continue

        candidates.append(record)

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
    stats.license_rows = len(license_groups)
    if license_error:
        log.warning("면허제한정보 조회 실패 — 자격 게이트를 건너뜁니다 (fail-open): %s", license_error)
    else:
        log.info("면허제한정보: 공고 %d건분 수신", len(license_groups))

    region_map, region_error = qualify.fetch_region_limits(client, begin, end)
    stats.region_error = region_error
    stats.region_rows = len(region_map)
    if region_error:
        log.warning("참가가능지역 조회 실패: %s", region_error)
    else:
        log.info("참가가능지역: 공고 %d건분 수신", len(region_map))

    candidates = build_candidates(notices, config, license_groups, region_map, now, stats)
    return candidates, stats, notices
