"""과거 실적·제안서와의 유사도 점수 산정.

로드맵(`narabid.md`) 4~5단계 — "회사 보유 실적과 조건 비교", "유사 과거 제안서 검색" —
의 1차 구현이다. 세 축으로 나눠 판단한다. 하나로 뭉뚱그리면 담당자가 "왜 이 점수인지"
알 수 없어 `qual-dot`처럼 근거를 못 보여준다.

  - 업역 적합도(structural)   : 세부품명번호/업종명이 겹치는가 (codes.json과 같은 매칭 축)
  - 규모 적합도(track_record) : 비슷한 예산 규모를 감당한 과거 실적이 있는가 (로그스케일 근접도)
  - 내용 유사도(text)         : 공고 제목/품명 vs 과거 사업 핵심 내용(과업개요·과업내용 절)이
                                 얼마나 겹치는가 (`_overlap_coefficient`. 임베딩으로 바꿀 때
                                 이 함수 자리만 교체하면 된다 — 그래서 별도로 뺐다)

세 점수를 가중합해 0~100 종합점수를 낸다. `DEFAULT_WEIGHTS`는 아직 실측으로 검증된 값이
아니라 동일가중치로 시작한 것이다(2026-09-18 논의) — 실제로 점수를 매겨보고 담당자가
"이건 낮게/높게 나와야 하는데" 하는 건이 생기면 그때 조정한다.

과거 실적은 `config/past_projects.json`에서 읽는다(키워드/코드 config와 같은 패턴 — 코드
수정 없이 파일만 고치면 반영됨). 2026-09-22 기준 실측 81건이 들어 있지만 `summaryText`
(원문 전체 텍스트)만 채워져 있고 `amount`/`industryNames`/`productCodes`는 전부 비어
있다 — 그래서 지금은 업역/규모 두 축이 항상 0점이고 내용(text) 축 하나로만 점수가 갈린다
(CLAUDE.md "월요일에 이어갈 것" 2번 항목 — 과거실적 데이터를 실제로 채우는 작업이 아직임).

**내용(text) 축 알고리즘 변경(2026-09-22, `python -m nego.similarity_cli`로 81건 실제
돌려보다 발견)**: 처음엔 자카드(교집합/합집합)를 썼는데, 공고 제목은 토큰 4~6개인 반면
과거 실적의 "과업내용" 절은 수천 토큰이라 분모(합집합)가 과거 문서 어휘량에 압도돼 진짜
일치하는 사례도 0.01~0.02점으로 묻혔다(실측: "울산과학관 전시체험물 교체 사업"을 그대로
질의해도 자기 자신이 1위로 안 나옴). 겹침계수(Szymkiewicz–Simpson, 교집합/min(|A|,|B|))로
바꿔 분모를 항상 더 작은 쪽(대개 공고 제목)에 맞췄다 — "공고 제목의 단어가 과거 문서 안에
실제로 있는가"를 직접 잰다.

**공통 토큰 필터도 같은 실측에서 추가**: 겹침계수로 바꾸니 이번엔 "포함·설치·있는·제작·
사업" 같은 조사/발주 보일러플레이트 단어가 우연히 겹쳐서 무관한 과거 실적이 진짜 일치
건과 동점을 먹는 현상이 나왔다. 손으로 불용어 목록을 짜는 대신 실제 81건에서 문서빈도를
재봤다 — "사업"은 83%, "포함"은 90% 문서에 등장, 즉 사실상 전체 corpus에 있는 단어라
겹쳐도 신호가 아니다(`_common_tokens`). 과거 실적 절반 이상에 등장하는 토큰은 두 집합
모두에서 제외하고 겹침계수를 계산한다.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import section_text
from .models import Notice

_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")

# summary_text는 원문 전체(목차·행정절차·서식 안내까지 포함)라 그대로 토큰화하면
# 노이즈가 신호를 희석시킨다. `section_text.find_section`으로 내용이 실제로 담긴
# 절만 뽑아 쓴다 — 절 구조를 못 찾거나 이 키워드에 해당하는 절이 없으면(fail-open
# 문서 등) summary_text 전체로 되돌아간다(`_content_text` 참고).
_CONTENT_SECTION_KEYWORDS = ["사업개요", "과업내용", "과업범위", "사업내용", "사업목적"]


def tokenize(text: str | None) -> set[str]:
    """비교용 토큰 집합. 한 글자 토큰(조사·단위 등)은 잡음이라 제외한다."""
    return set(_TOKEN_RE.findall(text or ""))


@dataclass
class PastProject:
    """회사가 과거에 수행한 사업 1건 (실적/제안서 요약).

    `product_codes`는 물품 공고(`notice.product_class_no`)와 정확일치로,
    `industry_names`는 용역/공사 공고(`notice.industry_text`)와 부분일치로 비교한다
    (`codes.json`의 productCodes/industryCodes와 같은 매칭 방식).
    """

    title: str
    institution: str | None = None
    amount: float | None = None
    tags: list[str] = field(default_factory=list)
    product_codes: list[str] = field(default_factory=list)
    industry_names: list[str] = field(default_factory=list)
    summary_text: str = ""

    @property
    def tokens(self) -> set[str]:
        return tokenize(self.title) | tokenize(_content_text(self)) | {t.strip() for t in self.tags if t.strip()}


def _content_text(past: PastProject) -> str:
    """`past.summary_text`에서 노이즈를 뺀 핵심 내용만 돌려준다(있으면).

    "사업개요/과업내용" 절을 찾으면 그 절(제목+본문)만 쓰고, 못 찾으면
    summary_text 전체로 되돌아간다 — 있는 신호를 버리느니 노이즈 섞인 신호라도
    쓰는 편이 낫다(`section_text.py`의 fail-open 철학과 동일).
    """
    section = section_text.find_section(past.summary_text, _CONTENT_SECTION_KEYWORDS)
    if section is not None and section.body.strip():
        return f"{section.heading}\n{section.body}"
    return past.summary_text


@dataclass
class SimilarityResult:
    """공고 1건에 대한 유사도 판정. 과거 실적이 하나도 없으면 매칭 대상이 없다는 뜻이라
    score=0, matched=None으로 둔다 — 임의로 중립값(50점 등)을 주지 않는다."""

    score: float  # 0~100 종합점수
    structural_score: float  # 0~1 — 업역(코드) 적합도
    track_record_score: float  # 0~1 — 규모(예산) 적합도
    text_score: float  # 0~1 — 내용 유사도
    matched: PastProject | None  # 종합점수가 가장 높았던 과거 사업(근거 설명용)

    @property
    def label(self) -> str:
        """리포트 등에 바로 쓸 수 있는 한 줄 요약."""
        if self.matched is None:
            return "비교할 과거 실적 없음"
        return f"{self.score:.0f}점 — 유사 사업: {self.matched.title}"


DEFAULT_WEIGHTS: dict[str, float] = {"structural": 1 / 3, "track_record": 1 / 3, "text": 1 / 3}


def _budget_proximity(a: float | None, b: float | None) -> float:
    """예산 규모가 로그스케일로 얼마나 가까운지 0~1.

    실측 참고(2026-09-18 이번주 수집): 후보 공고들이 1억~12억대로 10배 넘게 벌어져
    있어 선형 비교는 부적절하다 — 10배 차이면 0점, 같으면 1점이 되도록 로그로 잡는다.
    둘 중 하나라도 없으면 0으로 둔다(모르는 걸 중립으로 봐주면 실적 없는 신규 분야까지
    과대평가된다).
    """
    if not a or not b or a <= 0 or b <= 0:
        return 0.0
    ratio = math.log(max(a, b) / min(a, b))
    return max(0.0, 1 - ratio / math.log(10))


def _code_overlap(notice: Notice, past: PastProject) -> bool:
    if notice.product_class_no and notice.product_class_no in past.product_codes:
        return True
    industry_text = notice.industry_text or ""
    return any(name and name in industry_text for name in past.industry_names)


def _overlap_coefficient(a: set[str], b: set[str]) -> float:
    """겹침계수(Szymkiewicz–Simpson) — 교집합을 "더 작은 쪽" 크기로 나눈다.

    일반 자카드(교집합/합집합)는 두 집합 크기가 비슷할 때 적합하다. 여기서는
    공고 제목(작음)과 과거 실적 내용 절(큼)을 비교하므로, 합집합을 쓰면 분모가
    큰 쪽 어휘량에 압도된다 — 위 모듈 docstring "내용(text) 축 알고리즘 변경"
    항목 참고.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _notice_tokens(notice: Notice) -> set[str]:
    return tokenize(notice.title) | tokenize(notice.product_class_name)


