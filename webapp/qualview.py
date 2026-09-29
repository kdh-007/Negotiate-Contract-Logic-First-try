"""카드의 자격 배지·팝업용 자료 — 자격요건(업종 4자리) / 세부품명번호(10자리) 두 부문.

판정 자체(`QualificationResult`)는 건드리지 않고 **보여주는 방식만** 정한다.

- 미보유 쪽은 **요건 단위**로 센다: "A 또는 B"면 둘 중 하나만 있으면 되는 요건 1건.
- 충족 쪽은 **보유해서 요건을 채운 자격** 단위로 센다 — 같은 자격이 여러 요건을 채우면
  (예: 1469가 "1469 또는 4442", "1469 또는 4444" 두 요건을 모두 채움) 한 번만 나온다.
- 세부품명번호 부문은 요건 안의 코드가 **전부 10자리**일 때만. 업종·품목이 섞인
  "다음 중 하나" 요건은 자격요건 부문에 두고, 항목마다 [업종]/[품명] 표시를 붙인다.
- 요건이 없으면 "제한 없음"(정보를 봤는데 없음) / "미확인"(볼 정보가 없었음)으로 구분한다.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from nego.report import _dedupe_names_preferring_code, _display_name

_TRAILING_CODE = re.compile(r"\((\d{4}|\d{10})\)\s*$")
_NORM = re.compile(r"[\s·.,/()\-]")

SECTIONS = (("industry", "자격요건"), ("product", "세부품명번호"))


def _code(label: str) -> str | None:
    m = _TRAILING_CODE.search(label)
    return m.group(1) if m else None


def _base(label: str) -> str:
    return _NORM.sub("", _TRAILING_CODE.sub("", label))


def held_lookup(held_raw: dict) -> tuple[set[str], set[str]]:
    """보유 자격 → (코드 집합, 정규화한 이름 집합)."""
    codes, names = set(), set()
    for key in ("heldProducts", "heldIndustries"):
        for entry in held_raw.get(key, []) or []:
            if entry.get("code"):
                codes.add(str(entry["code"]).strip())
            if entry.get("name"):
                names.add(_NORM.sub("", str(entry["name"])))
    return codes, names


def _items(group, held: tuple[set[str], set[str]]) -> list[dict[str, Any]]:
    labels = _dedupe_names_preferring_code(list(dict.fromkeys(_display_name(n) for n in group.allowed_names)))
    codes, names = held
    out = []
    for label in labels:
        code = _code(label)
        is_held = (code in codes) if code else (_base(label) in names)
        out.append({"label": label, "code": code, "kind": "product" if code and len(code) == 10 else "industry",
                    "held": is_held})
    return out


def _section_of(items: list[dict[str, Any]]) -> str:
    coded = [i for i in items if i["code"]]
    return "product" if coded and len(coded) == len(items) and all(i["kind"] == "product" for i in items) else "industry"


def build(qualification, held: tuple[set[str], set[str]], had_source: dict[str, bool]) -> list[dict[str, Any]]:
    """부문 2개를 항상 돌려준다. `had_source[key]`: 그 부문 요건을 찾아볼 정보가 있었는지."""
    groups: dict[str, dict[str, list]] = {k: {"missing": [], "satisfied": []} for k, _ in SECTIONS}
    seen: set[tuple] = set()
    for state, source in (("missing", qualification.missing_groups), ("satisfied", qualification.satisfied_groups)):
        for g in source:
            items = _items(g, held)
            sig = tuple(sorted(i["label"] for i in items))
            if not items or sig in seen:
                continue
            seen.add(sig)
            groups[_section_of(items)][state].append(items)

    parts = []
    for key, name in SECTIONS:
        missing = groups[key]["missing"]
        satisfied = groups[key]["satisfied"]
        # 충족: 요건을 채운 보유 자격(중복 제거). 보유 표시를 못 붙인 요건은(이름 표기 차이) 요건 자체를 적는다.
        held_labels: list[str] = []
        for items in satisfied:
            mine = [i["label"] for i in items if i["held"]]
            held_labels.extend(mine or [" 또는 ".join(i["label"] for i in items)])
        held_labels = list(dict.fromkeys(held_labels))
        mixed = any(len({i["kind"] for i in items if i["code"]}) > 1 for items in missing)
        if missing:
            status = "미달"
        elif satisfied:
            status = "충족"
        else:
            status = "제한 없음" if had_source.get(key) else "미확인"
        parts.append({
            "key": key, "name": name, "status": status,
            "missing": [{"any_of": len(items) > 1, "items": items} for items in missing],
            "held": held_labels,
            "mixed": mixed,
        })
    return parts


def flat_missing(parts: Iterable[dict[str, Any]]) -> list[str]:
    return [" 또는 ".join(i["label"] for i in req["items"]) for p in parts for req in p["missing"]]
