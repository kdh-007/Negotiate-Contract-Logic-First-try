"""과업 내용 유사도의 AI 보조 판단 (narabid.md 로드맵 5단계).

`similarity.py`의 내용 유사도(text) 축은 키워드 자카드라 동의어("전시물" vs
"전시콘텐츠")나 표현 차이를 못 잡는다(2026-09-21 논의). 이 모듈은 그 축 하나만
Claude에게 다시 판단시켜 `SimilarityResult.text_source`를 "ai"로 바꾸고 종합점수를
재계산한다 — 업역/규모 축은 그대로 코드 판정을 쓴다(세부품명번호 정확일치·예산
로그근접도는 AI가 사람보다 더 잘 볼 이유가 없는 영역이라 비용을 들일 필요가 없다).

비용·속도 때문에 기본 파이프라인에는 안 걸려 있다 — `python -m nego --ai-similarity`
플래그로 켜면, 이미 스크리닝·자격게이트를 통과한 **최종 후보 목록**에 대해서만
(공고 하나당 API 호출 1회, 코드가 이미 골라낸 최유사 과거실적 1건과만 비교) 실행된다.
실패해도(키 없음·네트워크 오류·rate limit·응답 파싱 실패 등) 파이프라인을 죽이지
않고 그 후보만 기존 자카드 점수 그대로 둔다(다른 외부 API 연동과 같은 fail-open).

모델은 기본 Haiku 4.5 — narabid.md에서 "저비용으로 시작 추천"이라고 정리해둔 값이다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .models import Notice
from .similarity import DEFAULT_WEIGHTS, PastProject, SimilarityResult

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5"

_TOOL = {
    "name": "report_similarity",
    "description": "신규 공고의 과업 내용이 과거 수행 실적과 실제로 같은 종류의 일인지 판단한 결과를 보고한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "과업 내용이 얼마나 같은 종류의 일인지 (0=전혀 무관, 100=사실상 동일한 업무)",
            },
            "reason": {
                "type": "string",
                "description": "판단 근거 한두 문장 (한국어)",
            },
        },
        "required": ["score", "reason"],
        "additionalProperties": False,
    },
    "strict": True,
}

_SYSTEM_PROMPT = (
    "너는 전시관·박물관·과학관 등 전시 콘텐츠 설계·제작·시공 회사의 입찰 담당자를 돕는 "
    "보조원이다. 신규 나라장터 공고 하나와 회사의 과거 수행 실적 하나를 비교해서, "
    "과업 내용이 실제로 얼마나 같은 종류의 일인지 판단한다. 제목·품명에 쓰인 단어가 "
    "다르더라도 동의어나 유사한 표현이면 유사하다고 판단하되, 업무 성격 자체가 다르면"
    "(예: 단순 물품 납품 vs 설계·제작·시공, 혹은 무관한 분야) 낮은 점수를 준다. "
    "report_similarity 도구 호출로만 답하고 다른 텍스트는 출력하지 않는다."
)


@dataclass
class AiJudgment:
    score: float  # 0~100
    reason: str


def default_client():
    """CLI에서 쓰는 기본 클라이언트 팩토리. anthropic 패키지는 이 기능을 실제로 켤 때만
    필요하므로 여기서 지연 임포트한다(다른 실행 경로는 이 패키지 없이도 동작해야 함 —
    attachments.py가 pypdf를 다루는 방식과 동일)."""
    try:
        import anthropic
    except ImportError as err:
        raise RuntimeError("anthropic 패키지가 설치되어 있지 않습니다 (pip install anthropic)") from err
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 필요합니다 (AI 유사도 판단용).")
    return anthropic.Anthropic()


def _build_user_prompt(notice: Notice, past: PastProject) -> str:
    return (
        "[신규 공고]\n"
        f"제목: {notice.title}\n"
        f"품명/업종: {notice.product_class_name or notice.industry_text or '(정보 없음)'}\n\n"
        "[과거 수행 실적]\n"
        f"사업명: {past.title}\n"
        f"내용: {past.summary_text or '(요약 없음)'}\n"
        f"태그: {', '.join(past.tags) or '(없음)'}\n"
    )


def judge_text_similarity(client, notice: Notice, past: PastProject, model: str = DEFAULT_MODEL) -> AiJudgment | None:
    """공고 1건과 과거실적 1건의 내용 유사도를 Claude에게 판단시킨다.

    실패하면(네트워크 오류·인증 오류·rate limit·응답 파싱 실패 등) None을 반환한다 —
    호출부는 이 경우 기존 자카드 점수를 그대로 쓴다(fail-open)."""
    try:
        response = client.messages.create(
            model=model,
            max_tokens=300,
            system=_SYSTEM_PROMPT,
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "report_similarity"},
            messages=[{"role": "user", "content": _build_user_prompt(notice, past)}],
        )
    except Exception as err:  # 네트워크/인증/rate limit 등 — 파이프라인을 죽이면 안 된다
        log.warning("AI 유사도 판단 실패(%s): %s", notice.notice_no, err)
        return None

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "report_similarity":
            data = block.input
            try:
                return AiJudgment(score=float(data["score"]), reason=str(data["reason"]))
            except (KeyError, TypeError, ValueError) as err:
                log.warning("AI 유사도 응답 파싱 실패(%s): %s", notice.notice_no, err)
                return None
    log.warning("AI 유사도 응답에 report_similarity 호출이 없음(%s)", notice.notice_no)
    return None


def refine_with_ai(candidates, client, model: str = DEFAULT_MODEL, weights: dict[str, float] | None = None) -> int:
    """이미 코드가 계산해 둔 `Candidate.similarity`의 내용유사도(text) 축만 AI 판단으로
    교체하고 종합점수를 재계산한다. 대상은 과거실적이 하나라도 매칭된 후보뿐이다
    (비교할 실적이 없으면 애초에 판단할 게 없다).

    반환값은 실제로 AI 판단이 반영된 후보 수(로그/통계용)."""
    weights = weights or DEFAULT_WEIGHTS
    refined = 0
    for c in candidates:
        sim = c.similarity
        if sim.matched is None:
            continue
        judgment = judge_text_similarity(client, c.notice, sim.matched, model=model)
        if judgment is None:
            continue
        text_score = judgment.score / 100
        total = (
            weights["structural"] * sim.structural_score
            + weights["track_record"] * sim.track_record_score
            + weights["text"] * text_score
        )
        c.similarity = SimilarityResult(
            score=round(total * 100, 1),
            structural_score=sim.structural_score,
            track_record_score=sim.track_record_score,
            text_score=text_score,
            matched=sim.matched,
            text_source="ai",
            ai_reason=judgment.reason,
        )
        refined += 1
    return refined
