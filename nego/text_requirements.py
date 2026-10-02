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
# "적격심사 평가대상 업종 및 평가비율" 표는 자격요건이 아니다(2026-10-01 고이분교: "종합 건축공사업 … 100%")
_NOT_REQUIREMENT = re.compile(r"공동수급|공동도급|공동이행|분담이행|대표사|구성원|실적|제재|부정당|하도급|평가\s*대상\s*업종|평가\s*비율")
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
    """정규화 이름 → (표시 이름, 코드). 업종(4자리)만. 보유 목록이 사전보다 우선.

    같은 이름이 여러 코드에 붙어 있으면(코드 사전의 "소프트웨어사업자" = 1426·1468·1469·1470, 분야별 등록) 코드를
    하나로 정할 수 없어 None으로 둔다 — 아무 코드나 붙이면 엉뚱한 분야를 요구하는 것처럼 보인다."""
    codes: dict[str, set[str]] = {}
    names: dict[str, str] = {}
    for source in (lookup, held_code_names):
        for code, name in source.items():
            if len(code) == 4 and name:
                codes.setdefault(_norm(name), set()).add(code)
                names[_norm(name)] = name
    # 보유 목록에 그 이름이 있으면 보유 코드로 ("실내건축공사업" = 사전 0006 · 등록증 4990 — 등록증 쪽)
    held_code = {_norm(name): code for code, name in held_code_names.items() if len(code) == 4 and name}
    return {k: (names[k], held_code.get(k) or (next(iter(c)) if len(c) == 1 else None)) for k, c in codes.items()}


def _is_held_name(name: str, held_names: set[str]) -> bool:
    """보유 이름과 완전히 같거나, 분야 없이 적은 통칭("소프트웨어사업자")이고 그 분야 중 하나를 보유
    ("소프트웨어사업자(컴퓨터관련서비스사업)")하면 보유."""
    key = _norm(name)
    return key in held_names or any(h.startswith(key + "(") for h in held_names)


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


def sub_items(item: str) -> list[str]:
    """하위 번호 1)·2)·①로 나눈 조각들 (하위 번호가 없으면 항목 그대로 1개)."""
    return [p for p in _SUBITEM_RE.split(item) if p.strip()]


def name_bundles(
    item: str, held_code_names: dict[str, str], lookup: dict[str, str]
) -> list[list[NameReq]]:
    """코드 없는 항목에서 업종·직접생산 품목을 이름으로 찾아 [묶음(OR)] 목록(묶음끼리 AND)으로 돌려준다.

    한 항목 안에 "다음 각 조건을 모두 갖춘 업체 1) … 2) …"처럼 하위 번호가 있으면 하위 항목마다 따로
    본다(하위 항목끼리는 모두 필요). 찾은 게 없으면 빈 목록 — 판정하지 않는다.
    """
    parts = sub_items(item)
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
            found.append(NameReq(f"{name}({code})" if code else name, _is_held_name(name, held_names), code))
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


# 줄머리 표시(나./1)/①/-/※/❍ …)로 시작하는 줄은 새 문장이다
_LINE_HEAD = re.compile(r"\n\s*(?=[-※*·•□■○●◎❍◦▪▶►【]|\d{1,2}\s*[).]|[①-⑳]|[가-하]\s*[.)])")
_MAX_JOINED = 200
_FORM_RE = re.compile(r"서식\s*(?:제\s*)?\d")  # 이어 붙인 문장이 이보다 길면(공백 제외) 표·서식이 섞인 것 — 줄 단위로 되돌린다


def _sentences(item: str) -> list[str]:
    """항목을 문장으로 나눈다. PDF는 문장 중간에서 줄을 바꿔 "…단일공사 5억원 이상\n준공실적을 보유한 업체"가
    두 줄로 갈라진다(2026-10-01 제보: 영주 과수거점산지유통센터 — 실적 칩 누락). 줄바꿈은 줄머리 표시가 있을 때만
    문장 경계로 보고, 나머지는 이어 붙인 뒤 마침표로 나눈다. 너무 길어지면(표·서식 칸이 이어진 것) 줄 단위로 본다."""
    out = []
    for block in _LINE_HEAD.split(item):
        for sent in re.split(r"(?<=[.。])\s+(?=\S)", block):
            if len(re.sub(r"\s+", "", sent)) > _MAX_JOINED:
                out.extend(line for line in sent.split("\n") if line.strip())
            elif sent.strip():
                out.append(re.sub(r"\s*\n\s*", " ", sent))
    return out


