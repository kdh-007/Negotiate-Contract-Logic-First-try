"""유사도 점수 산정(`nego/similarity.py`) 단위 테스트.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego.models import notice_from_raw  # noqa: E402
from nego.similarity import (  # noqa: E402
    TEXT_ONLY_WEIGHTS,
    PastProject,
    load_past_projects,
    score,
    tokenize,
)
from tests import fixtures  # noqa: E402


def _notice(**overrides):
    return notice_from_raw(fixtures.notice("R99TEST0001", **overrides), "용역")


class TestTokenize(unittest.TestCase):
    def test_splits_on_non_alnum_and_drops_single_chars(self):
        self.assertEqual(tokenize("전시관 미디어아트 및 A/S"), {"전시관", "미디어아트"})

    def test_none_returns_empty_set(self):
        self.assertEqual(tokenize(None), set())


class TestScore(unittest.TestCase):
    def test_no_past_projects_gives_zero_score_and_no_match(self):
        """비교 대상 자체가 없으면 임의로 중립값을 주지 않고 0점 처리한다."""
        result = score(_notice(), [])
        self.assertEqual(result.score, 0.0)
        self.assertIsNone(result.matched)

    def test_same_industry_and_budget_scores_high(self):
        """업종명이 겹치고(구조) 예산도 비슷하면(규모) 두 축 다 만점에 가까워야 한다."""
        notice = _notice(
            bidNtceNm="OO 전시관 미디어아트 콘텐츠 제작",
            presmptPrce="500000000",
            bidprcPsblIndstrytyNm="[실내건축공사업/4990]",
        )
        past = PastProject(
            title="XX 전시관 미디어아트 콘텐츠 제작 설치",
            amount=520_000_000,
            industry_names=["실내건축공사업"],
        )
        result = score(notice, [past])
        self.assertEqual(result.structural_score, 1.0)
        self.assertGreater(result.track_record_score, 0.9)
        self.assertGreater(result.text_score, 0.3)
        self.assertEqual(result.matched, past)
        self.assertGreater(result.score, 70)

    def test_unrelated_industry_and_budget_scores_low(self):
        """업종도 안 겹치고 예산도 10배 이상 차이 나면 거의 0점이어야 한다."""
        notice = _notice(
            bidNtceNm="상하수도 정비공사",
            presmptPrce="5000000000",
            bidprcPsblIndstrytyNm="[토목공사업/0001]",
        )
        past = PastProject(
            title="OO 전시관 콘텐츠 제작",
            amount=300_000_000,
            industry_names=["실내건축공사업"],
        )
        result = score(notice, [past])
        self.assertEqual(result.structural_score, 0.0)
        self.assertLess(result.track_record_score, 0.1)
        self.assertLess(result.score, 10)

    def test_product_code_exact_match_counts_as_structural_hit(self):
        """물품 공고는 업종명이 아니라 세부품명번호 정확일치로 구조적 적합도를 판단한다."""
        notice = _notice(prdctClsfcNo="6012100201")
        past = PastProject(title="조형물 제작", product_codes=["6012100201"])
        result = score(notice, [past])
        self.assertEqual(result.structural_score, 1.0)

    def test_picks_best_match_not_average(self):
        """무관한 과거 사업과 평균 내지 않고, 가장 유사한 건 하나를 근거로 삼는다."""
        notice = _notice(
            bidNtceNm="전시관 미디어아트 콘텐츠 제작",
            presmptPrce="500000000",
            bidprcPsblIndstrytyNm="[실내건축공사업/4990]",
        )
        good = PastProject(
            title="전시관 미디어아트 콘텐츠 제작 설치",
            amount=500_000_000,
            industry_names=["실내건축공사업"],
        )
        unrelated = PastProject(title="상하수도 정비공사", amount=5_000_000_000)
        result = score(notice, [unrelated, good])
        self.assertEqual(result.matched, good)

    def test_extra_text_from_attachment_content_improves_weak_title_match(self):
        """공고 제목만으로는 안 겹치더라도(제목이 짧고 포괄적인 협상공고가 흔함),
        첨부파일에서 뽑은 과업내용·전시내용(`extra_text`)을 넘기면 그 본문이
        토큰 비교에 들어가 무관한 과거사업보다 순위가 올라가야 한다."""
        notice = _notice(bidNtceNm="OO시 체험관 조성사업")  # 제목만으로는 과거사업과 거의 안 겹침
        good = PastProject(
            title="XX시 어린이과학관",
            summary_text="야외형 과학체험전시품 신규설치 물레방아 아르키메데스펌프",
        )
        unrelated = PastProject(title="상하수도 정비공사", summary_text="관로 교체 및 정비공사 시행")

        without_extra = score(notice, [unrelated, good], weights=TEXT_ONLY_WEIGHTS)
        with_extra = score(
            notice,
            [unrelated, good],
            weights=TEXT_ONLY_WEIGHTS,
            extra_text="야외형 과학체험전시품 물레방아 아르키메데스펌프 신규설치",
        )
        self.assertEqual(with_extra.matched, good)
        self.assertGreater(with_extra.score, without_extra.score)

    def test_missing_budget_on_either_side_scores_zero_track_record(self):
        """예산 정보가 한쪽이라도 없으면 규모 적합도는 판단 불가로 0점 처리한다(중립값을 주지 않음)."""
        notice = _notice(presmptPrce="", asignBdgtAmt="")
        past = PastProject(title="테스트", amount=500_000_000)
        result = score(notice, [past])
        self.assertEqual(result.track_record_score, 0.0)

    def test_weights_are_configurable(self):
        """가중치를 바꾸면 종합점수도 그에 따라 달라져야 한다(고정값이 아님을 확인)."""
        notice = _notice(
            bidNtceNm="전시관 미디어아트 콘텐츠 제작",
            presmptPrce="500000000",
            bidprcPsblIndstrytyNm="[실내건축공사업/4990]",
        )
        past = PastProject(
            title="완전히 다른 제목",
            amount=500_000_000,
            industry_names=["실내건축공사업"],
        )
        text_heavy = {"structural": 0.0, "track_record": 0.0, "text": 1.0}
        result_default = score(notice, [past])
        result_text_only = score(notice, [past], weights=text_heavy)
        self.assertNotEqual(result_default.score, result_text_only.score)


class TestLoadPastProjects(unittest.TestCase):
    def test_parses_full_record(self):
        raw = {
            "pastProjects": [
                {
                    "title": "테스트 사업",
                    "institution": "테스트기관",
                    "amount": 100000000,
                    "tags": ["전시관"],
                    "productCodes": ["1234567890"],
                    "industryNames": ["실내건축공사업"],
                    "summaryText": "요약 텍스트",
                }
            ]
        }
        projects = load_past_projects(raw)
        self.assertEqual(len(projects), 1)
        p = projects[0]
        self.assertEqual(p.title, "테스트 사업")
        self.assertEqual(p.amount, 100000000)
        self.assertEqual(p.product_codes, ["1234567890"])

    def test_missing_optional_fields_default_gracefully(self):
        projects = load_past_projects({"pastProjects": [{"title": "제목만 있음"}]})
        self.assertEqual(projects[0].amount, None)
        self.assertEqual(projects[0].tags, [])

    def test_empty_config_returns_empty_list(self):
        self.assertEqual(load_past_projects({}), [])


if __name__ == "__main__":
    unittest.main()
