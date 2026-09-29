"""LLM(Claude API)으로 신규 공고와 과거 실적의 내용 유사도를 판정한다.

`similarity.py`의 내용 유사도는 단어가 글자 그대로 겹쳐야만 점수가 나서
"체험관" ↔ "인터랙티브 전시관" 같은 표현 차이를 못 잡는다(2026-09-28 논의).
이 모듈은 그 빈자리를 AI 판단으로 보조한다 — 규칙 기반 판정을 대체하지 않는다.

  - 판정은 과거 실적별로 유사 / 부분 유사 / 무관 / 판단 불가 네 가지로만 낸다.
    정보가 모자라면 "판단 불가"로 둔다(모르는 걸 무관·0점으로 바꾸지 않는다).
  - 모든 결과에 `source="ai"`를 붙여 코드 판정과 구분한다.
  - LLM이 "같은 종류"라고 본 표현 쌍(`equivalent_terms`)을 함께 남긴다 — 사람이
    확인해서 동의어 사전에 옮길 후보다.
  - API 호출이 실패해도 파이프라인을 죽이지 않는다. 그 공고만 `error`를 채워 넘긴다.

실행: `python -m nego --llm-similarity` (ANTHROPIC_API_KEY 필요), 또는 수집 없이
`scripts/llm_similarity_test.py`로 공고 제목/본문만 넣어 바로 시험해볼 수 있다.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .similarity import PastProject

log = logging.getLogger(__name__)

VERDICTS = ["유사", "부분 유사", "무관", "판단 불가"]

DEFAULT_MODEL = "claude-opus-5"
# 서버 측 거절 대비 폴백 — 안전 분류기가 요청을 거절하면 API가 같은 호출 안에서
# 다른 모델로 다시 돌린다. 공고 비교에서 거절될 일은 드물지만 켜두는 편이 안전하다.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """\
당신은 주식회사 지일(JIIL)의 입찰 검토 담당자를 돕는 분석가입니다.
지일의 주력 분야는 전시관·체험관·박물관·과학관 전시 설계시공, 어린이 놀이시설 제작설치,
체험콘텐츠·미디어아트 제작입니다.

나라장터 신규 공고 1건과 지일의 과거 수행 실적 목록을 받습니다. 과거 실적마다
"이 공고의 과업이 그 실적과 같은 종류의 일인가"를 판정하세요.

판정 기준:
- 유사: 과업의 핵심(무엇을 만들고 설치하는가)이 같은 종류다. 표현이 달라도 된다
  (예: "체험관"과 "인터랙티브 전시관", "홍보관"과 "전시홍보관").
- 부분 유사: 과업 일부가 겹친다 (예: 전시관 조성 중 영상 콘텐츠 제작만 겹침).
- 무관: 과업 종류가 다르다. 단어가 겹쳐도 업역이 다르면 무관이다
  (예: "전시관 건립공사"의 건축 본공사, "행사 운영 대행").
- 판단 불가: 공고 정보가 너무 적어 판단할 수 없다. 추측으로 채우지 마세요.

