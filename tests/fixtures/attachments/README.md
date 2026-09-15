# 첨부파일 샘플 (회귀 테스트용)

실제 나라장터 공개 입찰공고 첨부파일 5건이다. 공개공고이므로 저장소에 포함한다.
`tests/test_attachment_samples.py`가 이 파일들로 `nego/attachments.py`의
텍스트 추출 로직을 실제 문서 기준으로 검증한다.

**담당자 연락처(전화번호/이메일)는 파일 자체에서 마스킹했다.** 최초 커밋 때는
원문 그대로였는데, 문서 안에 발주기관 담당자 실명+직통번호가 그대로 들어있는
걸 뒤늦게 발견해서 HWPX는 `Contents/section*.xml`의 해당 텍스트 노드를, PDF는
페이지 콘텐츠 스트림을 직접 편집해 지운 뒤 다시 커밋했다. 이름 자체는 남겨뒀다.

(이후 방침 변경으로 첨부파일 텍스트 추출 시 자동 마스킹 기능
`redact_personal_contacts()`는 제거했다 — 공개공고에 실린 발주기관 문의처는
가려야 할 개인정보로 보지 않기로 했다. 이 픽스처 파일들은 과거에 이미
편집돼 있어 그대로 둔다.)

| 파일 | 원 문서 | 형식 |
|---|---|---|
| `jeongseon_culture_center_task_order.hwpx` | 정선군 복합문화센터 공간디자인 및 전시물 제작설치 — 과업지시서 | HWPX |
| `jeongseon_culture_center_rfp.hwpx` | 정선군 복합문화센터 공간디자인 및 전시물 제작·설치 — 제안요청서 (평가기준 배점표 포함) | HWPX |
| `jeongseon_culture_center_notice.hwpx` | 정선군 공고 제2026-1035호 (협상에 의한 계약) | HWPX |
| `sejong_labor_relations_bid_explanation.pdf` | 중앙노동위원회 본관 인테리어공사 — 공사입찰설명서(지역제한: 세종특별자치시) | PDF |
| `sejong_labor_relations_bid_explanation.hwpx` | 위와 같은 공고의 입찰설명서 (HWPX본) | HWPX |

## 알려진 한계

`.hwp`(구버전 바이너리) 형식 샘플은 아직 없다 — `_extract_hwp_text`는 레코드
파싱 로직만 단위 테스트(`tests/test_attachments.py`)했고, 실제 파일로는
검증되지 않았다.

(과거 한계였던 "HWPX 표가 행/열 구분 없이 한 줄로 이어붙는 문제"는
`_render_table`로 표 구조(행=줄바꿈, 셀=` | `)를 살리도록 고쳐서 해결했다.
`jeongseon_culture_center_rfp.hwpx`의 평가기준 배점표로 검증함.)
