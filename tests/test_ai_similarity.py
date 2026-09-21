"""내용 유사도 AI 보조 판단(`nego/ai_similarity.py`) 단위 테스트.

실제 Claude API를 호출하지 않는다 — client.messages.create를 흉내낸 가짜
객체로 응답/예외 상황을 재현한다.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego.ai_similarity import judge_text_similarity, refine_with_ai  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.similarity import PastProject, SimilarityResult  # noqa: E402
from tests import fixtures  # noqa: E402


def _notice(**overrides):
    return notice_from_raw(fixtures.notice("R99TEST0001", **overrides), "용역")


def _past(**overrides):
    defaults = dict(title="○○과학관 전시물 제작", amount=500_000_000, tags=["과학관"])
    defaults.update(overrides)
    return PastProject(**defaults)


class _FakeBlock:
    def __init__(self, type_, name=None, input_=None):
        self.type = type_
        self.name = name
        self.input = input_


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeMessages:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc:
            raise self._exc
        return self._response


class _FakeClient:
    def __init__(self, response=None, exc=None):
        self.messages = _FakeMessages(response=response, exc=exc)


class TestJudgeTextSimilarity(unittest.TestCase):
    def test_returns_judgment_from_matching_tool_use_block(self):
        response = _FakeResponse([_FakeBlock("tool_use", "report_similarity", {"score": 82, "reason": "같은 종류의 전시 제작 사업"})])
        client = _FakeClient(response=response)

        result = judge_text_similarity(client, _notice(), _past())

        self.assertEqual(result.score, 82)
        self.assertEqual(result.reason, "같은 종류의 전시 제작 사업")
        # tool_choice로 report_similarity를 강제해야 파싱이 안정적이다.
        self.assertEqual(client.messages.calls[0]["tool_choice"], {"type": "tool", "name": "report_similarity"})

    def test_network_or_auth_error_returns_none_not_raises(self):
        client = _FakeClient(exc=RuntimeError("network down"))
        result = judge_text_similarity(client, _notice(), _past())
        self.assertIsNone(result)

    def test_no_tool_use_block_returns_none(self):
        response = _FakeResponse([_FakeBlock("text", input_=None)])
        client = _FakeClient(response=response)
        result = judge_text_similarity(client, _notice(), _past())
        self.assertIsNone(result)

    def test_malformed_tool_input_returns_none(self):
        response = _FakeResponse([_FakeBlock("tool_use", "report_similarity", {"score": "not-a-number"})])
        client = _FakeClient(response=response)
        result = judge_text_similarity(client, _notice(), _past())
        self.assertIsNone(result)


class _FakeCandidate:
    def __init__(self, notice, similarity):
        self.notice = notice
        self.similarity = similarity


class TestRefineWithAi(unittest.TestCase):
    def test_replaces_text_axis_and_recomputes_total(self):
        past = _past()
        sim = SimilarityResult(score=50.0, structural_score=1.0, track_record_score=1.0, text_score=0.0, matched=past)
        candidate = _FakeCandidate(_notice(), sim)

        response = _FakeResponse([_FakeBlock("tool_use", "report_similarity", {"score": 90, "reason": "동의어 표현 차이일 뿐 같은 업무"})])
        client = _FakeClient(response=response)

        refined = refine_with_ai([candidate], client)

        self.assertEqual(refined, 1)
        self.assertEqual(candidate.similarity.text_source, "ai")
        self.assertEqual(candidate.similarity.ai_reason, "동의어 표현 차이일 뿐 같은 업무")
        self.assertAlmostEqual(candidate.similarity.text_score, 0.9)
        # structural=1.0, track_record=1.0, text=0.9 → (1+1+0.9)/3*100
        self.assertAlmostEqual(candidate.similarity.score, round((1.0 + 1.0 + 0.9) / 3 * 100, 1))

    def test_skips_candidates_with_no_matched_past_project(self):
        candidate = _FakeCandidate(_notice(), SimilarityResult.no_match())
        client = _FakeClient(response=_FakeResponse([]))

        refined = refine_with_ai([candidate], client)

        self.assertEqual(refined, 0)
        self.assertEqual(client.messages.calls, [], "비교할 실적이 없으면 API를 호출할 필요가 없다")

    def test_ai_failure_leaves_existing_jaccard_score_untouched(self):
        past = _past()
        sim = SimilarityResult(score=50.0, structural_score=1.0, track_record_score=1.0, text_score=0.3, matched=past)
        candidate = _FakeCandidate(_notice(), sim)
        client = _FakeClient(exc=RuntimeError("rate limited"))

        refined = refine_with_ai([candidate], client)

        self.assertEqual(refined, 0)
        self.assertIs(candidate.similarity, sim, "AI 판단이 실패하면 기존 결과를 그대로 둔다(fail-open)")
        self.assertEqual(candidate.similarity.text_source, "jaccard")


if __name__ == "__main__":
    unittest.main(verbosity=2)
