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
    PastProject,
    load_past_projects,
    rank,
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

    def test_common_boilerplate_words_do_not_create_false_ties(self):
        """실측(2026-09-22, config/past_projects.json 81건으로 프로토타입 실행):
        "포함·설치·있는·제작·사업" 같은 단어는 과거 실적 80~90%에 등장하는
        보일러플레이트라, 겹침계수만 쓰면 무관한 실적이 진짜 일치 건과 동점을
        먹는다. corpus가 충분히 크면(5건 이상) 절반 넘게 등장하는 토큰을 걸러내
        진짜 일치 건이 확실히 더 높은 점수를 받아야 한다."""
        notice = _notice(bidNtceNm="울산과학관 전시체험물 교체 사업")
        real_match = PastProject(title="울산과학관", summary_text="울산과학관 전시체험물 교체 사업 관련 내용")
        boilerplate_only = [
            PastProject(title=f"무관한 사업 {i}", summary_text="사업 포함 설치 제작 관련 절차 내용")
            for i in range(5)
        ]
        result = score(notice, [real_match, *boilerplate_only])
        self.assertEqual(result.matched, real_match)
        self.assertGreater(result.text_score, 0.5)


class TestContentAwareTokens(unittest.TestCase):
    """`PastProject.tokens`가 summary_text 전체가 아니라 section_text로 뽑아낸
    "사업개요/과업내용" 절만 쓰는지 확인한다 — 실측(국립중앙과학관 등)에서 목차·
    서식 안내가 summary_text 대부분을 차지해 그대로 토큰화하면 신호가 희석된다."""

    def test_prefers_content_section_over_toc_noise(self):
        summary_text = (
            "차   례\n"
            "Ⅰ. 사업 개요 蠠ȃ1\n"
            "Ⅱ. 제안 요청 내용 秤ȃ2\n"
            "Ⅲ. 일반 사항 蠠ȃ7\n"
            "\n"
            "Ⅰ. 사업 개요\n"
            " 1. 사업명 : 미디어아트 콘텐츠 제작 설치\n"
            " 2. 사업 목적 : 창의적 전시 콘텐츠 확보\n"
            "\n"
            "Ⅱ. 제안 요청 내용\n"
            " 일반 사항 안내 및 서식 목록\n"
            "\n"
            "Ⅲ. 일반 사항\n"
            " 제안서 작성 방법, 제출 서류, 청렴계약 이행서약서 등 행정 절차\n"
        )
        past = PastProject(title="테스트 사업", summary_text=summary_text)
        self.assertIn("미디어아트", past.tokens)
        self.assertIn("콘텐츠", past.tokens)
        # "일반 사항" 절(행정 절차)은 "사업개요" 절이 아니므로 안 섞여 들어와야 한다.
        self.assertNotIn("청렴계약", past.tokens)
        self.assertNotIn("이행서약서", past.tokens)

    def test_falls_back_to_full_text_when_no_content_section_found(self):
        """절 구조를 못 찾거나(fail-open) 사업개요/과업내용 절이 아예 없으면
        summary_text 전체를 그대로 쓴다 — 있는 신호를 버리지 않는다."""
        past = PastProject(title="테스트 사업", summary_text="구조 없는 평문 텍스트, 미디어아트 콘텐츠 제작 건")
        self.assertIn("미디어아트", past.tokens)
        self.assertIn("콘텐츠", past.tokens)


class TestRank(unittest.TestCase):
    def test_returns_top_n_sorted_descending(self):
        notice = _notice(
            bidNtceNm="전시관 미디어아트 콘텐츠 제작",
            presmptPrce="500000000",
            bidprcPsblIndstrytyNm="[실내건축공사업/4990]",
        )
        best = PastProject(
            title="전시관 미디어아트 콘텐츠 제작 설치",
            amount=500_000_000,
            industry_names=["실내건축공사업"],
        )
        mid = PastProject(title="전시관 콘텐츠 일부 제작", amount=300_000_000)
        worst = PastProject(title="상하수도 정비공사", amount=5_000_000_000)
        matches = rank(notice, [worst, mid, best], top_n=2)
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0].past, best)
        self.assertGreaterEqual(matches[0].score, matches[1].score)

    def test_top_n_larger_than_pool_returns_all(self):
        notice = _notice()
        past = PastProject(title="유일한 실적")
        matches = rank(notice, [past], top_n=5)
        self.assertEqual(len(matches), 1)

    def test_empty_pool_returns_empty_list(self):
        self.assertEqual(rank(_notice(), []), [])


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
