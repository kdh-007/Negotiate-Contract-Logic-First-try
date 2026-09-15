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
    # 이 판정이 어디서 나왔는지. "API"(면허제한정보) 또는 "첨부파일 텍스트"
    # (evaluate_from_text_items 참고) — 후자는 정규식 기반이라 API보다
    # 신뢰도가 낮으므로 리포트에 출처를 구분해 보여준다.
    source: str = "API"
    # API·텍스트 양쪽 다 코드 기반 판정을 못 했을 때(checked=False), 첨부파일에서
    # 그래도 "입찰 참가자격" 절 자체는 찾았다면 그 요약. 사람이 확인해야 할
    # 정보가 있다는 것만 알려준다 (attachments.resolve_missing_qualifications 참고).
    attachment_note: str | None = None

    @property
    def missing_count(self) -> int:
        return len(self.missing_groups)

    @property
    def summary(self) -> str:
        if not self.checked:
            if self.attachment_note:
                return f"자격정보 없음(API) — 첨부파일 확인: {self.attachment_note}"
            return "자격정보 없음 (판정 보류, 통과)"
        prefix = "" if self.source == "API" else f"[{self.source} 기준] "
        if self.missing_count == 0:
            return f"{prefix}자격 충족 ({self.total_groups}개 그룹 전부)"
        return f"{prefix}미충족 {self.missing_count}/{self.total_groups} 그룹"


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


def load_held_names(held_config: dict) -> list[str]:
    names: list[str] = []
    for key in ("heldProducts", "heldIndustries"):
        for entry in held_config.get(key, []):
            name = str(entry.get("name", "")).strip()
            if name:
                names.append(name)
    return names


def load_held_codes(held_config: dict) -> tuple[set[str], set[str]]:
    """(보유 업종코드 집합, 보유 세부품명번호 집합)을 반환한다.

    첨부파일 텍스트에서 코드를 직접 뽑아 대조하는 용도 — `evaluate_from_text_items` 참고.
    """
    industry_codes = {str(e.get("code", "")).strip() for e in held_config.get("heldIndustries", [])}
    product_codes = {str(e.get("code", "")).strip() for e in held_config.get("heldProducts", [])}
    return industry_codes - {""}, product_codes - {""}


# 괄호/대괄호 안에 든 내용 하나를 통째로 뽑는다 ("업종코드: 4442", "세부품명번호
# 10자리, 4924159701" 처럼 라벨이 코드와 같이 있는 경우까지 잡기 위해 내용 전체를
# 먼저 떼어낸 뒤, 그 안에서 숫자만 다시 찾는다.
_BRACKET_CONTENT_RE = re.compile(r"[(\[]([^()\[\]]{0,40}?)[)\]]")
_DIGIT_RUN_RE = re.compile(r"\d+")
# 4자리 순수 숫자가 연도로 보이면(예: "(2026. 12. 15.)") 업종코드로 착각하면 안 된다.
_YEAR_RANGE = range(2000, 2100)
# 참가자격 항목 안에 담당 부서 연락처가 괄호로 같이 적힌 경우가 있다
# (실측: "(안성시청 문화관광과 관광팀, ☎031-678-2492)") — 전화번호의 마지막
# 4자리가 업종코드로 오인되지 않도록 코드를 찾기 전에 전화번호부터 지운다.
_PHONE_LIKE_RE = re.compile(r"\d{2,4}[-.]\d{3,4}[-.]\d{4}")


def _extract_candidate_codes(text: str) -> list[str]:
    """괄호/대괄호 안의 4자리(업종코드)·10자리(세부품명번호) 숫자만 후보로 뽑는다."""
    codes: list[str] = []
    for content in _BRACKET_CONTENT_RE.findall(text):
        content = _PHONE_LIKE_RE.sub(" ", content)
        for run in _DIGIT_RUN_RE.findall(content):
            if len(run) == 4:
                if int(run) in _YEAR_RANGE:
                    continue
                codes.append(run)
            elif len(run) == 10:
                codes.append(run)
    return codes


def evaluate_from_text_items(
    items: list[str], held_industry_codes: set[str], held_product_codes: set[str]
) -> QualificationResult:
    """첨부파일에서 뽑은 참가자격 항목들을 업종코드/세부품명번호 기준으로 판정한다.

    면허제한정보 API가 비어 있을 때의 보완 로직이다(PoC4: 등록업종 스캔 규칙).
    항목 하나 = 그룹 하나로 보고, 그 안의 괄호/대괄호 숫자 중 보유 코드와
    하나라도 일치하면 충족(OR) — API 판정과 동일한 규칙(`MAX_ALLOWED_MISSING_QUALIFICATIONS`)을
    그대로 적용한다.

    "중소기업 확인서 소지" 같은 코드가 아예 없는 항목은 뭘 보유해야 하는지 알
    방법이 없으므로 판정 대상에서 빼고 넘어간다 — 실적·신용평가등급처럼 코드로
    표현되지 않는 요건도 마찬가지라 아직은 자동 판정하지 못한다(PoC4 이후 과제).
    코드가 있는 항목이 하나도 없으면 API가 비었을 때와 동일하게 fail-open으로
    통과시킨다(checked=False) — 정규식이 못 찾았다고 무단으로 탈락시키지 않는다.
    """
    groups: list[LicenseGroup] = []
    missing: list[LicenseGroup] = []

    for i, item in enumerate(items, start=1):
        codes = _extract_candidate_codes(item)
        if not codes:
            continue
        group = LicenseGroup(group_no=f"텍스트{i}", allowed_names=codes)
        groups.append(group)
        if not any(code in held_industry_codes or code in held_product_codes for code in codes):
            missing.append(group)

    if not groups:
        return QualificationResult(total_groups=0, missing_groups=[], passes=True, checked=False)

    return QualificationResult(
        total_groups=len(groups),
        missing_groups=missing,
        passes=len(missing) <= MAX_ALLOWED_MISSING_QUALIFICATIONS,
        checked=True,
        source="첨부파일 텍스트",
    )


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
