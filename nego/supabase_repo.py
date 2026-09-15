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

from .pipeline import Candidate

log = logging.getLogger(__name__)

DEFAULT_TABLE = "협상에의한계약 추출 로직_1차"
DEFAULT_BUCKET = "nego-attachments"

# upsert 충돌 기준. 테이블의 UNIQUE 제약과 반드시 일치해야 한다.
CONFLICT_COLUMNS = "work_type,bid_ntce_no,bid_ntce_ord"

# 한 번에 보내는 행 수. 너무 크면 요청이 커져 타임아웃이 난다.
BATCH_SIZE = 100

# PostgREST는 배열로 여러 행을 보낼 때 **모든 객체의 키가 완전히 같아야** 한다.
# 하나라도 키 구성이 다르면 PGRST102 "All object keys must match" 로 전체가 거부된다.
# 그래서 모든 행을 이 컬럼 목록으로 맞춘 뒤 전송한다.
# (id / created_at / updated_at 은 DB가 채우므로 보내지 않는다)
ROW_COLUMNS = (
    "work_type",
    "bid_ntce_no",
    "bid_ntce_ord",
    "title",
    "notice_institution",
    "demand_institution",
    "detail_url",
    "award_method",
    "award_variant",
    "contract_method",
    "bid_method",
    "notice_kind",
    "is_re_notice",
    "change_reason",
    "estimated_price",
    "assigned_budget",
    "budget",
    "posted_at",
    "qualification_deadline",
    "joint_agreement_deadline",
    "bid_deadline",
    "earliest_deadline",
    "earliest_deadline_kind",
    "days_left",
    "joint_allowed",
    "joint_submit_type",
    "joint_exec_type",
    "joint_method_name",
    "qualification_summary",
    "qualification_total_groups",
    "qualification_missing_count",
    "qualification_checked",
    "regions",
    "confidence",
    "matched_keywords",
    "matched_product_codes",
    "matched_industry_codes",
    "attachments",
    "attachment_count",
    "raw",
    "is_candidate",
    "excluded_reason",
    "collected_at",
)


def align_columns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """모든 행이 ROW_COLUMNS와 똑같은 키 구성을 갖도록 맞춘다.

    빠진 키는 None으로 채우고, 목록에 없는 키는 버린다.
    이걸 안 하면 후보 행(키 42개)과 제외 행(키 더 적음)이 섞여 PGRST102가 난다.
    """
    return [{col: row.get(col) for col in ROW_COLUMNS} for row in rows]


class SupabaseError(Exception):
    pass


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def candidate_to_row(candidate: Candidate) -> dict[str, Any]:
    """Candidate 하나를 테이블 한 행으로 변환한다.

    후보와 제외된 공고를 **같은 함수로** 처리한다. 예전에는 제외된 공고를 별도 함수로
    만들면서 파생 컬럼(마감·공동수급·자격 등)을 안 채웠고, 그래서 CSV로 빼보면
    후보 몇 줄만 값이 차 있고 나머지 천여 줄이 비어 보였다.
    """
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
        "is_candidate": candidate.is_candidate,
        "excluded_reason": candidate.excluded_reason,
        "collected_at": datetime.now().isoformat(),
    }


@dataclass
class SupabaseConfig:
    url: str
    service_key: str
    table: str = DEFAULT_TABLE
    bucket: str = DEFAULT_BUCKET

    @classmethod
    def from_env(cls) -> "SupabaseConfig | None":
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        table = os.environ.get("SUPABASE_TABLE", DEFAULT_TABLE).strip() or DEFAULT_TABLE
        bucket = os.environ.get("SUPABASE_BUCKET", DEFAULT_BUCKET).strip() or DEFAULT_BUCKET
        if not url or not key:
            return None
        return cls(url=url, service_key=key, table=table, bucket=bucket)


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

        rows = align_columns(rows)

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

    # ── Storage (첨부파일 보관) ────────────────────────────────
    #
    # GitHub Actions는 실행이 끝나면 파일시스템이 사라진다.
    # 내려받은 첨부를 남기려면 Storage에 올려야 한다.

    def _storage_url(self, path: str, prefix: str = "object") -> str:
        bucket = quote(self.config.bucket, safe="")
        # 경로 구분자(/)는 살리고 한글·공백만 인코딩한다.
        object_path = quote(path, safe="/")
        return f"{self.config.url}/storage/v1/{prefix}/{bucket}/{object_path}"

    def object_exists(self, path: str, timeout: float = 15.0) -> bool:
        """Storage에 이미 올라간 파일인지 확인한다. 확인 실패는 False로 본다(그냥 받는다)."""
        try:
            res = self.session.get(
                self._storage_url(path, prefix="object/info"),
                headers={
                    "apikey": self.config.service_key,
                    "Authorization": f"Bearer {self.config.service_key}",
                },
                timeout=timeout,
            )
        except requests.RequestException:
            return False
        return res.status_code == 200

    def upload_file(self, local_path, storage_path: str, content_type: str, timeout: float = 120.0) -> bool:
        """파일 하나를 Storage에 올린다. 같은 경로가 있으면 덮어쓴다(x-upsert)."""
        with open(local_path, "rb") as handle:
            res = self.session.post(
                self._storage_url(storage_path),
                headers={
                    "apikey": self.config.service_key,
                    "Authorization": f"Bearer {self.config.service_key}",
                    "Content-Type": content_type,
                    "x-upsert": "true",
                },
                data=handle,
                timeout=timeout,
            )

        if res.status_code >= 400:
            raise SupabaseError(f"Storage 업로드 실패 (HTTP {res.status_code}): {res.text[:200]}")
        return True

    def save(self, candidates: Iterable[Candidate], rejected: Iterable[Candidate]) -> int:
        """후보와 제외된 공고를 한 번에 저장한다. 둘 다 Candidate라 변환 함수가 하나면 된다."""
        rows = [candidate_to_row(c) for c in candidates]
        rows += [candidate_to_row(c) for c in rejected]
        return self.upsert_rows(rows)
