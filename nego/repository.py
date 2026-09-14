"""저장소.

저장 방식은 추후 선정하기로 했으므로 인터페이스를 먼저 고정한다.
1단계 구현체는 로컬 JSONL이고, 나중에 Postgres/Supabase 등으로 교체할 때
`NoticeRepository`만 새로 구현하면 파이프라인 코드는 그대로 둔다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Protocol

from .models import Notice, notice_from_raw


class NoticeRepository(Protocol):
    def upsert(self, notices: Iterable[Notice]) -> int: ...
    def all(self) -> list[Notice]: ...
    def known_keys(self) -> set[tuple[str, str, str]]: ...


class JsonlRepository:
    """공고 1건 = 1줄. 같은 키(업무구분+공고번호+차수)는 최신 것으로 덮어쓴다.

    원문(raw)을 같이 보관한다. 나중에 첨부 파싱·배점표 추출 단계를 붙일 때
    재수집 없이 이 파일만 다시 읽으면 되기 때문이다.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load_map(self) -> dict[tuple[str, str, str], dict]:
        out: dict[tuple[str, str, str], dict] = {}
        if not self.path.exists():
            return out
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = (row.get("work_type", ""), row.get("notice_no", ""), row.get("notice_ord", ""))
                out[key] = row
        return out

    def upsert(self, notices: Iterable[Notice]) -> int:
        existing = self._load_map()
        added = 0
        for notice in notices:
            if notice.key not in existing:
                added += 1
            existing[notice.key] = notice.to_dict()

        with self.path.open("w", encoding="utf-8") as handle:
            for row in existing.values():
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return added

    def all(self) -> list[Notice]:
        out: list[Notice] = []
        for row in self._load_map().values():
            raw = row.get("raw") or {}
            notice = notice_from_raw(raw, row.get("work_type", ""))
            # raw가 비어 있는 행(테스트 픽스처 등)은 저장된 값을 그대로 복원한다.
            if not raw:
                notice.notice_no = row.get("notice_no", "")
                notice.notice_ord = row.get("notice_ord", "000")
                notice.title = row.get("title", "")
            out.append(notice)
        return out

    def known_keys(self) -> set[tuple[str, str, str]]:
        return set(self._load_map().keys())
