"""첨부의 "사업 범위" 목록 꺼내기 (`nego/business_scope.py`) — 2026-10-01 사용자 제공 문서 두 건의 모양."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego.business_scope import extract_scope_items  # noqa: E402

IMSIL = """    2. 사업 개요
    3. 과업 내용
   Ⅱ. 과업내용 및 지침
3. 과업 내용
  가. 사업주체: 전북특별자치도임실교육지원청
  라. 규   모: 60㎡
  마. 사업내용의 범위
    1) 자전거보관함용도로 설계된 공간(60㎡)을 재구조화하여 체험 공간 연출 및 공간 창출 계획 제안
    2) 체험 공간 연출 기획(디자인), 체험물 설계 및 제작・설치(컨텐츠 포함)
    3) 그래픽, 연출 영상, 최신 인터렉티브 체험공간 기획
    4) 사진, 관련 이미지의 저작권료, 번역, 검수, 자문 등
  바. (구)자전거보관함, 꿈꾸는 강터 예정 배치도
   1) 과업위치
"""

MARITIME = """□ 사업 내용
 ㅇ 시설 현황
  - 위    치: 부산광역시 영도구 해양로 301번길 45 국립해양박물관 2층
 ㅇ 사업 범위
   - 전시 주제에 따른 전시 연출·제작·시공 일체
    · 전시 공간 구성 계획 및 설계도서(기본․실시) 작성
    · 전시 사인물 제작 및 설치(그래픽 패널, 네임카드 등)
    · 전시 폐막 후 전시 공간 원상 복구
 ㅇ 사업기간 : 계약체결일 ~ 2027. 3. 31.(수)
   - 전시 연출 계획 및 설계 : 계약체결일로부터 3주 이내
"""


class TestBusinessScope(unittest.TestCase):
    def test_hangul_item_heading(self):
        items = extract_scope_items(IMSIL)
        self.assertEqual(len(items), 4)
        self.assertTrue(items[1].startswith("체험 공간 연출 기획(디자인), 체험물 설계 및 제작"))
        self.assertNotIn("과업위치", " ".join(items), "다음 항목 '바.'에서 끊는다")

    def test_o_marker_heading_stops_at_next_o(self):
        items = extract_scope_items(MARITIME)
        self.assertEqual(items[0], "전시 주제에 따른 전시 연출·제작·시공 일체")
        self.assertEqual(len(items), 4)
        self.assertFalse(any("사업기간" in i or "3주" in i for i in items))

    def test_no_scope_or_only_notice_text(self):
        self.assertEqual(extract_scope_items("1. 입찰참가자격\n 가. 실내건축공사업 등록 업체"), [])
        notice = " ㅇ 과업 범위\n  - 거점: 정선군 일원\n  - 확산: 군 내 전역\n ㅇ 기간"
        self.assertEqual(extract_scope_items(notice), [], "과업 동사가 없는 목록(위치·안내문)은 건너뜀")


if __name__ == "__main__":
    unittest.main()
