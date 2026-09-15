# 첨부파일 샘플 (회귀 테스트용)

실제 나라장터 공개 입찰공고 첨부파일 9건이다. 공개공고이므로 저장소에 포함한다.
`tests/test_attachment_samples.py`, `tests/test_qualification_text.py`가 이
파일들로 `nego/attachments.py`/`nego/qualification_text.py`의 텍스트 추출·
참가자격 절 인식 로직을 실제 문서 기준으로 검증한다.

**담당자 연락처(전화번호/이메일)를 지우지 않고 원문 그대로 둔다.** 초기 5건은
최초 커밋 때 원문 그대로였다가, 문서 안에 발주기관 담당자 실명+직통번호가
그대로 들어있는 걸 뒤늦게 발견해서 한 차례 편집해 지운 적이 있다. 이후 방침을
바꿔 첨부파일 텍스트 추출 시 자동 마스킹 기능(`redact_personal_contacts()`)을
아예 제거했다 — 공개공고에 실린 발주기관 문의처는 가려야 할 개인정보로 보지
않기로 했기 때문이다. 그래서 나중에 추가한 4건(안성시/나로우주센터/광주/
양산시)은 원문을 그대로 두었고, 연락처가 남아있어도 정상이다.

| 파일 | 원 문서 | 형식 |
|---|---|---|
| `jeongseon_culture_center_task_order.hwpx` | 정선군 복합문화센터 공간디자인 및 전시물 제작설치 — 과업지시서 | HWPX |
| `jeongseon_culture_center_rfp.hwpx` | 정선군 복합문화센터 공간디자인 및 전시물 제작·설치 — 제안요청서 (평가기준 배점표 포함) | HWPX |
| `jeongseon_culture_center_notice.hwpx` | 정선군 공고 제2026-1035호 (협상에 의한 계약) | HWPX |
| `sejong_labor_relations_bid_explanation.pdf` | 중앙노동위원회 본관 인테리어공사 — 공사입찰설명서(지역제한: 세종특별자치시) | PDF |
| `sejong_labor_relations_bid_explanation.hwpx` | 위와 같은 공고의 입찰설명서 (HWPX본) | HWPX |
| `anseong_gosam_lake_park_notice.hwpx` | 안성시 공고 제2026-2469호 고삼호수 문화공원 관광안내소 인테리어 설계 및 설치 (협상에 의한 계약) — 가~바 6개 항목 | HWPX |
| `naro_space_center_bid_notice.pdf` | 나로우주센터 우주과학관 우주탐사 전시 개선 — 입찰공고문, ①~④ **원문자 번호** 4개 항목 | PDF |
| `gwangju_digital_experience_center_notice.pdf` | 광주 전자 디지털 체험관 전시 체험 콘텐츠 구축 — 제안서 제출안내 공고(협상에 의한 계약) | PDF |
| `yangsan_jujin_park_notice.hwpx` | 양산시 공고 제2026-2538호 주진불빛공원 모험놀이터 놀이시설 디자인 및 제작, 설치 (협상에 의한 계약) | HWPX |

## 알려진 한계

`.hwp`(구버전 바이너리) 형식 샘플은 아직 없다 — `_extract_hwp_text`는 레코드
파싱 로직만 단위 테스트(`tests/test_attachments.py`)했고, 실제 파일로는
검증되지 않았다.

(과거 한계였던 "HWPX 표가 행/열 구분 없이 한 줄로 이어붙는 문제"는
`_render_table`로 표 구조(행=줄바꿈, 셀=` | `)를 살리도록 고쳐서 해결했다.
`jeongseon_culture_center_rfp.hwpx`의 평가기준 배점표로 검증함.)
