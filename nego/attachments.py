"""공고 첨부파일(HWP/HWPX/PDF) 다운로드 및 텍스트 추출.

API가 안 주는 정보(과업내용·평가기준)를 다루기 위한 밑작업이다. 오늘 범위는
**평문 텍스트 추출까지만** — 지역제한/면허제한/공동수급처럼 API로 이미 수집한
값을 원문과 대조해볼 수 있게 하는 것이 목적이다. 표 구조 파싱(평가기준 배점표)은
다음 단계에서 이 텍스트를 재료로 다룬다.

실패는 전부 `AttachmentText.error`에 담고 예외를 올리지 않는다 — 첨부파일 하나
못 읽는다고 공고 전체 수집이 죽으면 안 되기 때문이다 (fail-open).
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

import requests

if TYPE_CHECKING:
    from .models import Notice
    from .pipeline import Candidate

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {"hwp", "hwpx", "pdf"}


class AttachmentError(Exception):
    """다운로드/파싱 단계 실패. 호출측(fetch_attachment_text)이 잡아서 계속 진행한다."""


@dataclass
class AttachmentText:
    seq: str
    file_name: str
    url: str
    ext: str
    text: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def char_count(self) -> int:
        return len(self.text)


def download_bytes(session: requests.Session, url: str, timeout: float = 30.0) -> bytes:
    try:
        res = session.get(url, timeout=timeout)
    except requests.RequestException as err:
        raise AttachmentError(f"다운로드 실패: {err}") from err
    if res.status_code >= 400:
        raise AttachmentError(f"다운로드 실패: HTTP {res.status_code}")
    if not res.content:
        raise AttachmentError("빈 파일을 받았습니다")
    return res.content


# ── 개인정보(발주기관 담당자) 마스킹 ────────────────────────────────
#
# API 응답의 담당자 이름·전화·이메일은 fields.PERSONAL_FIELDS가 저장 전에
# 제거한다. 첨부파일 본문에도 문의처로 적힌 같은 종류의 정보가 자유 텍스트로
# 섞여 있어 같은 원칙으로 여기서도 지운다.
#
# 전화번호는 국번(02/010~019/031~069/070)과 구분자(-.공백)가 있는 패턴만
# 매칭한다 — 세부품명번호·업종코드처럼 구분자 없이 붙은 숫자열은 건드리지
# 않기 위해서다(실측 문서로 확인함). 이름은 일반 단어와 구분이 안 돼 오탐
# 위험이 크므로(예: "확인(전화번호)"에서 "확인"을 이름으로 착각) 마스킹
# 대상에서 뺀다 — 실제로 연락 가능하게 만드는 건 번호/이메일이지 이름 단독이
# 아니므로 이 선으로도 충분하다고 본다.
_KR_PHONE_RE = re.compile(r"0(?:2|1[016789]|[3-6][1-4]|70)[-.\s]\d{3,4}[-.\s]\d{4}")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def redact_personal_contacts(text: str) -> str:
    text = _KR_PHONE_RE.sub("(연락처 비공개)", text)
    text = _EMAIL_RE.sub("(이메일 비공개)", text)
    return text


def extract_text(data: bytes, ext: str) -> str:
    ext = ext.lower().lstrip(".")
    if ext == "pdf":
        text = _extract_pdf_text(data)
    elif ext == "hwpx":
        text = _extract_hwpx_text(data)
    elif ext == "hwp":
        text = _extract_hwp_text(data)
    else:
        raise AttachmentError(f"지원하지 않는 형식입니다: .{ext or '(확장자 없음)'}")
    return redact_personal_contacts(text)


def fetch_attachment_text(
    session: requests.Session, attachment: dict[str, str], timeout: float = 30.0
) -> AttachmentText:
    """첨부파일 1건을 내려받아 텍스트를 뽑는다. 실패해도 예외를 올리지 않는다."""
    seq = attachment.get("seq", "")
    file_name = attachment.get("file_name", "")
    url = attachment.get("url", "")
    ext = (attachment.get("ext") or "").lower()

    result = AttachmentText(seq=seq, file_name=file_name, url=url, ext=ext)

    if not url:
        result.error = "다운로드 URL이 없습니다"
        return result
    if ext not in SUPPORTED_EXTENSIONS:
        result.error = f"지원하지 않는 형식입니다: .{ext or '(확장자 없음)'}"
        return result

    try:
        data = download_bytes(session, url, timeout=timeout)
        result.text = extract_text(data, ext)
    except AttachmentError as err:
        result.error = str(err)
    except Exception as err:  # 예상 못한 오류도 파이프라인을 죽이면 안 된다
        log.exception("첨부파일 처리 중 예상 못한 오류: %s (%s)", file_name, url)
        result.error = f"예상 못한 오류: {err}"

    return result


def collect_notice_attachment_texts(
    session: requests.Session, notice: "Notice", timeout: float = 30.0
) -> list[AttachmentText]:
    return [fetch_attachment_text(session, att, timeout=timeout) for att in notice.attachments]


_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def _safe_filename(name: str) -> str:
    cleaned = _UNSAFE_FILENAME_CHARS.sub("_", name).strip()
    return cleaned or "attachment"


def save_attachment_texts(
    candidates: list["Candidate"],
    output_dir: Path,
    timeout: float = 30.0,
    session: requests.Session | None = None,
    held_codes: set[str] | None = None,
) -> dict[str, int]:
    """후보 공고의 첨부파일을 내려받아 텍스트를 `output_dir/attachment_text/`에 저장한다.

    지역제한/면허제한/공동수급을 원문과 대조해볼 수 있도록 평문만 남기는
    용도다. 첨부파일 하나가 실패해도 나머지 처리는 계속한다.

    텍스트 안에서 "입찰 참가자격" 절을 찾으면(`qualification_text` 참고)
    `<...>_참가자격.txt`로 항목별 요약도 같이 남긴다 — API의 면허제한정보가
    비어 있는 공고(발주기관이 구조화 등록을 안 한 경우, 실측: 부안청자박물관
    R26BK01719858)를 사람이 원문 전체를 뒤지지 않고 바로 확인하기 위함이다.
    절을 못 찾아도 실패로 세지 않는다 — 애초에 없는 문서가 대부분이다.

    `held_codes`를 주면, API 자격정보가 없는 공고(`candidate.qualification.checked
    is False`)에 한해 찾은 참가자격 항목으로 `qualify.evaluate_attachment_text`를
    돌려 **API와 똑같은 형태의 판정 결과로 `candidate.qualification`을 갈아끼운다**
    — 리포트에는 다른 공고와 동일하게 "자격 충족" / "자격 미달(이름)"로 표시된다.
    판정 근거를 찾지 못하면(코드가 명시된 항목이 없음) 손대지 않고 그대로
    "자격정보 없음"으로 남긴다. 이 판정은 표시용일 뿐 후보 목록 자체는 바꾸지
    않는다 — 후보/제외는 이미 API 기반 1차 판정에서 끝난 뒤이기 때문이다.
    """
    from .qualification_text import find_qualification_section
    from .qualify import evaluate_attachment_text

    text_dir = output_dir / "attachment_text"
    text_dir.mkdir(parents=True, exist_ok=True)
    session = session or requests.Session()

    stats = {"attempted": 0, "ok": 0, "failed": 0, "qualification_found": 0, "qualification_determined": 0}
    for candidate in candidates:
        notice = candidate.notice
        qualification = getattr(candidate, "qualification", None)
        needs_check = held_codes is not None and qualification is not None and not qualification.checked
        all_items: list[str] = []

        for result in collect_notice_attachment_texts(session, notice, timeout=timeout):
            stats["attempted"] += 1
            if not result.ok:
                log.warning("첨부파일 추출 실패 [%s] %s: %s", notice.notice_no, result.file_name, result.error)
                stats["failed"] += 1
                continue
            stats["ok"] += 1
            base = _safe_filename(f"{notice.notice_no}_{notice.notice_ord}_{result.seq}_{result.file_name}")
            (text_dir / f"{base}.txt").write_text(result.text, encoding="utf-8")

            section = find_qualification_section(result.text)
            if section is not None:
                stats["qualification_found"] += 1
                summary = section.heading + "\n\n" + "\n\n".join(section.items)
                (text_dir / f"{base}_참가자격.txt").write_text(summary, encoding="utf-8")
                all_items.extend(section.items)

        if not needs_check or not all_items:
            continue

        result = evaluate_attachment_text(all_items, held_codes)
        if result.checked:
            candidate.qualification = result
            stats["qualification_determined"] += 1
            if result.missing_groups:
                # 실측 문서마다 "업종코드"/"세부품명번호" 표기가 조금씩 달라 오탐
                # 가능성이 있다(예: "세부품명번호 10자리, ####" 필러) — 미달로
                # 판정된 건은 원문을 눈으로 대조할 수 있게 -v로만 남긴다.
                log.debug("첨부파일 재판정 원문 [%s]: %s", notice.notice_no, all_items)
    return stats


# ── PDF ──────────────────────────────────────────────────────────

def _extract_pdf_text(data: bytes) -> str:
    """페이지별로 추출한다. 그 전에 알려진 pypdf 결함을 먼저 패치해서

    실제로 텍스트가 뽑히게 만든다 (`_patch_missing_descendant_fonts` 참고).
    그래도 남는 예외는(다른 원인일 수 있으니) 조용히 건너뛰지 않고, 그
    페이지 자리에 "추출 실패, 원본 확인 필요" 표시를 남긴다 — 지역제한 같은
    중요 정보가 하필 그 페이지에 있었을 수 있으니 사람이 놓치면 안 된다.
    """
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
    except PdfReadError as err:
        raise AttachmentError(f"PDF 파싱 실패: {err}") from err

    _patch_missing_descendant_fonts(reader)

    total = len(reader.pages)
    pages: list[str] = []
    failed = 0
    for i, page in enumerate(reader.pages):
        try:
            pages.append(page.extract_text() or "")
        except Exception as err:  # pypdf가 던지는 예외 타입이 일정하지 않다 (KeyError 등)
            log.warning("PDF %d/%d페이지 텍스트 추출 실패: %s", i + 1, total, err)
            pages.append(f"[※ {i + 1}페이지 텍스트 추출 실패 — 이 페이지는 원본 파일에서 직접 확인해야 합니다]")
            failed += 1

    if failed and failed == total:
        raise AttachmentError(f"PDF 모든 페이지({total}개) 텍스트 추출 실패")

    return "\n\n".join(pages).strip()


def _patch_missing_descendant_fonts(reader) -> int:
    """Type0류 폰트인데 /DescendantFonts가 없으면 빈 배열을 채운다.

    실측 원인: pypdf(`_font.py Font.from_font_resource`)는 폰트가
    Type1/MMType1/TrueType/Type3가 아니면 무조건 합성(Type0/CID) 폰트로 보고
    `pdf_font_dict["/DescendantFonts"]`를 바로 인덱싱한다 — 이 키가 없는
    비정상 폰트(실측: 나라장터 첨부 PDF 일부)를 만나면 KeyError로 죽는다.

    하지만 텍스트를 실제 문자로 바꾸는 작업(인코딩/ToUnicode CMap 해석)은
    이 코드보다 **먼저** 끝나 있고 `/DescendantFonts`와 무관하다 — 이 값은
    글자 폭(레이아웃 계산)에만 쓰인다. 그래서 빈 배열을 채워 넣으면 폭
    정보만 기본값으로 빠지고 실제 텍스트는 정상 추출된다 (합성 PDF로
    직접 재현·검증함: 패치 전 KeyError, 패치 후 원문 그대로 추출).
    """
    from pypdf.generic import ArrayObject, NameObject

    patched = 0
    for page in reader.pages:
        resources = page.get("/Resources")
        if resources is None:
            continue
        fonts = resources.get_object().get("/Font")
        if fonts is None:
            continue
        for font_ref in fonts.get_object().values():
            font_dict = font_ref.get_object()
            if font_dict.get("/Subtype") in ("/Type1", "/MMType1", "/TrueType", "/Type3"):
                continue
            if "/DescendantFonts" not in font_dict:
                font_dict[NameObject("/DescendantFonts")] = ArrayObject()
                patched += 1
    return patched


# ── HWPX (zip + xml, 2020년 이후 신형식) ───────────────────────────

_HWPX_SECTION_RE = re.compile(r"^Contents/section\d+\.xml$")


def _extract_hwpx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            section_names = sorted(n for n in zf.namelist() if _HWPX_SECTION_RE.match(n))
            if not section_names:
                raise AttachmentError("HWPX 안에 Contents/section*.xml이 없습니다")
            sections = [zf.read(name) for name in section_names]
    except zipfile.BadZipFile as err:
        raise AttachmentError(f"HWPX(zip) 파싱 실패: {err}") from err

    parts = [_hwpx_section_text(section) for section in sections]
    return "\n\n".join(p for p in parts if p).strip()


def _hwpx_section_text(xml_bytes: bytes) -> str:
    """<hp:sec>의 최상위 <hp:p> 문단만 순회한다 (문단 사이는 줄바꿈으로 구분).

    표(<hp:tbl>)는 문단의 <hp:run> 안에 인라인으로 끼워져 있고, 그 표의 각 셀
    (<hp:tc>)도 내부에 자기 문단(<hp:p>)을 갖는다. `root.iter()`로 태그만 보고
    전부 훑으면 표 안 문단이 상위 문단의 텍스트에 한 번(구분자 없이 뭉쳐서),
    그리고 독립된 문단으로 또 한 번, 총 두 번 잡혀서 배점표 같은 표가
    깨지고 중복된다 — 그래서 최상위 문단만 돌고, 표를 만나면
    `_render_table`이 행/열 구조를 살려서 재귀적으로 처리한다.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as err:
        raise AttachmentError(f"HWPX 섹션 XML 파싱 실패: {err}") from err

    paragraphs = [_render_paragraph(p) for p in root if p.tag.endswith("}p")]
    return "\n".join(t for t in paragraphs if t)


