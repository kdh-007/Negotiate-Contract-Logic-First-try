"""과거 실적 규모/발주기관/업역 추출(`nego/past_project_facts.py`) 단위 테스트.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego.past_project_facts import extract_amount, extract_codes, extract_institution  # noqa: E402


class TestExtractAmount(unittest.TestCase):
    def test_recognizes_1000won_unit(self):
        """실측: KOSCOM 2017 제안요청서 — "사업예산 : 150,000천원(부가가치세 별도)"."""
        self.assertEqual(extract_amount("4. 사업예산 : 150,000천원(부가가치세 별도)"), 150_000_000.0)

    def test_recognizes_won_unit_with_geum_prefix(self):
        """실측: 둘리테마거리 조성 — "총사업비 : 금1,000,000,000원(부가가치세 등 제비용 모두 포함)"."""
        self.assertEqual(
            extract_amount("다. 총사업비 : 금1,000,000,000원(부가가치세 등 제비용 모두 포함)"), 1_000_000_000.0
        )

    def test_recognizes_million_won_unit(self):
        """실측: 태백 오로라파크 — "사업비 : 12,958백만원"."""
        self.assertEqual(extract_amount("사업비 : 12,958백만원"), 12_958_000_000.0)

    def test_label_without_colon_is_not_mistaken_for_a_budget_figure(self):
        """실측 오탐(첫 시도, 2026-09-22): 콜론 없이 "사업비"라는 단어만 있는
        행정 문구("사업비 증액은 없다")는 진짜 예산이 아니다 — 콜론이 없으면
        인정하지 않는다."""
        self.assertIsNone(extract_amount("기간이 증가하더라도 사업비 증액은 없다."))

    def test_no_budget_label_returns_none(self):
        self.assertIsNone(extract_amount("아무 관련 없는 본문 텍스트입니다."))


class TestExtractInstitution(unittest.TestCase):
    def test_recognizes_labelled_institution(self):
        """실측: 지평전투기념관 제안요청서 — "발주기관 : 양평군청"."""
        self.assertEqual(extract_institution("발주기관 : 양평군청"), "양평군청")

    def test_accepts_alternate_label(self):
        self.assertEqual(extract_institution("수요기관 : 춘천시청"), "춘천시청")

    def test_label_without_colon_is_not_mistaken_for_institution_name(self):
        """실측 오탐(첫 시도, 2026-09-22): "발주기관은 본 과업 수행에 필요하다고
        판단하는 경우..."처럼 "발주기관"이 문장의 주어로 쓰인 경우가 훨씬
        많다(첫 시도에서 76/81건이 "매칭"됐지만 전부 이런 문장 조각이었다).
        콜론이 없으면 기관명으로 보지 않는다."""
        self.assertIsNone(extract_institution("발주기관은 본 과업 수행에 필요하다고 판단하는 경우 협상을 통해"))

    def test_no_institution_label_returns_none(self):
        self.assertIsNone(extract_institution("아무 관련 없는 본문 텍스트입니다."))


class TestExtractCodes(unittest.TestCase):
    def _held_names(self):
        return {"4442": "산업디자인전문회사(환경디자인분야)", "6484": "공공디자인 전문회사"}

    def test_extracts_product_code_and_resolves_industry_code_via_held_registry(self):
        """실측: 인천아트시티조성사업 참가자격 절 — 세부품명번호(10자리)는 코드
        그대로, 업종코드(4자리)는 지일이 실제 보유한 자격 등록증(held_code_names)
        으로 이름을 되찾는다."""
        text = (
            "4. 입찰참가자격\n"
            "가. 다음 중 어느 하나에 해당하는 업체\n"
            "  - 조형물(세부품명번호 6012100201)을 소지한 자\n"
            "  - 공공디자인 전문회사(업종코드 6484)\n"
        )
        product_codes, industry_names = extract_codes(text, self._held_names())
        self.assertEqual(product_codes, ["6012100201"])
        self.assertEqual(industry_names, ["공공디자인 전문회사"])

    def test_industry_code_not_in_held_registry_is_dropped_not_guessed(self):
        """등록증에 없는 업종코드는 이름을 지어내지 않고 버린다 — fail-open으로
        비워두는 게 틀린 이름을 넣는 것보다 낫다."""
        text = "4. 입찰참가자격\n  - 미상업종(업종코드 9999)\n"
        product_codes, industry_names = extract_codes(text, self._held_names())
        self.assertEqual(product_codes, [])
        self.assertEqual(industry_names, [])

    def test_no_qualification_section_returns_empty_lists(self):
        product_codes, industry_names = extract_codes("구조 없는 평문 텍스트", self._held_names())
        self.assertEqual(product_codes, [])
        self.assertEqual(industry_names, [])


if __name__ == "__main__":
    unittest.main()
