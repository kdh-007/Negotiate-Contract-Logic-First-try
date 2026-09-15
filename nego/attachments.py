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
    candidates: list["Candidate"], output_dir: Path, timeout: float = 30.0
) -> dict[str, int]:
    """후보 공고의 첨부파일을 내려받아 텍스트를 `output_dir/attachment_text/`에 저장한다.

    지역제한/면허제한/공동수급을 원문과 대조해볼 수 있도록 평문만 남기는
    용도다 (오늘 범위). 첨부파일 하나가 실패해도 나머지 처리는 계속한다.
    """
    text_dir = output_dir / "attachment_text"
    text_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    stats = {"attempted": 0, "ok": 0, "failed": 0}
    for candidate in candidates:
        notice = candidate.notice
        for result in collect_notice_attachment_texts(session, notice, timeout=timeout):
            stats["attempted"] += 1
            if not result.ok:
                log.warning("첨부파일 추출 실패 [%s] %s: %s", notice.notice_no, result.file_name, result.error)
                stats["failed"] += 1
                continue
            stats["ok"] += 1
            base = _safe_filename(f"{notice.notice_no}_{notice.notice_ord}_{result.seq}_{result.file_name}")
            (text_dir / f"{base}.txt").write_text(result.text, encoding="utf-8")
    return stats


# ── PDF ──────────────────────────────────────────────────────────

def _extract_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except PdfReadError as err:
        raise AttachmentError(f"PDF 파싱 실패: {err}") from err
    return "\n\n".join(pages).strip()


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
    """<hp:p> 문단 단위로 <hp:t> 조각을 이어붙인다. 문단 사이는 줄바꿈으로 구분한다."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as err:
        raise AttachmentError(f"HWPX 섹션 XML 파싱 실패: {err}") from err

    paragraphs = []
    for p in root.iter():
        if not p.tag.endswith("}p"):
            continue
        text = "".join(t.text or "" for t in p.iter() if t.tag.endswith("}t"))
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


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
