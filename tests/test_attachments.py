"""첨부파일 텍스트 추출 단위 테스트. 표준 라이브러리 unittest만 사용한다.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego.attachments import (  # noqa: E402
    AttachmentError,
    _decode_para_text,
    _hwp_section_paragraphs,
    _hwpx_section_text,
    extract_text,
    fetch_attachment_text,
    redact_personal_contacts,
)


def _build_pdf(text: str) -> bytes:
    """텍스트 추출 검증용 최소 PDF. 표준 Helvetica만 쓰므로 폰트 임베딩이 필요 없다."""
    content = f"BT /F1 24 Tf 10 100 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj".encode() + b"\n" + body + b"\nendobj\n")
    xref_offset = out.tell()
    n = len(objs) + 1
    out.write(f"xref\n0 {n}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(b"trailer\n<< /Size " + str(n).encode() + b" /Root 1 0 R >>\n")
    out.write(b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF")
    return out.getvalue()


def _build_hwpx(paragraphs: list[str]) -> bytes:
    """지역/면허/공동수급 원문 대조에 실제로 쓰일 문단 구조를 재현한 최소 HWPX."""
    body = "".join(f"<hp:p><hp:run><hp:t>{p}</hp:t></hp:run></hp:p>" for p in paragraphs)
    section_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<hp:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        f"{body}</hp:sec>"
    ).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/section0.xml", section_xml)
    return buf.getvalue()


def _build_hwp_record(tag_id: int, payload: bytes, level: int = 0) -> bytes:
    size = len(payload)
    if size < 0xFFF:
        header = (size << 20) | (level << 10) | tag_id
        return header.to_bytes(4, "little") + payload
    header = (0xFFF << 20) | (level << 10) | tag_id
    return header.to_bytes(4, "little") + size.to_bytes(4, "little") + payload


class FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code
        self.text = ""


class FakeSession:
    """requests.Session 흉내. 고정된 바이트만 돌려준다."""

    def __init__(self, content: bytes, status_code: int = 200):
        self._content = content
        self._status_code = status_code

    def get(self, url, timeout=None):
        return FakeResponse(self._content, self._status_code)


class TestPdfExtraction(unittest.TestCase):
    def test_extracts_plain_text(self):
        data = _build_pdf("Hello Nego")
        text = extract_text(data, "pdf")
        self.assertIn("Hello Nego", text)

    def test_broken_pdf_raises_attachment_error(self):
        with self.assertRaises(AttachmentError):
            extract_text(b"not a pdf", "pdf")


class TestHwpxExtraction(unittest.TestCase):
    def test_extracts_paragraphs_in_order(self):
        data = _build_hwpx(["참가가능지역: 강원특별자치도", "공동수급 허용"])
        text = extract_text(data, "hwpx")
        self.assertEqual(text, "참가가능지역: 강원특별자치도\n공동수급 허용")

    def test_missing_section_raises_attachment_error(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("mimetype", "application/hwp+zip")
        with self.assertRaises(AttachmentError):
            extract_text(buf.getvalue(), "hwpx")

    def test_not_a_zip_raises_attachment_error(self):
        with self.assertRaises(AttachmentError):
            extract_text(b"not a zip", "hwpx")

    def test_section_text_joins_runs_within_paragraph(self):
        xml = (
            b'<?xml version="1.0"?>'
            b'<hp:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            b"<hp:p><hp:run><hp:t>\xea\xb3\xb5\xeb\x8f\x99 </hp:t></hp:run>"
            b"<hp:run><hp:t>\xed\x97\x88\xec\x9a\xa9</hp:t></hp:run></hp:p>"
            b"</hp:sec>"
        )
        self.assertEqual(_hwpx_section_text(xml), "공동 허용")

    def test_extract_text_redacts_phone_and_email(self):
        """extract_text()는 형식과 무관하게 담당자 연락처를 자동으로 지운다."""
        data = _build_hwpx(["국립경주박물관 기획운영과 유아름(Tel: 054-740-7520)", "문의: nego@example.go.kr"])
        text = extract_text(data, "hwpx")
        self.assertNotIn("054-740-7520", text)
        self.assertNotIn("nego@example.go.kr", text)
        self.assertIn("유아름", text)  # 이름 자체는 남긴다 (오탐 위험 때문)


class TestRedactPersonalContacts(unittest.TestCase):
    def test_masks_labeled_phone_number(self):
        text = redact_personal_contacts("담당자(Tel: 054-740-7520)에게 문의")
        self.assertNotIn("054-740-7520", text)
        self.assertIn("담당자", text)

    def test_masks_bare_phone_in_parens_without_label(self):
        text = redact_personal_contacts("우리기관 시설팀(054-740-7520)에 문의하여 주시기 바랍니다")
        self.assertNotIn("054-740-7520", text)

    def test_masks_email(self):
        text = redact_personal_contacts("문의: nego@example.go.kr 로 보내주세요")
        self.assertNotIn("nego@example.go.kr", text)

    def test_does_not_touch_product_classification_codes(self):
        """세부품명번호·업종코드처럼 구분자 없이 붙은 숫자열은 전화번호가 아니다."""
        text = "실물모형 및 전시물(세부품명번호 6010989901)"
        self.assertEqual(redact_personal_contacts(text), text)

    def test_does_not_touch_general_hotline_number(self):
        """1588-0800처럼 지역/휴대폰 국번이 아닌 대표번호는 개인 연락처가 아니라 남긴다."""
        text = "조달청 전자조달 콜센터(☎ 1588-0800)"
        self.assertEqual(redact_personal_contacts(text), text)

    def test_does_not_corrupt_surrounding_sentence(self):
        """이름이 아닌 일반 단어(예: '확인')를 이름으로 오인해 문장을 깨면 안 된다."""
        text = redact_personal_contacts("전화로 접수 여부를 확인(☎033-560-2342)하여야 하며")
        self.assertIn("확인", text)
        self.assertNotIn("033-560-2342", text)


class TestHwpRecordParsing(unittest.TestCase):
    """OLE 컨테이너 없이, 압축 해제된 레코드 스트림 파싱만 검증한다.

    실제 .hwp 파일(OLE+zlib)까지 통째로 만드는 건 라이터 라이브러리 없이는
    비현실적이라, 이 레이어(레코드 헤더 디코딩 + 텍스트 복원)만 단위 테스트한다.
    실제 파일 검증은 회사 공고 샘플로 별도 확인이 필요하다.
    """

    def test_decode_para_text_strips_inline_control_chars(self):
        payload = "지역제한\t텍스트".encode("utf-16le")
        self.assertEqual(_decode_para_text(payload), "지역제한텍스트")

    def test_decode_para_text_keeps_newlines(self):
        payload = "1행\n2행".encode("utf-16le")
        self.assertEqual(_decode_para_text(payload), "1행\n2행")

    def test_section_paragraphs_skips_non_text_records_and_extended_size(self):
        para1 = _build_hwp_record(0x43, "협상에 의한 계약".encode("utf-16le"))
        other = _build_hwp_record(0x10, b"\x01\x02\x03\x04")  # PARA_TEXT가 아닌 레코드
        para2 = _build_hwp_record(0x43, "둘째 문단".encode("utf-16le"))
        section = para1 + other + para2

        self.assertEqual(
            _hwp_section_paragraphs(section),
            ["협상에 의한 계약", "둘째 문단"],
        )

    def test_extended_size_record_is_parsed(self):
        text = "긴 문단 " * 500  # 0xFFF(4095)바이트를 넘겨 확장 크기 분기를 타게 한다
        payload = text.encode("utf-16le")
        self.assertGreater(len(payload), 0xFFF)
        record = _build_hwp_record(0x43, payload)

        self.assertEqual(_hwp_section_paragraphs(record), [text])


class TestFetchAttachmentText(unittest.TestCase):
    def test_missing_url_reports_error_without_raising(self):
        result = fetch_attachment_text(FakeSession(b""), {"seq": "1", "file_name": "a.hwp", "url": "", "ext": "hwp"})
        self.assertFalse(result.ok)
        self.assertIn("URL", result.error)

    def test_unsupported_extension_reports_error(self):
        att = {"seq": "1", "file_name": "a.zip", "url": "https://example.com/a.zip", "ext": "zip"}
        result = fetch_attachment_text(FakeSession(b""), att)
        self.assertFalse(result.ok)
        self.assertIn("지원하지 않는", result.error)

    def test_successful_pdf_round_trip(self):
        # 표준 Helvetica 폰트는 한글을 못 그리므로(WinAnsiEncoding), 여기서는
        # 다운로드→추출 파이프라인이 예외 없이 끝까지 이어지는지만 확인한다.
        data = _build_pdf("Verify")
        att = {"seq": "2", "file_name": "제안요청서.pdf", "url": "https://example.com/2.pdf", "ext": "pdf"}
        result = fetch_attachment_text(FakeSession(data), att)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.error, None)
        self.assertIn("Verify", result.text)

    def test_http_error_reports_error_without_raising(self):
        att = {"seq": "1", "file_name": "a.pdf", "url": "https://example.com/a.pdf", "ext": "pdf"}
        result = fetch_attachment_text(FakeSession(b"", status_code=404), att)
        self.assertFalse(result.ok)
        self.assertIn("404", result.error)


if __name__ == "__main__":
    unittest.main()
