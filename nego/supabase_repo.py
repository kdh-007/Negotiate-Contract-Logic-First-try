"""Supabase 저장소.

Supabase REST API(PostgREST)를 직접 호출한다. `supabase-py` 같은 SDK를 쓰지 않는 이유는
의존성을 `requests` 하나로 유지하기 위해서다 — GitHub Actions에서 설치가 빠르고 깨질 일이 적다.

필요한 환경변수
  SUPABASE_URL                 예: https://xxxxxxxx.supabase.co
  SUPABASE_SERVICE_ROLE_KEY    service_role 키 (RLS를 우회해 쓰기 가능)
  SUPABASE_TABLE               기본값 "협상에의한계약 추출 로직_1차"

service_role 키는 **절대 공개 레포나 클라이언트 코드에 넣지 말 것.**
GitHub Actions Secrets에만 둔다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import quote

import requests

from .models import Notice
from .pipeline import Candidate

log = logging.getLogger(__name__)

DEFAULT_TABLE = "협상에의한계약 추출 로직_1차"

# upsert 충돌 기준. 테이블의 UNIQUE 제약과 반드시 일치해야 한다.
CONFLICT_COLUMNS = "work_type,bid_ntce_no,bid_ntce_ord"

# 한 번에 보내는 행 수. 너무 크면 요청이 커져 타임아웃이 난다.
BATCH_SIZE = 100


class SupabaseError(Exception):
    pass


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def candidate_to_row(candidate: Candidate) -> dict[str, Any]:
    """Candidate 하나를 테이블 한 행으로 변환한다."""
    notice = candidate.notice
    schedule = candidate.schedule
    earliest = schedule.earliest

    return {
        "work_type": notice.work_type,
        "bid_ntce_no": notice.notice_no,
        "bid_ntce_ord": notice.notice_ord,
        "title": notice.title,
        "notice_institution": notice.notice_institution,
        "demand_institution": notice.demand_institution,
        "detail_url": notice.detail_url,
        "award_method": notice.award_method,
        "award_variant": candidate.variant,
        "contract_method": notice.contract_method,
        "bid_method": notice.bid_method,
        "notice_kind": notice.notice_kind,
        "is_re_notice": candidate.is_re_notice,
        "change_reason": notice.change_reason,
        "estimated_price": notice.estimated_price,
        "assigned_budget": notice.assigned_budget,
        "budget": notice.budget,
        "posted_at": notice.posted_at,
        "qualification_deadline": _iso(schedule.qualification_deadline),
        "joint_agreement_deadline": _iso(schedule.joint_agreement_deadline),
        "bid_deadline": _iso(schedule.bid_deadline),
        "earliest_deadline": _iso(earliest[1]) if earliest else None,
        "earliest_deadline_kind": earliest[0] if earliest else None,
        "days_left": candidate.days_left,
        "joint_allowed": candidate.joint.allowed,
        "joint_submit_type": candidate.joint.submit_type,
        "joint_exec_type": candidate.joint.exec_type,
        "joint_method_name": candidate.joint.raw_value,
        "qualification_summary": candidate.qualification.summary,
        "qualification_total_groups": candidate.qualification.total_groups,
        "qualification_missing_count": candidate.qualification.missing_count,
        "qualification_checked": candidate.qualification.checked,
        "regions": candidate.regions or None,
        "confidence": candidate.screen_result.confidence,
        "matched_keywords": candidate.screen_result.matched_keywords or None,
        "matched_product_codes": candidate.screen_result.matched_product_codes or None,
        "matched_industry_codes": candidate.screen_result.matched_industry_codes or None,
        "attachments": notice.attachments,
        "attachment_count": len(notice.attachments),
        "raw": notice.raw,
        "is_candidate": True,
        "collected_at": datetime.now().isoformat(),
    }


def rejected_to_row(notice: Notice, reason: str) -> dict[str, Any]:
    """후보에서 걸러진 협상 공고도 기록한다.

    왜 저장하는가: 나중에 "이 공고가 왜 안 떴지?"를 확인할 수 있어야 한다.
    is_candidate=false 로 구분하고, qualification_summary에 제외 사유를 남긴다.
    """
    return {
        "work_type": notice.work_type,
        "bid_ntce_no": notice.notice_no,
        "bid_ntce_ord": notice.notice_ord,
        "title": notice.title,
        "notice_institution": notice.notice_institution,
        "demand_institution": notice.demand_institution,
        "detail_url": notice.detail_url,
        "award_method": notice.award_method,
        "contract_method": notice.contract_method,
        "bid_method": notice.bid_method,
        "notice_kind": notice.notice_kind,
        "estimated_price": notice.estimated_price,
        "assigned_budget": notice.assigned_budget,
        "budget": notice.budget,
        "posted_at": notice.posted_at,
        "joint_method_name": notice.joint_method_name,
        "qualification_summary": f"제외: {reason}",
        "attachments": notice.attachments,
        "attachment_count": len(notice.attachments),
        "raw": notice.raw,
        "is_candidate": False,
        "collected_at": datetime.now().isoformat(),
    }


@dataclass
class SupabaseConfig:
    url: str
    service_key: str
    table: str = DEFAULT_TABLE

    @classmethod
    def from_env(cls) -> "SupabaseConfig | None":
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        table = os.environ.get("SUPABASE_TABLE", DEFAULT_TABLE).strip() or DEFAULT_TABLE
        if not url or not key:
            return None
        return cls(url=url, service_key=key, table=table)


class SupabaseRepository:
    def __init__(self, config: SupabaseConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()

    @property
    def _endpoint(self) -> str:
        # 테이블명에 공백과 한글이 있어 URL 인코딩이 필요하다.
        table_path = quote(self.config.table, safe="")
        return f"{self.config.url}/rest/v1/{table_path}"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.config.service_key,
            "Authorization": f"Bearer {self.config.service_key}",
            "Content-Type": "application/json",
            # merge-duplicates = 같은 키가 있으면 UPDATE, 없으면 INSERT
            # return=minimal = 저장한 행을 되돌려받지 않음 (응답이 가벼워진다)
            "Prefer": "resolution=merge-duplicates,return=minimal",
        }

    def upsert_rows(self, rows: list[dict[str, Any]], timeout: float = 60.0) -> int:
        """행을 BATCH_SIZE씩 나눠 upsert 한다."""
        if not rows:
            return 0

        saved = 0
        for start in range(0, len(rows), BATCH_SIZE):
            batch = rows[start : start + BATCH_SIZE]
            try:
                res = self.session.post(
                    self._endpoint,
                    params={"on_conflict": CONFLICT_COLUMNS},
                    headers=self._headers,
                    json=batch,
                    timeout=timeout,
                )
            except requests.RequestException as err:
                raise SupabaseError(f"Supabase 요청 실패: {err}") from err

            if res.status_code >= 400:
                # 오류 본문에 키가 섞일 일은 없지만, 길이를 잘라 로그를 지저분하게 만들지 않는다.
                raise SupabaseError(
                    f"Supabase 저장 실패 (HTTP {res.status_code}): {res.text[:300]}"
                )
            saved += len(batch)
            log.info("Supabase 저장 %d/%d", saved, len(rows))

        return saved

    def save(self, candidates: Iterable[Candidate], rejected: Iterable[tuple[Notice, str]]) -> int:
        rows = [candidate_to_row(c) for c in candidates]
        rows += [rejected_to_row(n, reason) for n, reason in rejected]
        return self.upsert_rows(rows)