# 이 비율 이상의 과거 실적 문서에 등장하는 토큰은 "공통 보일러플레이트"로 보고
# 내용 유사도 계산에서 제외한다. 0.5로 잡은 근거는 `_common_tokens` docstring 참고.
_COMMON_TOKEN_DOC_FREQ_THRESHOLD = 0.5
# 문서빈도 통계는 표본이 작으면 의미가 없다 — 과거 실적 1~2건짜리 corpus에서는
# 그 문서에 있는 단어가 전부 "100% 등장"이 되어 사실상 모든 토큰이 걸러진다.
# 이 정도는 돼야(실측 81건 기준 절반에 못 미치지만, 최소한 우연한 전수일치는
# 피할 수 있는 크기로) 문서빈도가 통계로서 뜻을 가진다.
_MIN_CORPUS_SIZE_FOR_COMMON_TOKEN_FILTER = 5


def _common_tokens(past_projects: list[PastProject]) -> frozenset[str]:
    """과거 실적 corpus 절반 이상의 문서에 등장하는 토큰 — 조사/연결어(포함·있는·
    따라·대한 등)와 발주 절차 보일러플레이트(사업·계약·제출·작성 등)라 겹쳐도 내용이
    비슷하다는 신호가 아니다. 손으로 고른 불용어 목록 대신 실제 corpus 문서빈도로
    직접 구한다 — 실측(2026-09-22, 81건): "사업" 83%, "포함" 90% 문서에 등장.
    """
    if len(past_projects) < _MIN_CORPUS_SIZE_FOR_COMMON_TOKEN_FILTER:
        return frozenset()
    doc_freq: Counter[str] = Counter()
    for past in past_projects:
        doc_freq.update(past.tokens)
    threshold = len(past_projects) * _COMMON_TOKEN_DOC_FREQ_THRESHOLD
    return frozenset(tok for tok, count in doc_freq.items() if count >= threshold)