def _render_paragraph(p: ET.Element) -> str:
    """문단 하나의 텍스트. 런 안에 표가 끼어 있으면 표도 이어서 렌더링한다."""
    parts = []
    for run in p:
        if not run.tag.endswith("}run"):
            continue
        for child in run:
            if child.tag.endswith("}t"):
                if child.text:
                    parts.append(child.text)
            elif child.tag.endswith("}tbl"):
                table_text = _render_table(child)
                if table_text:
                    parts.append("\n" + table_text)
    return "".join(parts)


def _render_table(tbl: ET.Element) -> str:
    """행은 줄바꿈, 같은 행의 셀은 ' | '로 구분한다 (평가기준 배점표 등)."""
    rows = []
    for tr in tbl:
        if not tr.tag.endswith("}tr"):
            continue
        cells = [_render_cell(tc) for tc in tr if tc.tag.endswith("}tc")]
        rows.append(" | ".join(cells))
    return "\n".join(rows)


def _render_cell(tc: ET.Element) -> str:
    paragraphs = []
    for sub_list in tc:
        if not sub_list.tag.endswith("}subList"):
            continue
        paragraphs.extend(_render_paragraph(p) for p in sub_list if p.tag.endswith("}p"))
    return " ".join(t for t in paragraphs if t)


