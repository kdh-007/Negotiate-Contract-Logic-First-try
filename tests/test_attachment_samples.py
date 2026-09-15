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

    def test_rfp_evaluation_table_has_row_and_cell_structure(self):
        """평가기준 배점표가 행/열 구분 없이 한 줄로 뭉치던 문제의 회귀 테스트."""
        text = _extract("jeongseon_culture_center_rfp.hwpx")
        header = "구분 | 평가 항목 | 평가요소 | 평가기준 | 배점 | 비고"
        self.assertIn(header, text)
        # 헤더 셀 이름이 표 밖 어딘가에 또(중복으로) 나오면 안 된다.
        self.assertEqual(text.count(header), 1)

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


class TestPersonalContactsRedacted(unittest.TestCase):
    """이 샘플들은 실제 발주기관 담당자 연락처를 담고 있던 공개공고 원문이다.

    커밋 전에 파일 자체를 편집해 지웠다 — 회귀 여부를 여기서 고정해 둔다
    (fixtures/attachments/README.md 참고). 첨부파일 텍스트 추출 시 자동으로
    연락처를 마스킹하던 기능은 방침 변경으로 제거했다 — 공개공고에 실린
    발주기관 문의처는 가릴 개인정보로 보지 않기로 했다.
    """

    _PHONE_RE = re.compile(r"0(?:2|1[016789]|[3-6][1-4]|70)[-.\s]\d{3,4}[-.\s]\d{4}")

    def test_no_fixture_leaks_a_phone_number(self):
        for path in sorted(FIXTURES_DIR.glob("*")):
            if path.suffix not in (".hwpx", ".pdf"):
                continue
            text = extract_text(path.read_bytes(), path.suffix.lstrip("."))
            self.assertEqual(self._PHONE_RE.findall(text), [], f"{path.name}에 연락처가 남아있습니다")


if __name__ == "__main__":
    unittest.main()
