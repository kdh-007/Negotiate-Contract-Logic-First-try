"""첨부 참가자격 절에서 **코드 없이 글로만 적힌** 요건을 읽는다 (2026-09-30 사용자 요청).

과거 제안요청서·공고문 144건(참가자격 절을 찾은 것)을 조사해 보니 코드 없는 요건이 많았다:

- 분야 이름만 적은 업종 32건 — "산업디자인 전문회사(시각디자인, 제품디자인, 종합디자인 분야 중 하나 이상)"
- 품명만 적은 직접생산증명 29건 — "직접생산증명서(실물모형)", "〈물품분류번호 : 60121002〉"(8자리)
- 실적 요건 22건, 현장설명회 참가 필수 6건, 기술인력 보유 1건

앞의 둘은 지일 보유 목록과 **이름으로 대조해 판정**하고(`name_bundles`), 뒤의 셋은 판정하지 않고
**"확인 필요" 칩**으로만 보여준다(`flag_requirements`). 중소기업·소상공인 확인서는 지일이 보유해
무시한다(사용자 확인).

판정은 **아는 이름만** 한다 — 보유 목록·코드 이름 사전(codes.json, code_names.json)에 없는 업종명은
건드리지 않는다. 모르는 이름을 추측해서 "미달"로 만들면 오탈락이 되기 때문이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 한 항목 안 이름들을 "또는"으로 볼 신호. 없으면 이름마다 모두 필요.
_OR_HINT = re.compile(r"또는|혹은|중\s*(?:하나|1\s*개|한\s*가지)|어느\s*하나|이상\s*(?:을|를)?\s*(?:보유|등록|신고|소지)")
# 요건이 아닌 항목 — 공동수급 방법·실적·결격사유 설명 속 업종명은 판정하지 않는다
_NOT_REQUIREMENT = re.compile(r"공동수급|공동도급|공동이행|분담이행|대표사|구성원|실적|제재|부정당|하도급")
_DESIGN_FIELDS = ("시각", "제품", "포장", "환경", "멀티미디어", "서비스", "종합")
_DESIGN_RE = re.compile(r"산업\s*디자인\s*전문\s*회사\s*[(\[]([^)\]]{1,80})[)\]]")
_DIRECT_RE = re.compile(r"직접\s*생산\s*(?:확인)?\s*증명(?:서)?")
_CLASS8_RE = re.compile(r"(?:물품\s*(?:분류)?\s*번호|분류\s*번호)\s*[:：]?\s*(\d{8})(?!\d)")


def _norm(text: str) -> str:
    return re.sub(r"[\s·ㆍ.]", "", text)


@dataclass(frozen=True)
class NameReq:
    label: str
    held: bool
    code: str | None = None


def _industry_dictionary(held_code_names: dict[str, str], lookup: dict[str, str]) -> dict[str, tuple[str, str | None]]:
    """정규화 이름 → (표시 이름, 코드). 업종(4자리)만. 보유 목록이 사전보다 우선."""
    out: dict[str, tuple[str, str | None]] = {}
    for source in (lookup, held_code_names):
        for code, name in source.items():
            if len(code) == 4 and name:
                out[_norm(name)] = (name, code)
    return out


def _design_names(item: str) -> list[str]:
    """"산업디자인 전문회사(시각디자인, 종합디자인 분야 중 하나 이상)" → 분야별 정식 이름들."""
    names = []
    for m in _DESIGN_RE.finditer(item):
        inside = m.group(1)
        for field in _DESIGN_FIELDS:
            if re.search(field + r"\s*(?:디자인)?", inside) and (field != "서비스" or "서비스디자인" in _norm(inside)):
                names.append(f"산업디자인전문회사({field}디자인분야)")
    return list(dict.fromkeys(names))


_SUBITEM_RE = re.compile(r"(?:(?<=\s)|^)(?=(?:\d{1,2}\)|[①-⑳])\s*)")


def name_bundles(
    item: str, held_code_names: dict[str, str], lookup: dict[str, str]
) -> list[list[NameReq]]:
    """코드 없는 항목에서 업종·직접생산 품목을 이름으로 찾아 [묶음(OR)] 목록(묶음끼리 AND)으로 돌려준다.

    한 항목 안에 "다음 각 조건을 모두 갖춘 업체 1) … 2) …"처럼 하위 번호가 있으면 하위 항목마다 따로
    본다(하위 항목끼리는 모두 필요). 찾은 게 없으면 빈 목록 — 판정하지 않는다.
    """
    parts = [p for p in _SUBITEM_RE.split(item) if p.strip()]
    if len(parts) > 1:
        return [b for p in parts for b in name_bundles(p, held_code_names, lookup)]
    if _NOT_REQUIREMENT.search(item):
        return []
    held_names = {_norm(n) for n in held_code_names.values()}
    held_products = {code: name for code, name in held_code_names.items() if len(code) == 10}
    found: list[NameReq] = []

    # ① 산업디자인전문회사 분야
    for name in _design_names(item):
        found.append(NameReq(name, _norm(name) in held_names))

    # ② 사전에 있는 업종명 (긴 이름부터 — "실내건축공사업" 안의 "건축공사업"을 따로 세지 않게)
    text = _norm(_DESIGN_RE.sub("", item))
    taken: list[tuple[int, int]] = []
    for key, (name, code) in sorted(_industry_dictionary(held_code_names, lookup).items(), key=lambda kv: -len(kv[0])):
        if len(key) < 4 or key.startswith("산업디자인전문회사"):
            continue
        for m in re.finditer(re.escape(key), text):
            if any(s < m.end() and m.start() < e for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            found.append(NameReq(f"{name}({code})" if code else name, _norm(name) in held_names, code))
            break

    # ③ 직접생산증명 품목 — 괄호 속 품명, 또는 8자리 물품분류번호(보유 10자리 세부품명번호의 앞자리)
    if _DIRECT_RE.search(item):
        for m in _CLASS8_RE.finditer(item):
            cls = m.group(1)
            held = any(code.startswith(cls) for code in held_products)
            label = next((name for code, name in held_products.items() if code.startswith(cls)), None)
            found.append(NameReq(f"{label or '물품분류'}({cls})", held, cls))
        if not _CLASS8_RE.search(item):
            for m in re.finditer(r"증명(?:서)?\s*[\[(<〔]+\s*(?:세부\s*품명\s*[:：]?\s*)?([가-힣A-Za-z]{2,20})", item):
                pname = m.group(1)
                if "번호" in pname or pname in ("세부품명", "물품", "품명"):
                    continue  # "(물품분류번호 …)" 같은 머리말은 품명이 아니다
                hit = next((name for name in held_products.values() if _norm(name).startswith(_norm(pname))), None)
                found.append(NameReq(pname, hit is not None))

    if not found:
        return []
    found = list({(r.label): r for r in found}.values())
    if len(found) > 1 and _OR_HINT.search(item):
        return [found]
    return [[r] for r in found]


# ── 판정하지 않고 칩으로만 보여주는 요건 ─────────────────────────────
_FLAGS = (
    ("실적", re.compile(r"실적|준공한|수행한\s*경험"), re.compile(r"\d\s*(?:억|천만|백만|만)?\s*원|\d+\s*건|최근\s*\d+\s*년")),
    # 참가 안 하면 입찰·평가에서 빠지는 경우만 — "참석하지 않은 업체의 질의는 받지 않음"은 필수가 아니다
    ("현장설명회", re.compile(r"현장\s*설명회"),
     re.compile(r"불참자|참가하지\s*않은\s*(?:업체|자)는|참석하지\s*않은\s*(?:업체|자)는|참가한\s*(?:업체|자)|참석한\s*(?:업체|자)|참석\s*필수|참가\s*필수|의무")),
    ("건축사사무소", re.compile(r"건축사\s*사무소"), re.compile(r"개설|등록|신고")),
    ("인력", re.compile(r"기술자|기술사|학예사|건축사|전문\s*인력|기사\s*자격"), re.compile(r"보유|소지|소속|재직|자격증")),
)
# "현장설명회 없음/생략"(설명회 자체가 없음), "참석 안 하면 질의 응답 안 함"(필수 아님) — "불참자는 참여할 수 없음"은 필수
_NOT_MANDATORY = re.compile(r"설명회[^.]{0,15}(?:없|미실시|생략|갈음|개최하지)|질의|응답")
_DATE_RE = re.compile(r"(20\d{2})\s*[.년-]\s*(\d{1,2})\s*[.월-]\s*(\d{1,2})\s*일?\.?\s*(?:\([월화수목금토일]\))?\s*(\d{1,2}\s*:\s*\d{2})?")


def flag_requirements(items: list[str], full_text: str = "") -> list[dict]:
    """실적·현장설명회 참가·기술인력 요건을 찾아 [{kind, text, date?}]로. 판정은 하지 않는다."""
    out: dict[str, dict] = {}
    sentences = [s for item in items for s in re.split(r"(?<=[.。])\s+|\n", item) if s.strip()]
    # 현장설명회는 참가자격 절 밖("입찰 일정")에 "참석 필수"로만 적히기도 해서 원문 전체도 본다
    extra = [m.group(0) for m in re.finditer(r"[^\n]{0,60}현장\s*설명회[^\n]{0,100}", full_text or "")]
    for kind, subject, detail in _FLAGS:
        pool = sentences + (extra if kind == "현장설명회" else [])
        for s in pool:
            if kind == "인력" and "건축사사무소" in out:
                break  # 건축사사무소 요건 문장의 "건축사 면허"를 인력 요건으로 또 세지 않는다
            if kind == "건축사사무소" and re.search(r"업종\s*코드|\[\d{4}\]|\(\d{4}\)", s):
                continue  # 코드 있는 업종과 "또는"으로 나열된 대안일 뿐 — 코드 판정이 이미 본다
            if subject.search(s) and detail.search(s) and not (kind == "현장설명회" and _NOT_MANDATORY.search(s)):
                entry = {"kind": kind, "text": re.sub(r"\s+", " ", s).strip()[:220]}
                if kind == "현장설명회":
                    near = [m.group(0) for m in re.finditer(r"[^\n]{0,20}현장\s*설명회[^\n]{0,120}", full_text or "")]
                    d = next((_DATE_RE.search(n) for n in near if _DATE_RE.search(n)), None)
                    if d:
                        entry["date"] = f"{d.group(1)}-{int(d.group(2)):02d}-{int(d.group(3)):02d}" + (
                            f" {d.group(4).replace(' ', '')}" if d.group(4) else "")
                out.setdefault(kind, entry)
                break
    return list(out.values())
