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
수정 없이 파일만 고치면 반영됨). 2026-09-18 기준 회사 과거 자료(제안서·수행능력평가서)가
아직 구조화되지 않아 이 파일은 예시 1건만 든 빈 상태로 시작한다. 제안서는 텍스트 레이어가
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
    # 참고 표시용(리포트의 "근거" 팝업에만 씀) — 신규 공고 쪽엔 면적을 파싱해 넣는
    # 곳이 아직 없어(첨부파일 파싱 미구현) 점수 계산에는 넣지 않는다. 넣으면 숫자
    # 하나 우연히 겹치는 걸로 점수가 왜곡될 위험만 있고 실제 비교 대상이 없다.
    area_note: str = ""

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

    @property
    def label(self) -> str:
        """리포트 등에 바로 쓸 수 있는 한 줄 요약."""
        if self.matched is None:
            return "비교할 과거 실적 없음"
        return f"{self.score:.0f}점 — 유사 사업: {self.matched.title}"


DEFAULT_WEIGHTS: dict[str, float] = {"structural": 1 / 3, "track_record": 1 / 3, "text": 1 / 3}

# config/past_projects.json에 발주기관·금액·업종코드·세부품명번호가 아직 없다
# (2026-09-23 기준 — 별도 API로 채울 계획). 그 상태에서 DEFAULT_WEIGHTS를 그대로
# 쓰면 업역·규모 축이 항상 0점이라 아무리 내용이 잘 맞아도 만점의 1/3(33점)을
# 못 넘는다. 그 두 축이 채워지기 전까지는 이 가중치로 내용(text) 축에만 100%를
# 몰아준다 — 데이터가 채워지면 DEFAULT_WEIGHTS로 되돌릴 것.
TEXT_ONLY_WEIGHTS: dict[str, float] = {"structural": 0.0, "track_record": 0.0, "text": 1.0}


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


def _document_frequencies(past_projects: list[PastProject]) -> dict[str, int]:
    """토큰별로 몇 개 과거사업 요약에 등장하는지. "제작"·"설치"·"사업"처럼 거의
    모든 과거사업에 등장하는 흔한 단어를 가려내기 위한 예비 집계다(`_idf`가 씀)."""
    df: dict[str, int] = {}
    for p in past_projects:
        for t in p.tokens:
            df[t] = df.get(t, 0) + 1
    return df


def _idf(token: str, df: dict[str, int], n_docs: int) -> float:
    """흔한 단어일수록(여러 과거사업에 등장) 가중치를 낮춘다. +1 스무딩으로
    처음 보는 단어(df=0)도 0이 아닌 값을 받는다."""
    return math.log((n_docs + 1) / (df.get(token, 0) + 1)) + 1.0


def _content_overlap(notice_tokens: set[str], past: PastProject, df: dict[str, int], n_docs: int) -> float:
    """공고 제목의 단어들이 과거사업 내용(과업내용 키워드+전시내용)에 얼마나
    담겨 있는지 0~1로 — 자카드(교집합/합집합)를 안 쓰는 이유는, 과거사업
    요약이 길수록(전시내용 항목이 수십 개인 사업도 있음) 합집합이 커져서
    실제로 잘 맞는 경우조차 비율이 희석되기 때문이다. 분모를 "공고 제목
    쪽 토큰 가중치 합"으로 고정하면(오버랩 계수) 과거사업 텍스트 길이에
    영향을 안 받는다. 거기에 IDF를 곱해 "제작"·"설치"처럼 흔한 단어가
    겹친 것만으로 점수가 뜨는 걸 막는다."""
    if not notice_tokens or not past.tokens:
        return 0.0
    total_weight = sum(_idf(t, df, n_docs) for t in notice_tokens)
    if total_weight == 0:
        return 0.0
    matched_weight = sum(_idf(t, df, n_docs) for t in notice_tokens if t in past.tokens)
    return matched_weight / total_weight


def _notice_tokens(notice: Notice, extra_text: str = "") -> set[str]:
    """공고 제목·품명에다, 있으면 첨부파일에서 뽑은 과업내용·전시내용
    (`extra_text` — cli.py가 candidate.content_task_text 등을 이어 붙여 넘긴다)
    까지 더한 토큰 집합. 첨부파일을 안 받았으면(`--fetch-attachment-text` 없이
    돈 경우) extra_text가 빈 문자열이라 기존과 똑같이 제목만으로 비교한다."""
    return tokenize(notice.title) | tokenize(notice.product_class_name) | tokenize(extra_text)


def _score_one(
    notice: Notice,
    past: PastProject,
    weights: dict[str, float],
    df: dict[str, int],
    n_docs: int,
    extra_text: str,
) -> tuple[float, float, float, float]:
    structural = 1.0 if _code_overlap(notice, past) else 0.0
    track_record = _budget_proximity(notice.budget, past.amount)
    text = _content_overlap(_notice_tokens(notice, extra_text), past, df, n_docs)
    total = weights["structural"] * structural + weights["track_record"] * track_record + weights["text"] * text
    return total, structural, track_record, text


def score(
    notice: Notice,
    past_projects: list[PastProject],
    weights: dict[str, float] | None = None,
    extra_text: str = "",
) -> SimilarityResult:
    """공고 1건을 과거 실적 목록 전체와 비교해, 가장 유사한 건 기준으로 점수를 낸다.

    평균이 아니라 최댓값을 쓴다 — "이런 걸 한 번이라도 해봤는가"가 중요하지, 무관한
    과거 사업들과 평균 내면 실제로 딱 맞는 실적 하나가 희석되어 버린다.

    `extra_text`: 공고 제목만으로는 정보가 부족해서(협상 공고는 제목이 짧고
    포괄적인 경우가 많음) 첨부파일(제안요청서·과업지시서)에서 뽑은 과업내용·
    전시내용 본문을 넘기면 그것도 비교에 쓴다. 비워두면(기본값) 제목·품명만
    쓰는 기존 동작 그대로다.
    """
    weights = weights or DEFAULT_WEIGHTS
    if not past_projects:
        return SimilarityResult(score=0.0, structural_score=0.0, track_record_score=0.0, text_score=0.0, matched=None)

    df = _document_frequencies(past_projects)
    n_docs = len(past_projects)
    total, structural, track_record, text, matched = max(
        (( *_score_one(notice, p, weights, df, n_docs, extra_text), p) for p in past_projects),
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
                area_note=item.get("area", ""),
            )
        )
    return out


def load_past_projects_file(path: Path) -> list[PastProject]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return load_past_projects(raw)
