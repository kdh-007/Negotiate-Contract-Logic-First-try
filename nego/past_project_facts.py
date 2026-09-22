"""과거 실적 원문(`PastProject.summary_text`)에서 규모(예산)·발주기관·업역
(업종코드/세부품명번호)을 뽑아 `config/past_projects.json`의 amount/institution/
industryNames/productCodes를 채운다.

사용자 지적(2026-09-22)으로 시작: "내가 보내준 자료에 수주 사업의 업역과 규모
(예산) 모두 포함되어 있었잖아" — 맞다. `nego/similarity.py`의 업역/규모 두 축이
지금까지 항상 0점이었던 건 데이터가 없어서가 아니라, 이미 가진 원문(summaryText)
에서 그 필드들을 뽑는 코드가 없었기 때문이다. 이 모듈이 그 공백을 메운다.

핵심 아이디어: 과거 실적은 지일이 **낙찰받은** 공고의 제안요청서다 — 그 공고
자신의 "참가자격" 절이 요구하는 업종코드/세부품명번호는, 지일이 그 자격을
가지고 있었다는 뜻이다(안 그러면 애초에 낙찰을 못 받았을 것). 그래서 라이브
공고의 첨부파일에서 참가자격을 찾아 판정하는 기존 로직(`qualification_text.
find_qualification_section` + `qualify._extract_code_requirements`)을 과거
실적 원문에 그대로 재사용할 수 있다 — 새 파서를 또 만들 필요가 없다.

실측(2026-09-22, 81건 전수):
  - **예산**: "사업예산 : 150,000천원"(KOSCOM), "총사업비 : 금1,000,000,000원"
    (둘리테마거리) 처럼 라벨+콜론+숫자+단위 꼴이 27/81건에서 발견됐다. "사업비"
    라는 단어 자체는 훨씬 많이 등장하지만("사업비 증액은 없다" 같은 행정 문구)
    콜론 없이 라벨만 나오는 문장은 실제 금액이 아니라서 제외한다
    (`_BUDGET_LABEL_RE`가 콜론을 필수로 요구).
  - **발주기관**: "발주기관 :"/"수요기관 :" 콜론 표기가 있는 문서는 4/81건뿐이다
    (지평전투기념관→양평군청 등, 실측 확인). 콜론을 선택적으로 두면("발주
    기관은 본 과업 수행에 필요하다고 판단하는 경우...") 문장 중간의 "발주기관"
    이란 단어를 전부 institution으로 오인하게 된다(실측 확인 — 첫 시도에서
    76/81건이 "매칭"됐지만 전부 문장 조각이었다) — 콜론을 반드시 요구해 이
    오탐을 막는다.
  - **업역(업종코드/세부품명번호)**: 참가자격 절이 있는 37/81건 중 4건에서
    코드를 뽑아냈다(인천아트시티조성사업 등). 세부품명번호(10자리)는 코드
    그대로 쓰면 되지만, 업종코드(4자리)는 코드만으론 `similarity._code_overlap`
    이 기대하는 "이름" 형태가 아니다 — `config/held_qualifications.json`
    (지일이 실제 보유한 자격 등록증 목록, `qualify.load_held_code_names`가
    쓰는 것과 같은 파일)로 code→name을 되짚어서, 지일이 실제 보유 중인 코드일
    때만 이름을 채운다. 등록증에 없는 코드는 이름을 지어내지 않고 버린다
    (fail-open — 틀린 이름을 넣느니 비워두는 게 낫다).
"""

from __future__ import annotations

import re

from . import qualify
from .qualification_text import find_qualification_section, split_items

# "사업예산 : 150,000천원(부가가치세 별도)" / "총사업비 : 금1,000,000,000원"처럼
# 라벨 뒤에 콜론이 와야만 인정한다 — 콜론이 없으면 "사업비 증액은 없다" 같은
# 행정 문구 속 단어와 구분할 수 없다(실측: `발주기관` 오탐과 같은 종류의 문제).
_BUDGET_LABEL_RE = re.compile(
    r"(?:총\s*사업비|사업\s*예산|사업\s*비|계약\s*금액|예산\s*액)\s*[:：]\s*(?:금)?\s*([0-9][0-9,]*)\s*(천원|백만원|원)"
)
_BUDGET_UNIT_MULTIPLIERS = {"천원": 1_000, "백만원": 1_000_000, "원": 1}

# 콜론을 필수로 요구한다(실측 근거는 위 모듈 docstring "발주기관" 항목 참고).
_INSTITUTION_RE = re.compile(r"(?:발주\s*기관|수요\s*기관)\s*[:：]\s*([^\n]{1,30})")


def extract_amount(text: str) -> float | None:
    match = _BUDGET_LABEL_RE.search(text)
    if not match:
        return None
    digits, unit = match.group(1), match.group(2)
    return float(digits.replace(",", "")) * _BUDGET_UNIT_MULTIPLIERS[unit]


def extract_institution(text: str) -> str | None:
    match = _INSTITUTION_RE.search(text)
    if not match:
        return None
    name = match.group(1).strip()
    return name or None


def extract_codes(text: str, held_code_names: dict[str, str]) -> tuple[list[str], list[str]]:
    """(productCodes, industryNames)를 돌려준다.

    참가자격 절을 못 찾거나 코드가 없으면 둘 다 빈 리스트 — "이 문서는 코드
    정보가 없다"는 뜻이지 "업역이 없다"는 뜻이 아니므로, 호출측은 이 결과로
    기존 값을 지우지 말고 값이 있을 때만 채워야 한다.
    """
    section = find_qualification_section(text)
    if section is None:
        return [], []

    product_codes: set[str] = set()
    industry_names: set[str] = set()
    for item in split_items(section.body):
        for code, _label in qualify._extract_code_requirements(item):
            if len(code) == 10:
                product_codes.add(code)
            elif len(code) == 4:
                name = held_code_names.get(code)
                if name:
                    industry_names.add(name)
    return sorted(product_codes), sorted(industry_names)