def _score_one(
    notice: Notice, past: PastProject, weights: dict[str, float], common_tokens: frozenset[str] = frozenset()
) -> tuple[float, float, float, float]:
    structural = 1.0 if _code_overlap(notice, past) else 0.0
    track_record = _budget_proximity(notice.budget, past.amount)
    text = _overlap_coefficient(_notice_tokens(notice) - common_tokens, past.tokens - common_tokens)
    total = weights["structural"] * structural + weights["track_record"] * track_record + weights["text"] * text
    return total, structural, track_record, text


def score(
    notice: Notice,
    past_projects: list[PastProject],
    weights: dict[str, float] | None = None,
) -> SimilarityResult:
    """공고 1건을 과거 실적 목록 전체와 비교해, 가장 유사한 건 기준으로 점수를 낸다.

    평균이 아니라 최댓값을 쓴다 — "이런 걸 한 번이라도 해봤는가"가 중요하지, 무관한
    과거 사업들과 평균 내면 실제로 딱 맞는 실적 하나가 희석되어 버린다.
    """
    weights = weights or DEFAULT_WEIGHTS
    if not past_projects:
        return SimilarityResult(score=0.0, structural_score=0.0, track_record_score=0.0, text_score=0.0, matched=None)

    common_tokens = _common_tokens(past_projects)
    total, structural, track_record, text, matched = max(
        (( *_score_one(notice, p, weights, common_tokens), p) for p in past_projects),
        key=lambda row: row[0],
    )
    return SimilarityResult(
        score=round(total * 100, 1),
        structural_score=structural,
        track_record_score=track_record,
        text_score=text,
        matched=matched,
    )


@dataclass
class RankedMatch:
    """`rank()` 결과 1건. `SimilarityResult`는 "가장 유사한 건 하나"만 남기는
    반면, 이건 담당자가 상위 몇 건을 눈으로 비교해보는 용도라 축별 점수를 그대로
    들고 있는다."""

    past: PastProject
    score: float
    structural_score: float
    track_record_score: float
    text_score: float


def rank(
    notice: Notice,
    past_projects: list[PastProject],
    weights: dict[str, float] | None = None,
    top_n: int = 5,
) -> list[RankedMatch]:
    """공고 1건을 과거 실적 전체와 비교해 유사도 상위 `top_n`건을 점수 내림차순으로
    돌려준다. `score()`는 근거 설명용으로 최댓값 1건만 남기지만, 이건 "그 다음으로
    비슷한 건 뭐였지" 같은 프로토타입 탐색에 쓴다."""
    weights = weights or DEFAULT_WEIGHTS
    common_tokens = _common_tokens(past_projects)
    scored = [
        RankedMatch(
            past=past,
            score=round(total * 100, 1),
            structural_score=structural,
            track_record_score=track_record,
            text_score=text,
        )
        for past in past_projects
        for total, structural, track_record, text in [_score_one(notice, past, weights, common_tokens)]
    ]
    scored.sort(key=lambda m: m.score, reverse=True)
    return scored[:top_n]


def load_past_projects(raw: dict[str, Any]) -> list[PastProject]:
    """`config/past_projects.json` 파싱. 필드가 없으면 빈 값으로 채운다(관대하게 읽기)."""
    out: list[PastProject] = []
    for item in raw.get("pastProjects", []):
        out.append(
            PastProject(
                title=item.get("title", ""),
                institution=item.get("institution"),
                amount=item.get("amount"),
                tags=list(item.get("tags", [])),
                product_codes=list(item.get("productCodes", [])),
                industry_names=list(item.get("industryNames", [])),
                summary_text=item.get("summaryText", ""),
            )
        )
    return out


def load_past_projects_file(path: Path) -> list[PastProject]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return load_past_projects(raw)
