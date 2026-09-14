"""수집 결과를 담는 자료구조."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import fields as F


@dataclass
class Notice:
    """공고 1건 (공고번호 + 차수 단위)."""

    work_type: str
    notice_no: str
    notice_ord: str
    title: str
    notice_institution: str | None = None
    demand_institution: str | None = None
    detail_url: str | None = None

    award_method: str | None = None
    contract_method: str | None = None
    bid_method: str | None = None

    notice_kind: str | None = None
    re_notice_yn: str | None = None
    change_reason: str | None = None

    estimated_price: float | None = None
    assigned_budget: float | None = None

    posted_at: str | None = None
    qualification_deadline: str | None = None
    bid_deadline: str | None = None

    joint_method_name: str | None = None
    joint_method_code: str | None = None
    joint_agreement_deadline: str | None = None
    joint_member_region_limit: str | None = None

    industry_limit_yn: str | None = None
    industry_text: str | None = None
    product_class_no: str | None = None
    product_class_name: str | None = None

    attachments: list[dict[str, str]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.work_type, self.notice_no, self.notice_ord)

    @property
    def budget(self) -> float | None:
        """금액 기준을 하나로 통일한다. 추정가격 우선, 없으면 배정예산."""
        return self.estimated_price if self.estimated_price else self.assigned_budget

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def notice_from_raw(raw: dict[str, Any], work_type: str) -> Notice:
    clean = F.strip_personal_fields(raw)
    return Notice(
        work_type=work_type,
        notice_no=F.pick_field(clean, "notice_no") or "",
        notice_ord=F.pick_field(clean, "notice_ord") or "000",
        title=F.pick_field(clean, "title") or "(제목 없음)",
        notice_institution=F.pick_field(clean, "notice_institution"),
        demand_institution=F.pick_field(clean, "demand_institution"),
        detail_url=F.pick_field(clean, "detail_url"),
        award_method=F.pick_field(clean, "award_method"),
        contract_method=F.pick_field(clean, "contract_method"),
        bid_method=F.pick_field(clean, "bid_method"),
        notice_kind=F.pick_field(clean, "notice_kind"),
        re_notice_yn=F.pick_field(clean, "re_notice_yn"),
        change_reason=F.pick_field(clean, "change_reason"),
        estimated_price=F.pick_number(clean, "estimated_price"),
        assigned_budget=F.pick_number(clean, "assigned_budget"),
        posted_at=F.pick_field(clean, "posted_at"),
        qualification_deadline=F.pick_field(clean, "qualification_deadline"),
        bid_deadline=F.pick_field(clean, "bid_deadline"),
        joint_method_name=F.pick_field(clean, "joint_method_name"),
        joint_method_code=F.pick_field(clean, "joint_method_code"),
        joint_agreement_deadline=F.pick_field(clean, "joint_agreement_deadline"),
        joint_member_region_limit=F.pick_field(clean, "joint_member_region_limit"),
        industry_limit_yn=F.pick_field(clean, "industry_limit_yn"),
        industry_text=F.pick_field(clean, "industry_text"),
        product_class_no=F.pick_field(clean, "product_class_no"),
        product_class_name=F.pick_field(clean, "product_class_name"),
        attachments=F.extract_attachments(clean),
        raw=clean,
    )
