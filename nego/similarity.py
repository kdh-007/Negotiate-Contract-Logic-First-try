"""과거 실적·제안서와의 유사도 점수 산정.

로드맵(`narabid.md`) 4~5단계 — "회사 보유 실적과 조건 비교", "유사 과거 제안서 검색" —
의 1차 구현이다. 세 축으로 나눠 판단한다. 하나로 뭉뚱그리면 담당자가 "왜 이 점수인지"
알 수 없어 `qual-dot`처럼 근거를 못 보여준다.

  - 업역 적합도(structural)   : 세부품명번호/업종명이 겹치는가 (codes.json과 같은 매칭 축)
  - 규모 적합도(track_record) : 비슷한 예산 규모를 감당한 과거 실적이 있는가 (로그스케일 근접도)
  - 내용 유사도(text)         : 공고 제목/품명 vs 과거 사업 제목·요약 텍스트가 얼마나 겹치는가
                                 (1차는 키워드 자카드 유사도. 임베딩으로 바꿀 때 `_jaccard` 자리만
                                 교체하면 된다 — 그래서 이 함수를 별도로 뺐다)

세 점수를 가중합해 0~100 종합점수를 낸다. `DEFAULT_WEIGHTS`는 아직 실측으로 검증된 값이
아니라 동일가중치로 시작한 것이다(2026-09-18 논의) — 실제로 점수를 매겨보고 담당자가
"이건 낮게/높게 나와야 하는데" 하는 건이 생기면 그때 조정한다.

과거 실적은 `config/past_projects.json`에서 읽는다(키워드/코드 config와 같은 패턴 — 코드
수정 없이 파일만 고치면 반영됨). 2026-09-21부로 `pipeline.build_candidates`가 매 후보마다
이 파일 기준으로 점수를 계산해 `Candidate.similarity`에 담고, `report.py`가 표의 "유사도"
칸으로 보여준다 — 코드를 다시 건드릴 필요 없이 `config/past_projects.json`에 실적을 추가할
때마다 다음 실행부터 반영된다. 2026-09-18 기준 회사 과거 자료(제안서·수행능력평가서)가
아직 구조화되지 않아 이 파일은 예시 1건만 든 빈 상태다 — 실제 항목이 없으면 모든 후보가
`SimilarityResult.no_match()`(비교할 과거 실적 없음)로 나온다. 제안서는 텍스트 레이어가
살아있어 pypdf로 바로 추출 가능함을 확인했고(수행능력평가서/기타자료는 절반가량 스캔이라
OCR 필요) — `summary_text`를 채울 때 그 텍스트를 쓰면 된다.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import Notice

_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")


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
        return tokenize(self.title) | tokenize(self.summary_text) | {t.strip() for t in self.tags if t.strip()}


@dataclass
class SimilarityResult:
    """공고 1건에 대한 유사도 판정. 과거 실적이 하나도 없으면 매칭 대상이 없다는 뜻이라
    score=0, matched=None으로 둔다 — 임의로 중립값(50점 등)을 주지 않는다."""

    score: float  # 0~100 종합점수
    structural_score: float  # 0~1 — 업역(코드) 적합도
    track_record_score: float  # 0~1 — 규모(예산) 적합도
    text_score: float  # 0~1 — 내용 유사도
    matched: PastProject | None  # 종합점수가 가장 높았던 과거 사업(근거 설명용)
    # text_score의 출처. 기본은 자카드 키워드 겹침("jaccard")이고, `ai_similarity.py`가
    # 이 축을 Claude 판단으로 교체하면 "ai"로 바뀐다 — 코드 판정과 AI 판단을 리포트에서
    # 구분해서 보여주기 위함(2026-09-18 논의: "결과엔 source=ai 표시").
    text_source: str = "jaccard"
    ai_reason: str | None = None  # text_source가 "ai"일 때 판단 근거 한두 문장

    @classmethod
    def no_match(cls) -> "SimilarityResult":
        """비교할 과거 실적이 없을 때(또는 아직 계산 전) 쓰는 중립값.

        `Candidate.similarity`의 기본값으로도 쓴다 — `config/past_projects.json`이
        비어 있으면 파이프라인 전체가 이 값 그대로 리포트에 나간다."""
        return cls(score=0.0, structural_score=0.0, track_record_score=0.0, text_score=0.0, matched=None)

    @property
    def label(self) -> str:
        """리포트 등에 바로 쓸 수 있는 한 줄 요약."""
        if self.matched is None:
            return "비교할 과거 실적 없음"
        suffix = " (AI 판단)" if self.text_source == "ai" else ""
        return f"{self.score:.0f}점 — 유사 사업: {self.matched.title}{suffix}"


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


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _notice_tokens(notice: Notice) -> set[str]:
    return tokenize(notice.title) | tokenize(notice.product_class_name)


def _score_one(notice: Notice, past: PastProject, weights: dict[str, float]) -> tuple[float, float, float, float]:
    structural = 1.0 if _code_overlap(notice, past) else 0.0
    track_record = _budget_proximity(notice.budget, past.amount)
    text = _jaccard(_notice_tokens(notice), past.tokens)
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
        return SimilarityResult.no_match()

    total, structural, track_record, text, matched = max(
        (( *_score_one(notice, p, weights), p) for p in past_projects),
        key=lambda row: row[0],
    )
    return SimilarityResult(
        score=round(total * 100, 1),
        structural_score=structural,
        track_record_score=track_record,
        text_score=text,
        matched=matched,
    )


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
