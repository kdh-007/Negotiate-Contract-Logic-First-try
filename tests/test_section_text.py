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

    def test_one_or_two_real_sections_are_accepted(self):
        """사용자 확인(2026-09-22, 화진포 씨월드·함양 곶감 조형물 과업지시서/제안요청서):
        최상위 절이 1~2개뿐인 문서도 실제로 존재하는 정상 구조다 — 이전에는 최소
        3개를 요구해 이런 문서를 전부 fail-open으로 흘렸는데, 실측으로 확인된
        만큼 1개부터도 인정한다."""
        text = "Ⅰ. 과업개요\n개요 내용\nⅡ. 과업 세부내용\n세부 내용\n"
        sections = split_sections(text)
        headings = [s.heading for s in sections]
        self.assertEqual(headings, ["Ⅰ. 과업개요", "Ⅱ. 과업 세부내용"])

        single = split_sections("Ⅰ. 과업개요\n개요 내용만 있는 문서\n")
        self.assertEqual(len(single), 1)
        self.assertEqual(single[0].heading, "Ⅰ. 과업개요")

    def test_boxed_title_heading_on_next_line(self):
        """실측(사용자 스크린샷, 2026-09-22 — 국립무형유산원 2020 과업지시서,
        시아의여행 2017 제안요청서): 번호가 박스 안에, 제목이 옆에 굵은 글씨로
        배치된 디자인은 텍스트로 뽑으면 번호만 있는 줄 다음에 제목 줄이 온다
        ("Ⅰ." 한 줄짜리 표기가 아니라 "Ⅰ\\n과업개요")."""
        text = "Ⅰ\n과업개요\n개요 내용\nⅡ\n과업내용\n내용\nⅢ\n제출서류\n서류\n"
        sections = split_sections(text)
        headings = [s.heading for s in sections]
        self.assertEqual(headings, ["Ⅰ 과업개요", "Ⅱ 과업내용", "Ⅲ 제출서류"])
        self.assertIn("개요 내용", sections[0].body)

    def test_boxed_and_inline_roman_styles_mix_in_one_document(self):
        """실측(KOSCOM 2017 제안요청서): 한 문서 안에서 앞쪽 절은 "Ⅰ. 제목" 한
        줄짜리로, 뒤쪽 절은 박스형("Ⅰ\\n제목")으로 섞여 쓰이기도 한다 — 두 패턴의
        매치를 합쳐 위치순으로 정렬해야 순서가 안 꼬인다."""
        text = "Ⅰ. 사업개요\n개요 내용\nⅡ\n제안업체 일반사항\n일반사항 내용\nⅢ. 사업수행 부문\n수행 내용\n"
        sections = split_sections(text)
        headings = [s.heading for s in sections]
        self.assertEqual(headings, ["Ⅰ. 사업개요", "Ⅱ 제안업체 일반사항", "Ⅲ. 사업수행 부문"])

    def test_ascii_roman_numerals_are_recognized_as_headings(self):
        """실측(국립중앙과학관 2017 제안요청서): 목차는 유니코드 로마숫자(Ⅰ)를
        쓰면서 실제 본문 헤딩은 똑같이 생긴 ASCII 알파벳("I. II. III. IV.")으로
        쓴다 — pypdf 추출 텍스트에서 `text.find('IV.')`로 직접 확인. 유니코드
        로마숫자 패턴만으로는 이 문서의 본문 헤딩을 전혀 못 찾아 후보가 0개였다."""
        text = (
            "I. 개요\n 1. 사업목적\n  가. 국립중앙과학관 야외전시장 물과학공원\n"
            "II. 과업내용 및 지침\n 1. 과업내용 및 범위\n  가. 과업내용\n"
            "III. 제안서 작성요령 및 제출\n 1. 제출도서의 종류 및 규격\n"
            "IV. 제안서 평가\n 1. 평가 기준\n"
        )
        sections = split_sections(text)
        headings = [s.heading for s in sections]
        self.assertEqual(
            headings,
            ["I. 개요", "II. 과업내용 및 지침", "III. 제안서 작성요령 및 제출", "IV. 제안서 평가"],
        )
        self.assertIn("물과학공원", sections[0].body)

    def test_roman_listing_sentence_is_not_mistaken_for_a_heading(self):
        """실측(국립중앙과학관 2017 제안요청서 등 16건): "Ⅰ., Ⅱ., Ⅲ., Ⅳ. ····"처럼
        절 번호를 한 문장에서 나열하는 상투 문구가 "Ⅰ."로 시작해 매칭되면서, 진짜
        "마지막 Ⅰ"보다 뒤에 있으면 경계를 잘못 잡아 구조를 통째로 놓친다."""
        text = (
            "Ⅰ. 개요\n개요 내용\n"
            "Ⅱ. 과업내용\n과업 내용\n"
            "Ⅲ. 제안서 작성요령\n작성요령 내용\n"
            "Ⅳ. 제안서 평가\n"
            "  Ⅰ., Ⅱ., Ⅲ., Ⅳ. 항목별 배점은 붙임 기준에 따른다\n"
            "평가 내용\n"
        )
        sections = split_sections(text)
        headings = [s.heading for s in sections]
        self.assertEqual(headings, ["Ⅰ. 개요", "Ⅱ. 과업내용", "Ⅲ. 제안서 작성요령", "Ⅳ. 제안서 평가"])
        self.assertIn("평가 내용", sections[3].body)

    def test_arabic_with_korean_letter_subitems_is_trusted(self):
        """실측(고성군 독립만세운동 기념탑 2019 제안요청서): 로마숫자/장 표기 없이
        "1. 2. 3."에 "가.나.다." 하위항목이 붙는 문서도 있다 — 가나다 하위항목이
        뒤따르는지로 진짜 절과 법률식 조항 나열을 구분한다."""
        text = (
            "1. 사업내용\n"
            "가. 사업목적\n목적 내용\n"
            "나. 기본방향\n방향 내용\n"
            "2. 제안안내\n"
            "가. 참가자격\n자격 내용\n"
            "나. 접수방법\n접수 내용\n"
            "3. 제안서 작성\n"
            "가. 작성요령\n요령 내용\n"
            "나. 제출서류\n서류 내용\n"
        )
        sections = split_sections(text)
        headings = [s.heading for s in sections]
        self.assertEqual(headings, ["1. 사업내용", "2. 제안안내", "3. 제안서 작성"])
        self.assertIn("목적 내용", sections[0].body)

    def test_circled_pua_glyph_headings(self):
        """실측(사용자 제보, 2026-09-22 — 법천사지·웅진백제역사관 과업지시서 원문
        직접 역추적): 원문자(①②③...) 대신 한컴 전용 폰트의 유니코드 개인 영역
        글리프(U+F02B1부터 순서대로)로 절 번호를 매기는 문서가 있다. 표시용으로는
        실제 원문자로 바꿔 담아야 한다."""
        text = (
            "\U000f02b1 사업 개요\n개요 내용\n"
            "\U000f02b2 과업 내용\n과업 내용\n"
            "\U000f02b3 기본지침\n지침 내용\n"
        )
        sections = split_sections(text)
        headings = [s.heading for s in sections]
        self.assertEqual(headings, ["① 사업 개요", "② 과업 내용", "③ 기본지침"])
        self.assertIn("지침 내용", sections[2].body)

    def test_toc_entries_ending_in_a_page_number_are_not_mistaken_for_headings(self):
        """실측(사용자 지적, 2026-09-22 — 국립중앙과학관 2017 제안요청서): 이 문서는
        "Ⅰ.Ⅱ.Ⅲ.Ⅳ."가 전체에서 딱 1번씩만 등장했는데 그 1번이 전부 목차 줄이었다
        ("Ⅰ. 개요 1"의 "1"은 페이지 번호). "번호 1의 마지막 등장을 경계로 삼는다"는
        로직은 최소 2번 등장(목차+본문)을 전제하는데, 여기서는 그 전제가 깨져서
        목차 자체를 4개 절로 잘못 쪼갰고 진짜 본문은 마지막 절에 전부 뭉뚱그려
        들어갔다. 제목이 페이지 번호로 끝나는 후보는 아예 제외해야 한다."""
        text = (
            "목    차\n"
            "Ⅰ. 개요 1\n"
            "Ⅱ. 과업내용 및 지침 5\n"
            "Ⅲ. 제안서 작성요령 및 제출 20\n"
            "Ⅳ. 제안서 평가 26\n"
            "\n"
            "실제로는 이 문서 전체에 로마숫자 헤딩이 이게 전부다 — 본문 어디에도\n"
            "Ⅰ.Ⅱ.Ⅲ.Ⅳ.가 다시 나오지 않는다. 사업목적 관련 진짜 내용은 그냥 평문으로\n"
            "이어진다.\n"
        )
        sections = split_sections(text)
        # 로마숫자 4개를 그대로 절로 잘못 쪼개면 안 된다 — 목차 줄로 걸러져서
        # 로마숫자 방식 자체가 실패하고, 다른 방식도 안 맞으면 fail-open이어야 한다.
        self.assertEqual(sections[0].heading, "")

    def test_arabic_clause_with_incidental_letters_still_fails_open(self):
        """가나다가 우연히 섞인 조항 설명 문장("N. 입찰자는 ... 하여야 한다.")은
        절 제목치고 너무 길어서(20자 초과) 여전히 걸러져야 한다(실측 6건)."""
        text = (
            "1. 입찰자는 수행실적 평가를 위하여 다음 각 목의 서류를 제출하여야 한다.\n"
            "가. 사업수행실적증명서\n나. 계약서 사본\n"
            "2. 공동수급체의 경우 구성원별 실적에 지분율을 곱하여 평가한다.\n"
            "가. 지분율 산정기준\n나. 합산방법\n"
            "3. 위 기준에 따라 최종 순위를 결정한다.\n"
            "가. 동점자 처리\n나. 이의신청\n"
        )
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