# 실적 칩에서 뺄 문장 — "시공실적으로 입찰참가자격을 제한하지 아니한 입찰"(2026-10-01 제보: 부산 50+복합지원센터 인테리어)처럼
# 실적 제한이 없다는 안내, 수행능력평가·시공경험평가·평가대상 업종·평가비율·배점 설명(점수 산정이지 참가 요건이 아님)
_NOT_TRACK_REQUIREMENT = re.compile(
    r"제한\s*하지\s*(?:아니|않)|제한\s*(?:이\s*)?없|수행\s*능력\s*평가|시공\s*경험\s*평가|경영\s*상태\s*평가"
    r"|평가\s*대상\s*업종|평가\s*비율|배\s*점|평\s*점|가\s*점"
)


def flag_requirements(items: list[str], full_text: str = "") -> list[dict]:
    """실적·현장설명회 참가·기술인력 요건을 찾아 [{kind, text, date?}]로. 판정은 하지 않는다."""
    out: dict[str, dict] = {}
    sentences = [s for item in items for s in _sentences(item)]
    # 현장설명회는 참가자격 절 밖("입찰 일정")에 "참석 필수"로만 적히기도 해서 원문 전체도 본다
    extra = [m.group(0) for m in re.finditer(r"[^\n]{0,60}현장\s*설명회[^\n]{0,100}", full_text or "")]
    for kind, subject, detail in _FLAGS:
        pool = sentences + (extra if kind == "현장설명회" else [])
        for s in pool:
            if _FORM_RE.search(s):
                continue  # 제출 서식(참여인력 경력사항 표 등)의 칸 이름은 요건이 아니다
            if kind == "인력" and "건축사사무소" in out:
                break  # 건축사사무소 요건 문장의 "건축사 면허"를 인력 요건으로 또 세지 않는다
            if kind == "건축사사무소" and re.search(r"업종\s*코드|\[\d{4}\]|\(\d{4}\)", s):
                continue  # 코드 있는 업종과 "또는"으로 나열된 대안일 뿐 — 코드 판정이 이미 본다
            if kind == "실적" and _NOT_TRACK_REQUIREMENT.search(s):
                continue  # 실적으로 참가를 "제한하지 않는다"·적격심사 평가(배점) 설명은 참가 요건이 아니다
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


_ZIP_PART_RE = re.compile(r"^=== \[압축 안\] (.+?) ===$", re.M)


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text)


def attach_sources(flags: list[dict], files: list[tuple[str, str]]) -> list[dict]:
    """칩 원문 문장이 어느 첨부파일에서 나왔는지 `source`(파일명)를 붙인다 (2026-10-02 사용자 요청 — 팝업 하단 출처).

    PDF·HWP는 문장 중간에서 줄을 바꾸므로 공백을 다 지우고 원문 앞 40자로 찾는다. 압축 첨부는 "=== [압축 안] 이름 ==="
    구분으로 나눠 "압축파일 › 안쪽 파일"로 적는다. 못 찾으면 source를 넣지 않는다(화면은 출처 줄을 안 그림)."""
    for flag in flags:
        needle = _squeeze(flag.get("text", ""))[:40]
        if not needle:
            continue
        for file_name, text in files:
            pieces = _ZIP_PART_RE.split(text)
            # split 결과: [압축 밖 앞부분, 이름1, 본문1, 이름2, 본문2, …]
            parts = [(file_name, pieces[0])] + [
                (f"{file_name} › {pieces[i]}", pieces[i + 1]) for i in range(1, len(pieces) - 1, 2)
            ]
            hit = next((label for label, body in parts if needle in _squeeze(body)), None)
            if hit:
                flag["source"] = hit
                break
    return flags
