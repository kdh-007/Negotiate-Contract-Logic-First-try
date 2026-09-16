"""자격조건(면허·업종 제한) 판정.

**판정 규칙은 기존 시스템(`matching/qualificationFilter.ts`)과 동일하게 유지한다.**
회사가 정한 사업 판단이므로 임의로 바꾸지 않는다.

  - 제한그룹(`lmtGrpNo`) 하나가 자격조건 하나의 단위
  - 그룹 안에 여러 업종이 나열되면 그중 하나만 보유해도 그 그룹은 충족(OR)
  - 충족하지 못한 그룹이 2개 이상이면 제외 (1개까지는 통과)
  - 조회 실패 / 정보 없음이면 걸러내지 않고 통과 (fail-open)

공동수급·지역은 **판정에 개입하지 않고 정보로만** 수집한다.

API 면허제한정보가 비어 있는 공고는 `evaluate_attachment_text`가 첨부파일
참가자격 절에서 뽑은 업종코드·세부품명번호로 같은 형태의 판정을 만든다
(`attachments.save_attachment_texts`가 호출). 이 판정은 표시만 갱신하고
후보 목록 자체(포함/제외)는 바꾸지 않는다 — 이미 API 기반 1차 판정이 끝난
뒤에 붙는 보조 재판정이기 때문이다.
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
        """지일이 요구 자격을 다 가지고 있으면 '자격 충족', 아니면 미보유 자격증 이름을 붙여
        '자격 미달(이름)'로 표시한다 — 담당자가 그룹/카운트 계산 없이 바로 알아볼 수 있게."""
        if not self.checked:
            return "자격정보 미확인 (통과)"
        if self.missing_count == 0:
            return "자격 충족"
        # 같은 자격이 여러 그룹에서 각각 미충족으로 걸리면(예: 첨부문서 항목
        # 여러 개가 같은 코드를 요구) 이름표가 그대로 중복 표시된다 — 중복 제거.
        names = ("/".join(g.allowed_names) for g in self.missing_groups)
        missing_names = ", ".join(dict.fromkeys(names))
        return f"자격 미달({missing_names})"


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


# "(업종코드 4990)" / "(세부품명번호 6010989901)" / "(디지털콘텐츠개발서비스사업,
# 업종코드 1469)" — 참가자격 문서가 실측상 이 표기로 업종·품목을 명시한다.
# held_qualifications.json의 code와 그대로 비교할 수 있다. 실측: 일부 문서는
# "세부품명번호 10자리, 4924159701"처럼 자릿수 설명을 코드 앞에 끼워 넣는다 —
# 그 필러를 건너뛰지 않으면 "10"을 코드로 잘못 잡는다. 업종코드 4자리,
# 세부품명번호 10자리라 4~10자리로 캡처를 제한해 이런 오탐도 같이 막는다.
#
# 두 번째 대안은 키워드 없이 "이름(코드10자리)"만 쓰는 문서용이다(실측: 두바이
# 의료기기전시회 한국관 공고문 — "전시부스설치서비스(7215409901)"). 정확히
# 10자리로 제한해 일반 괄호 안 숫자(연도·조항 번호 등)를 코드로 오인하지
# 않게 한다.
_CODE_REQUIREMENT_RE = re.compile(
    r"(?:업종코드|세부품명번호)\s*(?:[0-9]+\s*자리\s*,?\s*)?(?P<code>[0-9]{4,10})"
    r"|\((?P<bare_code>[0-9]{10})\)"
)
# 문서마다 괄호를 쓰는지 대괄호를 쓰는지, 코드 여러 개를 콤마로 나열하는지
# 세미콜론이나 공백으로 나열하는지가 제각각이다 — 문서마다 정규식을 하나씩
# 추가하는 대신, "괄호/대괄호 하나 안에 코드가 몇 개 있든, 구분자가 뭐든
# 전부 뽑는다"로 일반화한다. "세부품명번호 10자리(코드1 이름1, 코드2 이름2)"처럼
# 프리픽스 바로 뒤에 괄호가 오면(키워드로 확신 가능) 4~10자리를 코드로 보고,
# "[코드, 이름]"처럼 키워드 없이 괄호/대괄호만 쓰면(실측: 직접생산확인증명서
# 항목) 세부품명번호 자릿수(정확히 10자리)일 때만 코드로 인정한다 — 키워드가
# 없어 짧은 숫자는 법조문·연도 인용과 구분할 수 없기 때문이다.
_PREFIXED_GROUP_RE = re.compile(
    r"(?:업종코드|세부품명번호)\s*(?:[0-9]+\s*자리\s*,?\s*)?[(\[]([^()\[\]]*)[)\]]"
)
_BARE_GROUP_RE = re.compile(r"[(\[]([^()\[\]]*)[)\]]")
_GROUP_ENTRY_CODE_RE = re.compile(r"[0-9]{4,10}")
_BARE_GROUP_ENTRY_CODE_RE = re.compile(r"[0-9]{10}")
_GROUP_ENTRY_STRIP_CHARS = " ,·/;、"
_OR_MARKER_RE = re.compile(r"어느\s*하나")
# 이름표에서 떼어낼 법령 인용 연결어. 실측 문서마다 표현이 달라 여러 개를 다룬다.
_LABEL_CONNECTOR_RE = re.compile(r"(?:에\s*따른|에\s*의하여|규정에\s*따라)\s*")
_MAX_LABEL_LEN = 20


def _truncate_label(name: str) -> str:
    if len(name) > _MAX_LABEL_LEN:
        name = name[-_MAX_LABEL_LEN:]
        if " " in name:  # 잘린 앞 단어 조각을 버리고 온전한 단어부터 남긴다
            name = name.split(" ", 1)[1]
    return name


def _split_group_entries(content: str, code_re: re.Pattern) -> list[tuple[str, str]]:
    """괄호/대괄호 안 내용에서 코드-이름 쌍을 뽑는다. 콤마·세미콜론·공백 등
    구분자가 무엇이든 상관없이, 코드 숫자 뒤부터 다음 코드 앞까지를 그
    코드의 이름표로 본다(실측 표기가 "코드 이름, 코드 이름" 순이라 이렇게
    자르면 이름이 온전히 남는다)."""
    matches = list(code_re.finditer(content))
    pairs = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        name = content[match.end() : end].strip(_GROUP_ENTRY_STRIP_CHARS)
        pairs.append((match.group(0), name))
    return pairs


def _spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and a[1] > b[0]


def _extract_code_requirements(item: str) -> list[tuple[str, str]]:
    """항목 한 줄에서 코드 표기가 붙은 요건을 [(코드, "이름(코드)" 이름표)]로 뽑는다.

    이름표는 두 가지 표기 관행을 다룬다 — "이름(업종코드 ####)"(괄호 앞이 이름)와
    "(설명, 업종코드 ####)"(괄호 안 콤마 앞이 이름). 전자는 괄호 앞 구절에서
    "…법에 따른/의하여" 같은 인용부를 떼고 남은 마지막 구절을 이름표로 쓴다.
    실측 문서 문장이 다양해 완벽히 못 떼어낼 수 있으니, 그래도 너무 길면
    (`_MAX_LABEL_LEN`) 뒷부분만 잘라 쓴다 — 문장 전체가 그대로 리포트에
    나오는 것보다는, 한글 문장 특성상 대상 명사가 대개 끝에 오므로 뒷부분만
    잘라도 알아볼 수 있는 경우가 많다.

    세 단계로 나눠 뽑고, 뒤 단계는 앞 단계가 이미 잡은 구간을 다시 건드리지
    않는다(겹치면 건너뜀) — 문서마다 괄호/대괄호·구분자 표기가 제각각이라
    형태별로 정규식을 계속 추가하는 대신, 우선순위가 있는 일반 규칙으로
    처리한다.
      1. `_PREFIXED_GROUP_RE`: 프리픽스 바로 뒤 괄호/대괄호 — 키워드로 코드임을
         확신할 수 있어 하나든 여러 개든, 구분자가 뭐든 전부 뽑는다.
      2. `_CODE_REQUIREMENT_RE`: 프리픽스 뒤 코드 하나(괄호 없이), 또는 정확히
         10자리 숫자만 있는 단독 괄호 — 기존 형태, 이름표 추출 로직도 그대로.
      3. `_BARE_GROUP_RE`: 위 두 규칙이 건드리지 않은 나머지 모든 괄호/대괄호 —
         키워드가 없어 확신할 수 없으므로 정확히 10자리(세부품명번호 자릿수)인
         경우만 코드로 인정한다.
    """
    results = []
    for line in item.splitlines():
        consumed: list[tuple[int, int]] = []

        for group_match in _PREFIXED_GROUP_RE.finditer(line):
            consumed.append(group_match.span())
            for code, name in _split_group_entries(group_match.group(1), _GROUP_ENTRY_CODE_RE):
                name = _truncate_label(name)
                results.append((code, f"{name}({code})" if name else code))

        for match in _CODE_REQUIREMENT_RE.finditer(line):
            if any(_spans_overlap(match.span(), span) for span in consumed):
                continue
            consumed.append(match.span())
            code = match.group("code") or match.group("bare_code")
            prefix = line[: match.start()]
            inside_paren = prefix.rsplit("(", 1)[1].strip(" ,") if "(" in prefix else ""
            if inside_paren and len(inside_paren) <= _MAX_LABEL_LEN:
                name = inside_paren
            else:
                before_paren = prefix.rsplit("(", 1)[0] if "(" in prefix else prefix
                before_paren = re.sub(r"^[\s\-·「『]+", "", before_paren)
                segments = [s for s in _LABEL_CONNECTOR_RE.split(before_paren) if s.strip()]
                name = (segments[-1] if segments else before_paren).strip()
                name = _truncate_label(name)
            results.append((code, f"{name}({code})" if name else code))

        for bare_match in _BARE_GROUP_RE.finditer(line):
            if any(_spans_overlap(bare_match.span(), span) for span in consumed):
                continue
            for code, name in _split_group_entries(bare_match.group(1), _BARE_GROUP_ENTRY_CODE_RE):
                name = _truncate_label(name)
                results.append((code, f"{name}({code})" if name else code))
    return results


def evaluate_attachment_text(items: list[str], held_codes: set[str]) -> QualificationResult:
    """첨부파일 참가자격 절에서 업종코드·세부품명번호가 명시된 항목만 뽑아,
    API 판정(`evaluate`)과 같은 형태의 결과를 만든다 — 리포트에서 "자격 충족" /
    "자격 미달(이름)"로 API 기반 판정과 똑같이 보이게 하기 위함이다.

    코드가 안 붙은 일반 결격사유(나라장터 등록 여부, 부정당업자 여부, 공동수급
    구성 방식 등)는 판정 대상에서 뺀다 — 문장만 보고 "이게 자격요건이다"를
    추정하면 오탈락 위험이 크지만, 코드는 문서에 명시된 값 그대로라 비교가
    정확하다. "다음 중 어느 하나"가 있으면 그 항목 안 코드 중 하나만 있어도
    충족(OR), 없으면 나열된 코드를 전부 가지고 있어야 충족(AND)으로 본다
    (실측: 정선군 복합문화센터 공고문 — "라"항은 품목 3개를 모두 소지해야
    하고, "바"항은 "다음 중 어느 하나"로 명시됨).
    """
    groups: list[LicenseGroup] = []
    missing: list[LicenseGroup] = []

    for idx, item in enumerate(items):
        requirements = _extract_code_requirements(item)
        if not requirements:
            continue

        group_no = str(idx)
        groups.append(LicenseGroup(group_no=group_no, allowed_names=[label for _, label in requirements]))

        is_or = bool(_OR_MARKER_RE.search(item))
        held_flags = [code in held_codes for code, _ in requirements]
        satisfied = any(held_flags) if is_or else all(held_flags)
        if not satisfied:
            missing_labels = (
                [label for _, label in requirements]
                if is_or
                else [label for (_, label), ok in zip(requirements, held_flags) if not ok]
            )
            missing.append(LicenseGroup(group_no=group_no, allowed_names=missing_labels))

    if not groups:
        return QualificationResult(total_groups=0, missing_groups=[], passes=True, checked=False)

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


def load_held_codes(held_config: dict) -> set[str]:
    codes: set[str] = set()
    for key in ("heldProducts", "heldIndustries"):
        for entry in held_config.get(key, []):
            code = str(entry.get("code", "")).strip()
            if code:
                codes.add(code)
    return codes


def fetch_license_groups(
    client: DataGoKrClient, begin: str, end: str
) -> tuple[dict[str, list[LicenseGroup]], str | None]:
    """조회기간 전체의 면허제한정보를 받아 공고번호별로 묶어 반환한다.

    실패해도 예외를 올리지 않는다 — 호출측에서 fail-open으로 처리하기 위함.

    주의: 이 오퍼레이션은 조회기간이 너무 길면 resultCode=07(입력범위값 초과)로
    거부한다. 실측상 30일은 되고 그 이상(180일은 물론 ~100일도)은 막힌다 —
    정확한 상한은 모른다. `client.fetch_all_pages_chunked`가 안전한 범위
    단위로 나눠 호출하므로 LOOKBACK_DAYS를 30일보다 길게 잡아도 된다.
    그래도 한 청크가 실패하면 이 기간 내 **모든** 공고가 "자격정보
    없음(판정 보류)"로 처리된다(`scripts/inspect_notice.py`로 먼저 범위를
    확인해볼 수 있다).
    """
    try:
        raw_items = client.fetch_all_pages_chunked(
            F.BID_NOTICE_BASE_URL,
            F.LICENSE_LIMIT_OPERATION,
            {"inqryDiv": "1"},
            begin,
            end,
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
        raw_items = client.fetch_all_pages_chunked(
            F.BID_NOTICE_BASE_URL,
            F.REGION_LIMIT_OPERATION,
            {"inqryDiv": "1"},
            begin,
            end,
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
