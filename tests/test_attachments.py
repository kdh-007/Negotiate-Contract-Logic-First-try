"""첨부파일 텍스트 추출 단위 테스트. 표준 라이브러리 unittest만 사용한다.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
import unittest.mock
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
    save_attachment_texts,
)
from nego.models import notice_from_raw  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "attachments"


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


def _build_pdf_pages(texts: list[str]) -> bytes:
    """여러 페이지짜리 최소 PDF. 한 페이지 추출 실패 시 나머지 보존 테스트용."""
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    catalog_num = add(b"")  # 자리만 예약, 아래서 채움
    pages_num = add(b"")
    font_num = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_nums = []
    for text in texts:
        content = f"BT /F1 24 Tf 10 100 Td ({text}) Tj ET".encode("latin-1")
        content_num = add(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
        page_num = add(
            b"<< /Type /Page /Parent "
            + str(pages_num).encode()
            + b" 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 "
            + str(font_num).encode()
            + b" 0 R >> >> /Contents "
            + str(content_num).encode()
            + b" 0 R >>"
        )
        page_nums.append(page_num)

    objs[catalog_num - 1] = b"<< /Type /Catalog /Pages " + str(pages_num).encode() + b" 0 R >>"
    kids = " ".join(f"{n} 0 R" for n in page_nums).encode()
    objs[pages_num - 1] = b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_nums)).encode() + b" >>"

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


def _build_pdf_with_broken_cid_font() -> bytes:
    """실측 pypdf 버그를 그대로 재현한 3페이지 PDF.

    1페이지: 정상 Helvetica. 2페이지: /Subtype이 /Type0인데 /DescendantFonts가
    없는 비정상 폰트(ToUnicode CMap으로 "AB"를 인코딩) — pypdf가 이 폰트를
    만나면 KeyError('/DescendantFonts')를 던진다(_font.py). 3페이지: 다시
    정상 Helvetica. 세 페이지 모두 성공적으로 뽑혀야 실제 버그가 고쳐진 것이다
    (건너뛰는 게 아니라 진짜로 파싱됨).
    """
    cmap_stream = (
        b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        b"2 beginbfchar\n<0041> <0041>\n<0042> <0042>\nendbfchar\nendcmap\nend\nend\n"
    )

    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    catalog_num = add(b"")
    pages_num = add(b"")
    helvetica_num = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    cmap_num = add(b"<< /Length " + str(len(cmap_stream)).encode() + b" >>\nstream\n" + cmap_stream + b"\nendstream")
    # 실측 버그의 핵심: Type0인데 /DescendantFonts가 없다.
    broken_font_num = add(
        b"<< /Type /Font /Subtype /Type0 /BaseFont /BrokenCID /Encoding /Identity-H /ToUnicode "
        + str(cmap_num).encode()
        + b" 0 R >>"
    )

    def add_page(font_num: int, content: bytes) -> int:
        content_num = add(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
        return add(
            b"<< /Type /Page /Parent "
            + str(pages_num).encode()
            + b" 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 "
            + str(font_num).encode()
            + b" 0 R >> >> /Contents "
            + str(content_num).encode()
            + b" 0 R >>"
        )

    page1 = add_page(helvetica_num, b"BT /F1 24 Tf 10 100 Td (Region limit page) Tj ET")
    page2 = add_page(broken_font_num, b"BT /F1 24 Tf 10 100 Td <00410042> Tj ET")
    page3 = add_page(helvetica_num, b"BT /F1 24 Tf 10 100 Td (Joint supply page) Tj ET")

    objs[catalog_num - 1] = b"<< /Type /Catalog /Pages " + str(pages_num).encode() + b" 0 R >>"
    kids = f"{page1} 0 R {page2} 0 R {page3} 0 R".encode()
    objs[pages_num - 1] = b"<< /Type /Pages /Kids [" + kids + b"] /Count 3 >>"

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

    def test_recovers_real_text_from_font_missing_descendant_fonts(self):
        """실측 버그의 근본 수정 검증 (건너뛰기가 아니라 실제 파싱).

        pypdf(_font.py)는 Type1/TrueType/Type3가 아닌 폰트는 무조건
        /DescendantFonts가 있다고 가정하고 바로 인덱싱한다 — 나라장터 첨부
        PDF 일부가 이 키 없는 Type0 폰트를 써서 KeyError로 문서 전체가
        실패했었다. `_patch_missing_descendant_fonts`가 빈 배열을 채워
        넣으면, 그 정보는 글자 폭(레이아웃) 계산에만 쓰이므로 실제
        텍스트(ToUnicode CMap으로 디코딩된 "AB")는 정상적으로 뽑혀야 한다.
        """
        data = _build_pdf_with_broken_cid_font()
        text = extract_text(data, "pdf")

        self.assertIn("Region limit page", text)
        self.assertIn("AB", text)  # 깨진 폰트 페이지도 실제로 파싱됨 — 건너뛴 게 아님
        self.assertIn("Joint supply page", text)
        self.assertNotIn("텍스트 추출 실패", text)

    def test_unexpected_per_page_failure_is_shown_not_silently_dropped(self):
        """패치로도 못 살리는 다른 원인의 실패는, 조용히 빼지 않고 표시만 남긴다."""
        data = _build_pdf_pages(["Region limit page", "Broken page", "Joint supply page"])
        with unittest.mock.patch(
            "pypdf._page.PageObject.extract_text",
            side_effect=["Region limit page", RuntimeError("무언가 다른 원인"), "Joint supply page"],
        ):
            text = extract_text(data, "pdf")

        self.assertIn("Region limit page", text)
        self.assertIn("Joint supply page", text)
        self.assertIn("2페이지 텍스트 추출 실패", text)
        self.assertIn("원본 파일에서 직접 확인", text)

    def test_all_pages_failing_raises_attachment_error(self):
        data = _build_pdf_pages(["Page one", "Page two"])
        with unittest.mock.patch(
            "pypdf._page.PageObject.extract_text",
            side_effect=RuntimeError("무언가 다른 원인"),
        ):
            with self.assertRaises(AttachmentError):
                extract_text(data, "pdf")


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

    def test_table_renders_as_rows_and_cells_not_a_single_jammed_line(self):
        """평가기준 배점표 재현: 표는 행=줄바꿈, 셀=' | '로 나와야 한다.

        예전엔 root.iter()로 전부 훑어서 표 안 문단이 상위 문단과 합쳐져
        구분자 없이 한 줄로 뭉쳐지고, 동시에 독립 문단으로도 다시 잡혀
        중복까지 됐다 (실제 정선군 제안요청서 배점표에서 발견됨).
        """
        xml = (
            b'<?xml version="1.0"?>'
            b'<hp:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            b"<hp:p><hp:run><hp:t>1. \xed\x8f\x89\xea\xb0\x80\xea\xb8\xb0\xec\xa4\x80</hp:t>"
            b'<hp:tbl><hp:tr>'
            b"<hp:tc><hp:subList><hp:p><hp:run><hp:t>\xea\xb5\xac\xeb\xb6\x84</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"<hp:tc><hp:subList><hp:p><hp:run><hp:t>\xeb\xb0\xb0\xec\xa0\x90</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"</hp:tr><hp:tr>"
            b"<hp:tc><hp:subList><hp:p><hp:run><hp:t>\xec\x88\x98\xed\x96\x89\xea\xb2\xbd\xed\x97\x98</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"<hp:tc><hp:subList><hp:p><hp:run><hp:t>5.0</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"</hp:tr></hp:tbl>"
            b"</hp:run></hp:p>"
            b"</hp:sec>"
        )
        text = _hwpx_section_text(xml)
        self.assertEqual(text, "1. 평가기준\n구분 | 배점\n수행경험 | 5.0")
        # 표 안 문단이 별도 문단으로 중복되지 않아야 한다.
        self.assertEqual(text.count("구분"), 1)
        self.assertEqual(text.count("수행경험"), 1)

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


class _FakeQualification:
    def __init__(self, checked: bool):
        self.checked = checked


class _FakeCandidate:
    """save_attachment_texts는 candidate.notice/.qualification만 본다 —
    pipeline.Candidate 전체를 안 만들어도 된다. checked=True가 기본값이라
    held_codes를 넘겨도 (API로 이미 판정됐다고 보고) 재판정을 시도하지 않는다.
    """

    def __init__(self, notice, checked: bool = True):
        self.notice = notice
        self.qualification = _FakeQualification(checked)


class TestSaveAttachmentTexts(unittest.TestCase):
    def test_writes_text_and_qualification_summary_for_real_fixture(self):
        fixture = FIXTURES_DIR / "jeongseon_culture_center_notice.hwpx"
        raw = {
            "bidNtceNo": "R26TEST0001",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고",
            "ntceSpecFileNm1": fixture.name,
            "ntceSpecDocUrl1": f"file://{fixture}",
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(fixture.read_bytes())

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            stats = save_attachment_texts([_FakeCandidate(notice)], output_dir, timeout=5.0, session=session)

            self.assertEqual(
                stats,
                {
                    "attempted": 1,
                    "ok": 1,
                    "failed": 0,
                    "qualification_found": 1,
                    "qualification_determined": 0,
                },
            )

            text_dir = output_dir / "attachment_text"
            written = {p.name for p in text_dir.iterdir()}
            base = f"R26TEST0001_000_1_{fixture.name}"
            self.assertIn(f"{base}.txt", written)
            self.assertIn(f"{base}_참가자격.txt", written)

            summary = (text_dir / f"{base}_참가자격.txt").read_text(encoding="utf-8")
            self.assertTrue(summary.startswith("5. 입찰 참가자격"))
            self.assertIn("실내건축공사업", summary)

    def test_no_summary_file_when_no_qualification_section(self):
        fixture = FIXTURES_DIR / "jeongseon_culture_center_task_order.hwpx"
        raw = {
            "bidNtceNo": "R26TEST0002",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고2",
            "ntceSpecFileNm1": fixture.name,
            "ntceSpecDocUrl1": f"file://{fixture}",
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(fixture.read_bytes())

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            stats = save_attachment_texts([_FakeCandidate(notice)], output_dir, timeout=5.0, session=session)
            self.assertEqual(stats["qualification_found"], 0)
            written = {p.name for p in (output_dir / "attachment_text").iterdir()}
            self.assertEqual(len(written), 1)  # .txt만, _참가자격.txt는 없음

    def test_replaces_qualification_with_pass_when_all_codes_held(self):
        """API 자격정보가 없는(checked=False) 공고는 첨부파일 참가자격 절의
        업종코드/세부품명번호를 보유 명단과 대조해 candidate.qualification 자체를
        갈아끼운다 — 다른 공고와 똑같이 '자격 충족'/'자격 미달(이름)'로 보이도록."""
        fixture = FIXTURES_DIR / "jeongseon_culture_center_notice.hwpx"
        raw = {
            "bidNtceNo": "R26TEST0001",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고",
            "ntceSpecFileNm1": fixture.name,
            "ntceSpecDocUrl1": f"file://{fixture}",
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(fixture.read_bytes())
        candidate = _FakeCandidate(notice, checked=False)
        # 실측 문서(정선군 복합문화센터)가 요구하는 코드 전부를 보유했다고 가정.
        held_codes = {"6010989901", "5610150701", "5611210501", "4990", "4442", "4444", "6484", "1469"}

        with tempfile.TemporaryDirectory() as tmp:
            stats = save_attachment_texts([candidate], Path(tmp), timeout=5.0, session=session, held_codes=held_codes)

        self.assertEqual(stats["qualification_determined"], 1)
        self.assertTrue(candidate.qualification.checked)
        self.assertEqual(candidate.qualification.summary, "자격 충족")

    def test_replaces_qualification_with_fail_when_a_code_missing(self):
        fixture = FIXTURES_DIR / "jeongseon_culture_center_notice.hwpx"
        raw = {
            "bidNtceNo": "R26TEST0001",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고",
            "ntceSpecFileNm1": fixture.name,
            "ntceSpecDocUrl1": f"file://{fixture}",
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(fixture.read_bytes())
        candidate = _FakeCandidate(notice, checked=False)
        # 실내건축공사업(4990)만 빠뜨린 명단.
        held_codes = {"6010989901", "5610150701", "5611210501", "4442", "4444", "6484", "1469"}

        with tempfile.TemporaryDirectory() as tmp:
            stats = save_attachment_texts([candidate], Path(tmp), timeout=5.0, session=session, held_codes=held_codes)

        self.assertEqual(stats["qualification_determined"], 1)
        self.assertIn("자격 미달", candidate.qualification.summary)
        self.assertIn("실내건축공사업", candidate.qualification.summary)

    def test_qualification_untouched_when_already_checked_by_api(self):
        """API에서 이미 자격정보를 받은(checked=True) 공고는 재판정을 건너뛴다."""
        fixture = FIXTURES_DIR / "jeongseon_culture_center_notice.hwpx"
        raw = {
            "bidNtceNo": "R26TEST0001",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고",
            "ntceSpecFileNm1": fixture.name,
            "ntceSpecDocUrl1": f"file://{fixture}",
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(fixture.read_bytes())
        candidate = _FakeCandidate(notice, checked=True)
        original_qualification = candidate.qualification

        with tempfile.TemporaryDirectory() as tmp:
            stats = save_attachment_texts(
                [candidate], Path(tmp), timeout=5.0, session=session, held_codes={"4990"}
            )

        self.assertEqual(stats["qualification_determined"], 0)
        self.assertIs(candidate.qualification, original_qualification)

    def test_qualification_untouched_when_no_qualification_section_found(self):
        """참가자격 절 자체가 없는 문서(과업지시서 등)는 판정 근거가 없으니 손대지 않는다."""
        fixture = FIXTURES_DIR / "jeongseon_culture_center_task_order.hwpx"
        raw = {
            "bidNtceNo": "R26TEST0002",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고2",
            "ntceSpecFileNm1": fixture.name,
            "ntceSpecDocUrl1": f"file://{fixture}",
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(fixture.read_bytes())
        candidate = _FakeCandidate(notice, checked=False)
        original_qualification = candidate.qualification

        with tempfile.TemporaryDirectory() as tmp:
            stats = save_attachment_texts(
                [candidate], Path(tmp), timeout=5.0, session=session, held_codes={"4990"}
            )

        self.assertEqual(stats["qualification_determined"], 0)
        self.assertIs(candidate.qualification, original_qualification)

    def test_qualification_untouched_when_download_fails(self):
        att = {"seq": "1", "file_name": "a.hwpx", "url": "https://example.com/a.hwpx", "ext": "hwpx"}
        raw = {
            "bidNtceNo": "R26TEST0003",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고3",
            "ntceSpecFileNm1": att["file_name"],
            "ntceSpecDocUrl1": att["url"],
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(b"", status_code=404)
        candidate = _FakeCandidate(notice, checked=False)
        original_qualification = candidate.qualification

        with tempfile.TemporaryDirectory() as tmp:
            stats = save_attachment_texts(
                [candidate], Path(tmp), timeout=5.0, session=session, held_codes={"4990"}
            )

        self.assertEqual(stats["qualification_determined"], 0)
        self.assertIs(candidate.qualification, original_qualification)


if __name__ == "__main__":
    unittest.main()
