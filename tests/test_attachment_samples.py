"""실제 나라장터 첨부파일(공개공고)로 텍스트 추출을 검증한다.

합성 데이터로는 잡아내기 어려운 실측 문제(줄바꿈/공백 표기, 실제 인코딩)를
확인하는 회귀 테스트다. 샘플 출처는 `tests/fixtures/attachments/README.md` 참고.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego.attachments import extract_text  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "attachments"


def _normalize(text: str) -> str:
    """공백 유무 표기 차이(문서마다 다름)를 무시하고 대조하기 위함."""
    return re.sub(r"\s+", "", text)


def _extract(file_name: str) -> str:
    path = FIXTURES_DIR / file_name
    ext = path.suffix.lstrip(".")
    return extract_text(path.read_bytes(), ext)


class TestRealHwpxSamples(unittest.TestCase):
    def test_task_order_contains_license_clause(self):
        text = _normalize(_extract("jeongseon_culture_center_task_order.hwpx"))
        self.assertIn(_normalize("기술자격(면허)종별"), text)

    def test_notice_identifies_negotiated_contract(self):
        text = _normalize(_extract("jeongseon_culture_center_notice.hwpx"))
        self.assertIn(_normalize("정선군 공고 제2026-1035호"), text)
        self.assertIn(_normalize("(협상에 의한 계약)"), text)

    def test_rfp_contains_joint_supply_and_evaluation_criteria(self):
        text = _normalize(_extract("jeongseon_culture_center_rfp.hwpx"))
        self.assertIn(_normalize("공동이행방식 또는 분담이행방식으로 구성할 수 있습니다"), text)
        self.assertIn(_normalize("평가기준"), text)
        self.assertIn(_normalize("정량평가"), text)

    def test_sejong_hwpx_contains_region_limit(self):
        text = _normalize(_extract("sejong_labor_relations_bid_explanation.hwpx"))
        self.assertIn(_normalize("지역제한"), text)
        self.assertIn(_normalize("세종특별자치시"), text)


class TestRealPdfSample(unittest.TestCase):
    def test_pdf_contains_region_limit_and_title(self):
        text = _normalize(_extract("sejong_labor_relations_bid_explanation.pdf"))
        self.assertIn(_normalize("중앙노동위원회 본관 인테리어공사"), text)
        self.assertIn(_normalize("지역제한"), text)
        self.assertIn(_normalize("세종특별자치시"), text)

    def test_pdf_and_hwpx_versions_agree_on_region_limit(self):
        """같은 공고의 PDF본과 HWPX본 모두 같은 지역제한을 담고 있어야 한다."""
        pdf_text = _normalize(_extract("sejong_labor_relations_bid_explanation.pdf"))
        hwpx_text = _normalize(_extract("sejong_labor_relations_bid_explanation.hwpx"))
        for text in (pdf_text, hwpx_text):
            self.assertIn(_normalize("지역제한"), text)
            self.assertIn(_normalize("세종특별자치시"), text)


if __name__ == "__main__":
    unittest.main()
