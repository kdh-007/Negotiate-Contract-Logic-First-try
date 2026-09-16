"""자격조건(면허·업종 제한) 판정.

**판정 규칙은 기존 시스템(`matching/qualificationFilter.ts`)과 동일하게 유지한다.**
회사가 정한 사업 판단이므로 임의로 바꾸지 않는다.

  - 제한그룹(`lmtGrpNo`) 하나가 자격조건 하나의 단위
  - 그룹 안에 여러 업종이 나열되면 그중 하나만 보유해도 그 그룹은 충족(OR)
  - 충족하지 못한 그룹이 2개 이상이면 제외 (1개까지는 통과)
  - 조회 실패 / 정보 없음이면 걸러내지 않고 통과 (fail-open)

공동수급·지역은 **판정에 개입하지 않고 정보로만** 수집한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import fields as F
from .http_client import ApiError, DataGoKrClient, RawItem

# 기존 시스템과 동일. 충족 못한 그룹이 이 개수까지는 통과시킨다.
MAX_ALLOWED_MISSING_QUALIFICATIONS = 1


@dataclass
class LicenseGroup:
    group_no: str
    allowed_names: list[str] = field(default_factory=list)


@dataclass
class QualificationResult:
    total_groups: int
    missing_groups: list[LicenseGroup]
    passes: bool
    checked: bool  # False면 자격정보가 없어 판정을 못 한 것 (fail-open으로 통과)

    @property
    def missing_count(self) -> int:
        return len(self.missing_groups)

    @property
    def summary(self) -> str:
        if not self.checked:
            return "자격정보 없음 (판정 보류, 통과)"
        if self.missing_count == 0:
            return f"자격 충족 ({self.total_groups}개 그룹 전부)"
        return f"미충족 {self.missing_count}/{self.total_groups} 그룹"


def split_industry_list(text: str) -> list[str]:
    """허용업종목록 텍스트를 개별 업종명으로 나눈다.

    실측 형태는 "[업종명/업종코드][업종명/업종코드]…" 이다 (예: "[실내건축공사업/4990]").
    대괄호 단위로 나눈 뒤 마지막 '/' 뒤의 코드를 떼어 업종명만 남긴다.
    대괄호가 없으면 콤마·슬래시로 나눈다.
    """
    bracketed = re.findall(r"\[([^\]]+)\]", text)
    if bracketed:
        out = []
        for entry in bracketed:
            name = entry.rsplit("/", 1)[0] if "/" in entry else entry
            name = name.strip()
            if name:
                out.append(name)
        return out

    return [s.strip() for s in re.split(r"[,/·、]", text) if s.strip()]


def group_license_rows(raw_items: list[RawItem]) -> dict[str, list[LicenseGroup]]:
    """면허제한 응답을 공고번호별 제한그룹 목록으로 정리한다.

    주의: 이 오퍼레이션은 bidNtceNo를 넘겨도 서버가 필터링하지 않고 조회기간 전체를
    페이지 단위로 내려준다. 그래서 공고별로 호출하지 않고 **기간 전체를 1회 받아
    여기서 공고번호별로 묶는다.** 공고 수가 늘어도 API 호출 횟수가 늘지 않는다.
    """
    by_notice: dict[str, dict[str, list[str]]] = {}

    for item in raw_items:
        notice_no = F.pick_by(item, F.LICENSE_LIMIT_FIELDS, "notice_no")
        group_no = F.pick_by(item, F.LICENSE_LIMIT_FIELDS, "group_no")
        if not notice_no or not group_no:
            continue

        names: list[str] = []
        license_name = F.pick_by(item, F.LICENSE_LIMIT_FIELDS, "license_name")
        if license_name:
            names.append(license_name)
        allowed = F.pick_by(item, F.LICENSE_LIMIT_FIELDS, "allowed_industries")
        if allowed:
            names.extend(split_industry_list(allowed))
        if not names:
            continue

        groups = by_notice.setdefault(notice_no, {})
        groups.setdefault(group_no, []).extend(names)

    return {
        notice_no: [LicenseGroup(group_no=g, allowed_names=names) for g, names in groups.items()]
        for notice_no, groups in by_notice.items()
    }


def _is_group_satisfied(group: LicenseGroup, held_names: list[str]) -> bool:
    """기존 시스템과 동일한 양방향 부분일치."""
    for allowed in group.allowed_names:
        for held in held_names:
            if allowed in held or held in allowed:
                return True
    return False


def evaluate(groups: list[LicenseGroup], held_names: list[str]) -> QualificationResult:
    if not groups:
        return QualificationResult(total_groups=0, missing_groups=[], passes=True, checked=False)

    missing = [g for g in groups if not _is_group_satisfied(g, held_names)]
    return QualificationResult(
        total_groups=len(groups),
        missing_groups=missing,
        passes=len(missing) <= MAX_ALLOWED_MISSING_QUALIFICATIONS,
        checked=True,
    )


def match_from_attachment_text(items: list[str], held_names: list[str]) -> str | None:
    """첨부파일에서 뽑은 참가자격 항목 중 보유 명단과 일치하는 것을 찾는다.

    API 면허제한정보가 비어 있어(`checked=False`) 판정을 못 한 공고를 사람이 원문
    전체를 열어 확인하는 수고를 줄이기 위한 보조 확인이다. 자유 텍스트라 그룹(OR)
    구조를 알 수 없으므로 이 결과로 **제외 판정을 내리지는 않는다** — 일치하는
    항목을 찾으면 그 문장을 반환해 확인됐음을 알리고, 못 찾으면 None을 반환해
    (제외가 아니라) 원문 확인이 필요함을 알리는 용도로만 쓴다.
    """
    for item in items:
        for held in held_names:
            if held and held in item:
                return item
    return None


def load_held_names(held_config: dict) -> list[str]:
    names: list[str] = []
    for key in ("heldProducts", "heldIndustries"):
        for entry in held_config.get(key, []):
            name = str(entry.get("name", "")).strip()
            if name:
                names.append(name)
    return names


def fetch_license_groups(
    client: DataGoKrClient, begin: str, end: str
) -> tuple[dict[str, list[LicenseGroup]], str | None]:
    """조회기간 전체의 면허제한정보를 1회 받아 공고번호별로 묶어 반환한다.

    실패해도 예외를 올리지 않는다 — 호출측에서 fail-open으로 처리하기 위함.

    주의: 이 오퍼레이션은 조회기간이 너무 길면 resultCode=07(입력범위값 초과)로
    거부한다. 실측상 30일은 되고 180일은 막힌다 — 정확한 상한은 모른다.
    실패하면 이 기간 내 **모든** 공고가 "자격정보 없음(판정 보류)"로 처리되므로,
    LOOKBACK_DAYS를 길게 잡아 수동 실행할 때는 특히 주의할 것
    (`scripts/inspect_notice.py`로 먼저 범위를 확인해볼 수 있다).
    """
    try:
        raw_items = client.fetch_all_pages(
            F.BID_NOTICE_BASE_URL,
            F.LICENSE_LIMIT_OPERATION,
            {"inqryDiv": "1", "inqryBgnDt": begin, "inqryEndDt": end},
            "면허제한정보",
        )
    except ApiError as err:
        return {}, str(err)
    return group_license_rows(raw_items), None


def fetch_region_limits(
    client: DataGoKrClient, begin: str, end: str
) -> tuple[dict[str, list[str]], str | None]:
    """참가가능지역을 공고번호별로 수집한다 (판정에는 쓰지 않고 정보로만 표시).

    협상계약은 용역·물품 비중이 높은데 이 API는 용역·물품에서 대부분 값을 주지 않는다.
    비어 있는 것이 정상이며, "제한 없음"이 아니라 "정보 없음"으로 다뤄야 한다.
    """
    try:
        raw_items = client.fetch_all_pages(
            F.BID_NOTICE_BASE_URL,
            F.REGION_LIMIT_OPERATION,
            {"inqryDiv": "1", "inqryBgnDt": begin, "inqryEndDt": end},
            "참가가능지역",
        )
    except ApiError as err:
        return {}, str(err)

    by_notice: dict[str, list[str]] = {}
    for item in raw_items:
        notice_no = F.pick_by(item, F.REGION_LIMIT_FIELDS, "notice_no")
        region = F.pick_by(item, F.REGION_LIMIT_FIELDS, "region_name")
        if notice_no and region:
            by_notice.setdefault(notice_no, []).append(region)
    return by_notice, None


# ── 공동수급 (정보 수집 전용) ───────────────────────────────────

_JOINT_PATTERN = re.compile(r"^\((?P<submit>전자|수기|없음)\)(?P<exec>.+)$")


@dataclass
class JointSupply:
    allowed: bool
    submit_type: str | None  # 전자 / 수기 / 없음 (공동수급협정서 제출 방식)
    exec_type: str | None  # 공동이행 / 분담이행 / 공동이행 또는 분담이행 / 혼합방식 / 공동수급불허
    raw_value: str | None

    @property
    def label(self) -> str:
        if self.raw_value is None:
            return "정보 없음"
        return self.exec_type or self.raw_value


def parse_joint_supply(value: str | None) -> JointSupply:
    """cmmnSpldmdMethdNm 을 '(제출방식)이행방식' 구조로 분해한다.

    실측 값 예: "(없음)공동수급불허", "(전자)공동이행 또는 분담이행", "(수기)분담이행"
    """
    if not value:
        return JointSupply(allowed=False, submit_type=None, exec_type=None, raw_value=None)

    text = value.strip()
    match = _JOINT_PATTERN.match(text)
    submit_type = match.group("submit") if match else None
    exec_type = match.group("exec").strip() if match else text

    allowed = "불허" not in exec_type
    return JointSupply(allowed=allowed, submit_type=submit_type, exec_type=exec_type, raw_value=text)