# ── HWP (OLE 복합문서 + 구 바이너리 포맷) ────────────────────────────
#
# 구조: OLE 컴파운드 파일(FileHeader, BodyText/Section0, Section1, ...).
# 각 Section 스트림은 (attribute 압축 플래그가 서 있으면) raw deflate로
# 압축돼 있고, 그 안은 레코드 스트림이다. 레코드 헤더 4바이트 = tag_id(10bit)
# + level(10bit) + size(12bit, 0xFFF면 다음 4바이트가 실제 크기).
#
# 참고: 표/그림 같은 인라인 컨트롤 문자 뒤에 예약된 텍스트 슬롯을 정교하게
# 건너뛰지는 않는다 — 문단 본문은 정상 추출되지만 표/이미지가 많은 문서는
# 경계 부분에 약간의 잡음이 섞일 수 있다. 실제 공고 첨부파일로 검증 필요.

_HWPTAG_PARA_TEXT = 0x43


def _extract_hwp_text(data: bytes) -> str:
    try:
        import olefile
    except ImportError as err:  # pragma: no cover
        raise AttachmentError("olefile 패키지가 필요합니다 (pip install olefile)") from err

    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
    except OSError as err:
        raise AttachmentError(f"HWP(OLE) 파싱 실패: {err}") from err

    try:
        compressed = _hwp_is_compressed(ole)
        section_paths = _hwp_body_section_paths(ole)
        if not section_paths:
            raise AttachmentError("HWP 안에 BodyText 섹션이 없습니다")

        paragraphs: list[str] = []
        for path in section_paths:
            raw = ole.openstream(path).read()
            payload = _inflate_raw(raw) if compressed else raw
            paragraphs.extend(_hwp_section_paragraphs(payload))
        return "\n".join(paragraphs).strip()
    finally:
        ole.close()


