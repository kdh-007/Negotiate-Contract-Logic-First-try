"""LLM 유사도 판정(`nego/llm_similarity.py`) 단위 테스트 — 실제 API는 부르지 않는다.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic  # noqa: E402
import httpx2  # noqa: E402

from nego import llm_similarity as L  # noqa: E402
from nego.similarity import PastProject  # noqa: E402

PROJECTS = [
    PastProject(title="OO기념관 인터랙티브 전시관 조성", institution="OO시청", amount=500_000_000, tags=["전시관"]),
    PastProject(title="△△공원 조합놀이대 제작설치", tags=["놀이시설"]),
]


def _response(payload=None, stop_reason="end_turn"):
    content = [] if payload is None else [SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))]
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content,
        model="claude-opus-5",
        usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
    )


class FakeClient:
    def __init__(self, response=None, error=None):
        self.calls = []
        self._response = response
        self._error = error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response


GOOD_PAYLOAD = {
    "task_summary": "청소년 대상 독도 디지털 체험시설 제작·설치",
    "overall": "유사",
    "overall_reason": "체험형 전시시설 제작·설치로 과거 인터랙티브 전시관 조성과 같은 종류다.",
    "comparisons": [
        {
            "project_index": 0,
            "verdict": "유사",
            "reason": "체험관과 인터랙티브 전시관은 같은 종류의 과업이다.",
            "equivalent_terms": [{"notice_term": "디지털체험관", "past_term": "인터랙티브 전시관"}],
        },
        {"project_index": 1, "verdict": "무관", "reason": "놀이시설과는 다르다.", "equivalent_terms": []},
        {"project_index": 7, "verdict": "유사", "reason": "존재하지 않는 번호", "equivalent_terms": []},
    ],
}


class TestJudgeNotice(unittest.TestCase):
    def setUp(self):
        self.config = L.LlmConfig()
        self.notice = L.NoticeInput(title="청소년 독도디지털체험관 전시체험시설 제작·설치", notice_no="R26BK01393534")

    def test_parses_structured_output(self):
        client = FakeClient(_response(GOOD_PAYLOAD))
        result = L.judge_notice(client, self.config, self.notice, PROJECTS)

        self.assertIsNone(result.error)
        self.assertEqual(result.source, "ai")
        self.assertEqual(result.overall, "유사")
        # 범위 밖 project_index(7)는 버린다
        self.assertEqual([c.project_index for c in result.comparisons], [0, 1])
        self.assertEqual(result.best.project_title, PROJECTS[0].title)
        self.assertEqual(result.comparisons[0].equivalent_terms[0]["past_term"], "인터랙티브 전시관")
        self.assertEqual((result.input_tokens, result.output_tokens), (1200, 300))

    def test_request_shape(self):
        client = FakeClient(_response(GOOD_PAYLOAD))
        L.judge_notice(client, self.config, self.notice, PROJECTS)
        call = client.calls[0]
        self.assertEqual(call["model"], "claude-opus-5")
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(call["fallbacks"], "default")
        self.assertIn(L.FALLBACK_BETA, call["betas"])
        prompt = call["messages"][0]["content"]
        self.assertIn("[0] OO기념관 인터랙티브 전시관 조성", prompt)
        self.assertIn("공고명: 청소년 독도디지털체험관", prompt)

    def test_refusal_becomes_error_not_verdict(self):
        client = FakeClient(_response(None, stop_reason="refusal"))
        result = L.judge_notice(client, self.config, self.notice, PROJECTS)
        self.assertIn("refusal", result.error)
        self.assertEqual(result.overall, "판단 불가")

    def test_api_error_does_not_raise(self):
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        err = anthropic.APIConnectionError(request=request)
        result = L.judge_notice(FakeClient(error=err), self.config, self.notice, PROJECTS)
        self.assertIn("연결 실패", result.error)


class TestPrompt(unittest.TestCase):
    def test_long_attachment_text_is_trimmed_and_flagged(self):
        notice = L.NoticeInput(title="t", task_text="가" * 50)
        prompt, truncated = L.build_prompt(notice, PROJECTS, max_text_chars=10)
        self.assertTrue(truncated)
        self.assertIn("가" * 10, prompt)
        self.assertNotIn("가" * 11, prompt)

    def test_no_attachment_text_is_stated(self):
        prompt, truncated = L.build_prompt(L.NoticeInput(title="t"), PROJECTS, 100)
        self.assertFalse(truncated)
        self.assertIn("첨부파일 원문 없음", prompt)


class TestJudgeCandidates(unittest.TestCase):
    def _candidate(self, title):
        notice = SimpleNamespace(
            title=title,
            notice_no="R1",
            demand_institution=None,
            notice_institution="기관",
            product_class_no=None,
            product_class_name=None,
            industry_text=None,
            budget=None,
        )
        return SimpleNamespace(notice=notice, attachment_text="과업내용 원문")

    def test_example_only_projects_skip_without_calling_api(self):
        client = FakeClient(_response(GOOD_PAYLOAD))
        results = L.judge_candidates(
            [self._candidate("a")], [PastProject(title="(예시) OO시 전시관")], L.LlmConfig(), client
        )
        self.assertEqual(results, [])
        self.assertEqual(client.calls, [])

    def test_attaches_result_and_respects_cap(self):
        client = FakeClient(_response(GOOD_PAYLOAD))
        candidates = [self._candidate("a"), self._candidate("b")]
        results = L.judge_candidates(candidates, PROJECTS, L.LlmConfig(max_candidates=1), client)
        self.assertEqual(len(results), 1)
        self.assertIs(candidates[0].llm_similarity, results[0])
        self.assertIn("과업내용 원문", client.calls[0]["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
