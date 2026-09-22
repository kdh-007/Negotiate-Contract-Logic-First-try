"""최상위 절 추출 단위 테스트. 표준 라이브러리 unittest만 사용한다.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego.section_text import find_section, split_sections  # noqa: E402


class TestSplitSections(unittest.TestCase):
    def test_empty_text_returns_empty_list(self):
        self.assertEqual(split_sections(""), [])
        self.assertEqual(split_sections("   \n  "), [])

    def test_no_structure_returns_whole_text_as_one_section(self):
        text = "아무 구조 없는 공고문 본문입니다.\n둘째 줄입니다."
        sections = split_sections(text)
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].heading, "")
        self.assertIn("둘째 줄", sections[0].body)

    def test_skips_table_of_contents_and_splits_body_in_order(self):
        """실측(상상나라 2017 제안요청서)과 같은 형태: 목차 한 줄에 여러 절 제목이
        나열되고, 본문에서 Ⅰ.~Ⅳ.가 순서대로 등장한다."""
        text = (
            "목    차\n"
            "  Ⅰ. 업체 현황     Ⅱ. 사업수행 방안     Ⅲ. 제안서 작성요령     Ⅳ. 제안서 평가\n"
            "\n"
            "Ⅰ. 업체 현황\n"
            "1. 일반현황 및 연혁\n"
            "회사 소개 내용입니다.\n"
            "Ⅱ. 사업수행 방안\n"
            "수행 방안 내용입니다.\n"
            "Ⅲ. 제안서 작성요령\n"
            "작성요령 내용입니다.\n"
            "Ⅳ. 제안서 평가\n"
            "평가 내용입니다.\n"
        )
        sections = split_sections(text)
        headings = [s.heading for s in sections]
        self.assertEqual(headings, ["Ⅰ. 업체 현황", "Ⅱ. 사업수행 방안", "Ⅲ. 제안서 작성요령", "Ⅳ. 제안서 평가"])
        self.assertIn("회사 소개", sections[0].body)
        self.assertNotIn("회사 소개", sections[1].body)
        self.assertIn("평가 내용", sections[3].body)

    def test_repeated_top_level_marker_does_not_corrupt_order(self):
        """어떤 번호(Ⅲ)가 본문 다른 곳에서 한 번 더 우연히 등장해도, 그 뒤에도
        진짜 Ⅳ가 있으면 순서가 뒤섞이면 안 된다."""
        text = (
            "Ⅰ. 사업개요\n마지막 Ⅰ\n"
            "본문\n"
            "Ⅰ. 사업개요\n실제 개요 내용\n"
            "Ⅱ. 과업내용\n과업 내용\n"
            "Ⅲ. 제안절차\n절차 내용\n"
            "Ⅲ. 참고 — 다른 문서에서 재인용\n"
            "Ⅳ. 협상 및 계약\n계약 내용\n"
        )
        sections = split_sections(text)
        headings = [s.heading for s in sections]
        self.assertEqual(headings[0], "Ⅰ. 사업개요")
        self.assertIn("실제 개요 내용", sections[0].body)
        self.assertEqual(headings[-1], "Ⅳ. 협상 및 계약")

    def test_clause_numbering_is_not_mistaken_for_top_level_structure(self):
        """법률식 조항 나열("1. 발주자 및 ...")뿐인 문서는 최상위 구조로 오인하면
        안 된다 — 실측(국립중앙과학관 2017 제안요청서)에서 로마숫자/장 표기가
        없는 문서를 억지로 쪼개면 조항 문장이 "절"로 둔갑하는 오탐이 확인됨."""
        text = (
            "1. 발주자 및 구성원 전원이 동의하는 경우\n"
            "2. 파산, 해산, 부도 기타 정당한 이유없이 해당계약을 이행하지 아니하여\n"
            "3. 계약보증금을 납부하지 않는 경우\n"
        )
        sections = split_sections(text)
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].heading, "")

    def test_chapter_style_numbering(self):
        text = (
            "제1장 총칙\n총칙 내용\n"
            "제2장 과업내용\n과업 내용\n"
            "제3장 제출서류\n서류 내용\n"
        )
        sections = split_sections(text)
        headings = [s.heading for s in sections]
        self.assertEqual(headings, ["제1장 총칙", "제2장 과업내용", "제3장 제출서류"])

    def test_fewer_than_three_top_level_matches_fails_open(self):
        text = "Ⅰ. 사업개요\n개요 내용\nⅡ. 과업내용\n과업 내용\n"
        sections = split_sections(text)
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].heading, "")


class TestFindSection(unittest.TestCase):
    def test_finds_section_by_keyword(self):
        text = (
            "Ⅰ. 사업개요\n개요 내용입니다.\n"
            "Ⅱ. 과업내용\n과업 상세 내용입니다.\n"
            "Ⅲ. 제안절차\n절차 내용입니다.\n"
        )
        section = find_section(text, ["과업내용", "과업 내용"])
        self.assertIsNotNone(section)
        self.assertIn("과업 상세", section.body)

    def test_ignores_spacing_differences(self):
        text = "Ⅰ. 사업 개요\n개요 내용\nⅡ. 과업내용\n과업 내용\nⅢ. 제안절차\n절차 내용\n"
        section = find_section(text, ["사업개요"])
        self.assertIsNotNone(section)
        self.assertIn("개요 내용", section.body)

    def test_returns_none_when_no_keyword_matches(self):
        text = "Ⅰ. 사업개요\n개요\nⅡ. 과업내용\n과업\nⅢ. 제안절차\n절차\n"
        self.assertIsNone(find_section(text, ["존재하지않는키워드"]))


if __name__ == "__main__":
    unittest.main()
