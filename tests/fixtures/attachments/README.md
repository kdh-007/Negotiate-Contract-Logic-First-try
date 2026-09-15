# 첨부파일 샘플 (회귀 테스트용)

실제 나라장터 공개 입찰공고 첨부파일 5건이다. 공개공고이므로 저장소에 포함한다.
`tests/test_attachment_samples.py`가 이 파일들로 `nego/attachments.py`의
텍스트 추출 로직을 실제 문서 기준으로 검증한다.

| 파일 | 원 문서 | 형식 |
|---|---|---|
| `jeongseon_culture_center_task_order.hwpx` | 정선군 복합문화센터 공간디자인 및 전시물 제작설치 — 과업지시서 | HWPX |
| `jeongseon_culture_center_rfp.hwpx` | 정선군 복합문화센터 공간디자인 및 전시물 제작·설치 — 제안요청서 (평가기준 배점표 포함) | HWPX |
| `jeongseon_culture_center_notice.hwpx` | 정선군 공고 제2026-1035호 (협상에 의한 계약) | HWPX |
| `sejong_labor_relations_bid_explanation.pdf` | 중앙노동위원회 본관 인테리어공사 — 공사입찰설명서(지역제한: 세종특별자치시) | PDF |
| `sejong_labor_relations_bid_explanation.hwpx` | 위와 같은 공고의 입찰설명서 (HWPX본) | HWPX |

## 알려진 한계

`jeongseon_culture_center_rfp.hwpx`의 평가기준 배점표는 텍스트 자체는 정확히
추출되지만, 표의 행/열 구분 없이 한 줄로 이어붙는다 (문단 단위 추출의 한계).
표 구조를 살린 파싱은 다음 단계 작업이다.

`.hwp`(구버전 바이너리) 형식 샘플은 아직 없다 — `_extract_hwp_text`는 레코드
파싱 로직만 단위 테스트(`tests/test_attachments.py`)했고, 실제 파일로는
검증되지 않았다.