equivalent_terms에는 공고와 과거 실적에서 표기는 다르지만 같은 대상을 가리킨다고 본
표현 쌍만 넣으세요. 글자가 똑같은 단어는 넣지 마세요.
reason은 한국어 한두 문장으로 쓰세요."""

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_summary": {"type": "string"},
        "overall": {"type": "string", "enum": VERDICTS},
        "overall_reason": {"type": "string"},
        "comparisons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "project_index": {"type": "integer"},
                    "verdict": {"type": "string", "enum": VERDICTS},
                    "reason": {"type": "string"},
                    "equivalent_terms": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "notice_term": {"type": "string"},
                                "past_term": {"type": "string"},
                            },
                            "required": ["notice_term", "past_term"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["project_index", "verdict", "reason", "equivalent_terms"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["task_summary", "overall", "overall_reason", "comparisons"],
    "additionalProperties": False,
}


@dataclass
class LlmConfig:
    model: str = DEFAULT_MODEL
    effort: str = "medium"
    # 첨부파일 원문은 공고당 수만~수십만 자라 전부 보내면 비용이 커진다. 앞부분(공고문·
    # 사업개요가 보통 앞에 온다)만 보내고, 잘랐으면 로그와 결과에 남긴다.
    max_text_chars: int = 20000
    # 한 번 실행에서 판정할 최대 공고 수 — 테스트 중 비용이 예상 밖으로 커지는 걸 막는다.
    max_candidates: int = 30
    max_tokens: int = 16000

    @classmethod
    def from_env(cls) -> "LlmConfig | None":
        """API 자격증명이 없으면 None — 호출 쪽에서 "건너뜀"으로 처리한다."""
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            return None
        return cls(
            model=os.environ.get("LLM_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            effort=os.environ.get("LLM_EFFORT", "medium").strip() or "medium",
            max_text_chars=int(os.environ.get("LLM_MAX_TEXT_CHARS", "20000")),
            max_candidates=int(os.environ.get("LLM_MAX_CANDIDATES", "30")),
        )


@dataclass
class NoticeInput:
    """LLM에 넘길 공고 정보. `Notice`에 묶이지 않게 따로 둬서, 수집 없이
    제목/본문만으로도 시험할 수 있게 했다(`scripts/llm_similarity_test.py`)."""

    title: str
    notice_no: str = ""
    institution: str | None = None
    product_class_no: str | None = None
    product_class_name: str | None = None
    industry_text: str | None = None
    budget: float | None = None
    task_text: str = ""

    @classmethod
    def from_candidate(cls, candidate: Any) -> "NoticeInput":
        notice = candidate.notice
        return cls(
            title=notice.title or "",
            notice_no=notice.notice_no or "",
            institution=notice.demand_institution or notice.notice_institution,
            product_class_no=notice.product_class_no,
            product_class_name=notice.product_class_name,
            industry_text=notice.industry_text,
            budget=notice.budget,
            task_text=getattr(candidate, "attachment_text", "") or "",
        )


@dataclass
class Comparison:
    project_index: int
    project_title: str
    verdict: str
    reason: str
    equivalent_terms: list[dict[str, str]] = field(default_factory=list)


@dataclass
class LlmJudgement:
    notice_no: str
    title: str
    source: str = "ai"
    model: str = ""
    overall: str = "판단 불가"
    overall_reason: str = ""
    task_summary: str = ""
    comparisons: list[Comparison] = field(default_factory=list)
    text_truncated: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None

    @property
    def best(self) -> Comparison | None:
        for verdict in ("유사", "부분 유사"):
            for c in self.comparisons:
                if c.verdict == verdict:
                    return c
        return None


def usable_past_projects(projects: list[PastProject]) -> list[PastProject]:
    """`past_projects.json`에 들어있는 "(예시)" 항목은 비교 대상에서 뺀다."""
    return [p for p in projects if p.title and not p.title.startswith("(예시)")]


def _format_amount(value: float | None) -> str:
    if not value:
        return "미상"
    return f"{value / 100_000_000:.2f}억원"


def build_prompt(notice: NoticeInput, projects: list[PastProject], max_text_chars: int) -> tuple[str, bool]:
    """(프롬프트, 본문을 잘랐는지). 과거 실적 목록을 앞에 둬서 같은 실행 안의 여러 공고가
    같은 앞부분을 공유하게 한다."""
    lines = ["# 지일 과거 수행 실적"]
    for i, p in enumerate(projects):
        lines.append(f"[{i}] {p.title}")
        detail = []
        if p.institution:
            detail.append(f"발주기관: {p.institution}")
        if p.amount:
            detail.append(f"금액: {_format_amount(p.amount)}")
        if p.tags:
            detail.append(f"분야: {', '.join(p.tags)}")
        if p.product_codes:
            detail.append(f"세부품명번호: {', '.join(p.product_codes)}")
        if detail:
            lines.append("    " + " / ".join(detail))
        if p.summary_text:
            lines.append(f"    내용: {p.summary_text.strip()}")

    lines.append("")
    lines.append("# 신규 공고")
    lines.append(f"공고명: {notice.title}")
    if notice.institution:
        lines.append(f"발주기관: {notice.institution}")
    if notice.product_class_name or notice.product_class_no:
        lines.append(f"세부품명: {notice.product_class_name or ''} ({notice.product_class_no or '번호 없음'})")
    if notice.industry_text:
        lines.append(f"투찰가능업종: {notice.industry_text}")
    lines.append(f"추정 금액: {_format_amount(notice.budget)}")

    text = (notice.task_text or "").strip()
    truncated = len(text) > max_text_chars
    if text:
        lines.append("")
        lines.append("## 첨부파일 원문" + (f" (앞 {max_text_chars}자만 발췌)" if truncated else ""))
        lines.append(text[:max_text_chars])
    else:
        lines.append("(첨부파일 원문 없음 — 공고명과 품명만으로 판단)")

    lines.append("")
    lines.append("위 과거 실적 각각에 대해 판정하세요. project_index는 대괄호 안 번호입니다.")
    return "\n".join(lines), truncated


def judge_notice(client: Any, config: LlmConfig, notice: NoticeInput, projects: list[PastProject]) -> LlmJudgement:
    """공고 1건 판정. 실패해도 예외를 올리지 않고 `error`를 채워 돌려준다."""
    import anthropic

    result = LlmJudgement(notice_no=notice.notice_no, title=notice.title, model=config.model)
    prompt, result.text_truncated = build_prompt(notice, projects, config.max_text_chars)
    if result.text_truncated:
        log.info("[%s] 첨부파일 원문을 앞 %d자로 잘라 보냅니다", notice.notice_no, config.max_text_chars)

    try:
        response = client.beta.messages.create(
            model=config.model,
            max_tokens=config.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "effort": config.effort,
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
    except anthropic.RateLimitError as err:
        result.error = f"요청 한도 초과(429): {err.message}"
        return result
    except anthropic.APIStatusError as err:
        result.error = f"API 오류({err.status_code}): {err.message}"
        return result
    except anthropic.APIConnectionError as err:
        result.error = f"연결 실패: {err}"
        return result

    usage = getattr(response, "usage", None)
    if usage is not None:
        result.input_tokens = getattr(usage, "input_tokens", 0) or 0
        result.output_tokens = getattr(usage, "output_tokens", 0) or 0
    result.model = getattr(response, "model", None) or config.model

    if response.stop_reason == "refusal":
        result.error = "모델이 응답을 거절함(refusal)"
        return result
    if response.stop_reason == "max_tokens":
        result.error = "출력이 max_tokens에서 잘림"
        return result

    text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
    if not text:
        result.error = "응답에 텍스트 블록이 없음"
        return result
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        result.error = f"JSON 파싱 실패: {err}"
        return result

    result.task_summary = data.get("task_summary", "")
    result.overall = data.get("overall", "판단 불가")
    result.overall_reason = data.get("overall_reason", "")
    for item in data.get("comparisons", []):
        index = item.get("project_index", -1)
        if not 0 <= index < len(projects):
            log.warning("[%s] 범위 밖 project_index=%s 무시", notice.notice_no, index)
            continue
        result.comparisons.append(
            Comparison(
                project_index=index,
                project_title=projects[index].title,
                verdict=item.get("verdict", "판단 불가"),
                reason=item.get("reason", ""),
                equivalent_terms=list(item.get("equivalent_terms", [])),
            )
        )
    return result


def make_client() -> Any:
    import anthropic

    return anthropic.Anthropic()


def judge_candidates(
    candidates: list[Any],
    projects: list[PastProject],
    config: LlmConfig,
    client: Any = None,
) -> list[LlmJudgement]:
    """후보 공고마다 판정하고 `candidate.llm_similarity`에 결과를 달아둔다."""
    projects = usable_past_projects(projects)
    if not projects:
        log.warning("비교할 과거 실적이 없습니다 (config/past_projects.json에 예시 항목만 있음) — LLM 판정 건너뜀")
        return []

    client = client or make_client()
    targets = candidates[: config.max_candidates]
    if len(candidates) > len(targets):
        log.warning("후보 %d건 중 앞 %d건만 LLM 판정합니다 (LLM_MAX_CANDIDATES)", len(candidates), len(targets))

    results: list[LlmJudgement] = []
    for candidate in targets:
        judgement = judge_notice(client, config, NoticeInput.from_candidate(candidate), projects)
        if judgement.error:
            log.warning("LLM 판정 실패 [%s]: %s", judgement.notice_no, judgement.error)
        candidate.llm_similarity = judgement
        results.append(judgement)
    return results


def render_markdown(results: list[LlmJudgement]) -> str:
    lines = ["# LLM 유사도 판정 결과 (source=ai)", ""]
    total_in = sum(r.input_tokens for r in results)
    total_out = sum(r.output_tokens for r in results)
    lines.append(f"공고 {len(results)}건 · 입력 토큰 {total_in:,} · 출력 토큰 {total_out:,}")
    lines.append("")
    for r in results:
        lines.append(f"## {r.title} ({r.notice_no or '번호 없음'})")
        if r.error:
            lines.append(f"- ⚠️ 판정 실패: {r.error}")
            lines.append("")
            continue
        lines.append(f"- 종합: **{r.overall}** — {r.overall_reason}")
        if r.task_summary:
            lines.append(f"- 과업 요약: {r.task_summary}")
        if r.text_truncated:
            lines.append("- (첨부파일 원문 일부만 사용)")
        for c in r.comparisons:
            terms = ", ".join(f"{t['notice_term']}≈{t['past_term']}" for t in c.equivalent_terms)
            lines.append(f"  - [{c.verdict}] {c.project_title}: {c.reason}" + (f" (동의 표현: {terms})" if terms else ""))
        lines.append("")
    return "\n".join(lines)


def render_console(results: list[LlmJudgement]) -> str:
    lines = [f"LLM 유사도 판정 {len(results)}건 (source=ai)"]
    for r in results:
        if r.error:
            lines.append(f"  · {r.title[:40]} → 실패: {r.error}")
            continue
        best = r.best
        tail = f" · 가장 가까운 실적: {best.project_title}" if best else ""
        lines.append(f"  · {r.title[:40]} → {r.overall}{tail}")
    return "\n".join(lines)


def save_results(results: list[LlmJudgement], output_dir: Path, now: datetime) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M")
    json_path = output_dir / f"llm_similarity_{stamp}.json"
    md_path = output_dir / f"llm_similarity_{stamp}.md"
    json_path.write_text(
        json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(render_markdown(results), encoding="utf-8")
    return {"llm_json": json_path, "llm_md": md_path}