def _hwp_is_compressed(ole) -> bool:
    if not ole.exists("FileHeader"):
        return True  # 정보가 없으면 실측상 대부분인 압축 문서로 가정한다
    header = ole.openstream("FileHeader").read()
    if len(header) < 40:
        return True
    flags = int.from_bytes(header[36:40], "little")
    return bool(flags & 0x1)


def _hwp_body_section_paths(ole) -> list[str]:
    sections = [
        entry
        for entry in ole.listdir(streams=True)
        if len(entry) == 2 and entry[0] == "BodyText" and entry[1].startswith("Section")
    ]

    def _index(entry: list[str]) -> int:
        try:
            return int(entry[1].replace("Section", ""))
        except ValueError:
            return 0

    sections.sort(key=_index)
    return ["/".join(entry) for entry in sections]


def _inflate_raw(data: bytes) -> bytes:
    try:
        return zlib.decompress(data, -15)
    except zlib.error as err:
        raise AttachmentError(f"HWP 섹션 압축 해제 실패: {err}") from err


def _hwp_section_paragraphs(payload: bytes) -> list[str]:
    paragraphs = []
    offset = 0
    length = len(payload)
    while offset + 4 <= length:
        header = int.from_bytes(payload[offset : offset + 4], "little")
        tag_id = header & 0x3FF
        size = (header >> 20) & 0xFFF
        offset += 4
        if size == 0xFFF:
            if offset + 4 > length:
                break
            size = int.from_bytes(payload[offset : offset + 4], "little")
            offset += 4
        record = payload[offset : offset + size]
        offset += size
        if tag_id == _HWPTAG_PARA_TEXT and record:
            text = _decode_para_text(record)
            if text:
                paragraphs.append(text)
    return paragraphs


def _decode_para_text(record: bytes) -> str:
    raw = record.decode("utf-16le", errors="ignore")
    # 표/그림 등 인라인 컨트롤 문자(0x00~0x1F, 개행 제외)는 걷어낸다.
    return "".join(ch for ch in raw if ch == "\n" or ord(ch) >= 0x20)
