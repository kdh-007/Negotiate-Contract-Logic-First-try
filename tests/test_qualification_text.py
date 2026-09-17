"""참가자격 절 추출 단위 테스트. 표준 라이브러리 unittest만 사용한다.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego.qualification_text import find_qualification_section, split_items  # noqa: E402


class TestFindQualificationSection(unittest.TestCase):
    def test_returns_none_when_no_heading(self):
        self.assertIsNone(find_qualification_section("아무 관련 없는 문서 내용입니다."))

    def test_skips_table_of_contents_style_run_on_line(self):
        """목차처럼 줄바꿈 없이 나열된 항목은 줄 시작이 아니라 매칭되면 안 된다."""
        text = (
            "Ⅱ. 제안 일반사항      1. 입찰 및 계약방법      2. 입찰참가자격      3. 세부 제안절차\n"
            "\n"
            "본문 내용...\n"
            "2. 입찰참가자격\n"
            " 가. 진짜 자격 요건입니다.\n"
            " 나. 두 번째 요건입니다.\n"
            "3. 다음 절\n"
        )
        section = find_qualification_section(text)
        self.assertIsNotNone(section)
        self.assertIn("진짜 자격 요건", section.body)
        self.assertNotIn("다음 절", section.body)

    def test_stops_before_next_top_level_heading(self):
        text = "5. 입찰 참가자격\n 가. 요건 하나\n 나. 요건 둘\n6. 입찰내용\n여기는 포함되면 안 됨\n"
        section = find_qualification_section(text)
        self.assertNotIn("포함되면 안 됨", section.body)

    def test_does_not_stop_at_decimal_sub_numbering(self):
        """'3.1.'처럼 소항목 번호는 새 최상위 절로 오인하면 안 된다."""
        text = "3. 입찰참가자격\n 3.1. 요건 하나\n 3.2. 요건 둘\n4. 다음 절\n여긴 빠져야 함\n"
        section = find_qualification_section(text)
        self.assertIn("요건 둘", section.body)
        self.assertNotIn("빠져야 함", section.body)

    def test_captures_to_end_of_text_when_no_next_heading(self):
        text = "5. 입찰 참가자격\n 가. 마지막 절이라 다음 헤딩이 없음\n"
        section = find_qualification_section(text)
        self.assertIn("마지막 절", section.body)

    def test_heading_with_trailing_text_on_same_line_is_matched(self):
        """실측: 두바이 의료기기전시회 한국관 공고문 — "4. 입찰참가자격 : 안내문"처럼
        콜론 뒤에 같은 줄로 안내문이 붙어도 절 제목으로 인식해야 한다."""
        text = (
            "4. 입찰참가자격 : 아래 자격을 모두 충족하는 경우에만 본 입찰에 참여 가능\n"
            "o 첫째 요건\n"
            "o 둘째 요건\n"
            "5. 다음 절\n여긴 빠져야 함\n"
        )
        section = find_qualification_section(text)
        self.assertIsNotNone(section)
        self.assertIn("첫째 요건", section.body)
        self.assertNotIn("빠져야 함", section.body)


class TestSplitItems(unittest.TestCase):
    def test_splits_korean_letter_items(self):
        body = " 가. 첫째\n 나. 둘째\n 다. 셋째\n"
        items = split_items(body)
        self.assertEqual(len(items), 3)
        self.assertTrue(items[0].startswith("가."))
        self.assertTrue(items[1].startswith("나."))

    def test_splits_bullet_items(self):
        """실측: 두바이 의료기기전시회 한국관 공고문 — "o" 불릿으로 나열된 항목."""
        body = "o 첫째 요건\no 둘째 요건\no 셋째 요건\n"
        items = split_items(body)
        self.assertEqual(len(items), 3)
        self.assertTrue(items[0].startswith("o 첫째"))

    def test_splits_bullet_items_indented_with_ideographic_space(self):
        """HWP류 문서는 들여쓰기에 전각 공백(U+3000)을 쓰기도 한다 — 일반
        공백/탭만 인식하면 이런 문서의 불릿을 통째로 항목 1개로 오인한다."""
        body = "　o 첫째 요건\n　o 둘째 요건\n"
        items = split_items(body)
        self.assertEqual(len(items), 2)

    def test_splits_decimal_sub_items(self):
        body = " 3.1. 첫째\n 3.2. 둘째\n"
        items = split_items(body)
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0].startswith("3.1."))

    def test_splits_numbered_paren_items_when_no_letter_or_decimal_markers(self):
        body = " 가. 아래 조건을 모두 충족한 업체\n    1) 첫째 조건\n    2) 둘째 조건\n    3) 셋째 조건\n"
        items = split_items(body)
        self.assertEqual(len(items), 3)
        self.assertTrue(items[0].startswith("1)"))
        self.assertTrue(items[2].startswith("3)"))

    def test_falls_back_to_whole_body_when_no_marker_recognized(self):
        body = "이 문서는 알려진 어떤 목록 기호도 쓰지 않는다."
        self.assertEqual(split_items(body), [body])

    def test_empty_body_returns_empty_list(self):
        self.assertEqual(split_items("   \n  "), [])

    def test_splits_circled_number_items(self):
        """실측: 한국항공우주연구원 나로우주센터 공고(R26BK01710751) — 참가자격
        절이 원문자(①②③④)로 나열됐다."""
        body = " ① 첫째 요건\n ② 둘째 요건\n ③ 셋째 요건\n ④ 넷째 요건\n"
        items = split_items(body)
        self.assertEqual(len(items), 4)
        self.assertTrue(items[0].startswith("①"))
        self.assertTrue(items[3].startswith("④"))

    def test_single_marker_is_not_enough_to_split(self):
        """마커가 1개뿐이면(전체가 '가.' 하나 아래) 그 패턴으론 안 쪼갠다 — 다음 패턴을 시도."""
        body = " 가. 이 항목 하나뿐\n    1) 실제 요건 A\n    2) 실제 요건 B\n"
        items = split_items(body)
        # '가.'는 1개뿐이라 레터 분할은 안 되고, 대신 '1) 2)' 숫자 목록으로 쪼개져야 한다.
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0].startswith("1)"))


class TestRealFixtures(unittest.TestCase):
    """실제 나라장터 첨부파일로 검증 (tests/fixtures/attachments)."""

    FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "attachments"

    def _extract(self, file_name: str) -> str:
        from nego.attachments import extract_text

        path = self.FIXTURES_DIR / file_name
        return extract_text(path.read_bytes(), path.suffix.lstrip("."))

    def test_notice_uses_korean_letter_items(self):
        """정선군 공고문: '5. 입찰 참가자격' 아래 가~차 10개 항목."""
        text = self._extract("jeongseon_culture_center_notice.hwpx")
        section = find_qualification_section(text)
        self.assertIsNotNone(section)
        self.assertEqual(section.heading, "5. 입찰 참가자격")
        self.assertEqual(len(section.items), 10)
        self.assertTrue(section.items[0].startswith("가."))

    def test_rfp_uses_numbered_paren_items_under_single_letter(self):
        """정선군 제안요청서: '가.' 하나 아래 1)~5) 5개 실제 요건."""
        text = self._extract("jeongseon_culture_center_rfp.hwpx")
        section = find_qualification_section(text)
        self.assertIsNotNone(section)
        self.assertEqual(section.heading, "2. 입찰참가자격")
        self.assertEqual(len(section.items), 5)
        self.assertIn("실내건축공사업", section.items[2])

    def test_sejong_hwpx_uses_decimal_items(self):
        """세종 입찰설명서(HWPX): '3. 입찰참가자격' 아래 3.1~3.3, 다음 절(4.) 앞에서 멈춤."""
        text = self._extract("sejong_labor_relations_bid_explanation.hwpx")
        section = find_qualification_section(text)
        self.assertIsNotNone(section)
        self.assertEqual(len(section.items), 3)
        self.assertIn("세종특별자치시", section.items[0])

    def test_task_order_has_no_qualification_section(self):
        """과업지시서에는 참가자격 절 자체가 없다 — None을 반환해야 한다."""
        text = self._extract("jeongseon_culture_center_task_order.hwpx")
        self.assertIsNone(find_qualification_section(text))

    def test_sejong_pdf_finds_heading_but_overruns_boundary(self):
        """알려진 한계 회귀 테스트: PDF는 문단 구분이 없어 다음 절까지 딸려온다.

        완벽한 경계 검출을 요구하지 않는다 — 절 시작과 첫 항목 내용이
        맞는지만 확인한다 (모듈 docstring의 '한계' 참고).
        """
        text = self._extract("sejong_labor_relations_bid_explanation.pdf")
        section = find_qualification_section(text)
        self.assertIsNotNone(section)
        self.assertIn("세종특별자치시", section.items[0])
        self.assertGreater(len(section.items), 3)  # 다음 절(4. 이하)까지 딸려옴 — 알려진 한계


if __name__ == "__main__":
    unittest.main()
