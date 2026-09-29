# 입찰 공고 싱크로율 탐색기 (사내 웹앱)

회사 PC 한 대에서 띄우면 팀원이 사내망 브라우저로 같이 쓰는 페이지.

- **나라장터에서 불러오기**: 누르는 순간 나라장터 API로 수집한다. `python -m nego --fetch-attachment-text`와
  같은 코드(`nego.pipeline.run` → `attachments.save_attachment_texts`)를 그대로 부른다. 수집 범위(수의계약 제외),
  키워드·예산 필터, 자격 충족/미달(API 면허제한 + 첨부 참가자격 "또는/모두" 규칙)이 CLI와 같다.
- **싱크로율**: 공고의 과업 프로필과 과거 실적 150건(jiil-past-contracts)을 비교해 가장 비슷한 사업과의 점수.
  높음 65% 이상 · 경계선 45~65% · 낮음 45% 미만. 첨부 원문이 없으면 공고명만으로 계산하고 "근거: 공고명만"으로 표시.
- **AI 판단**(선택): 싱크로율 상위 과거 실적 8건과 Claude API로 적합/검토필요/부적합 판정. `ANTHROPIC_API_KEY` 필요.
- **참가여부·담당·대화**: 서버 PC의 `data/webapp.sqlite3`에 저장되어 팀원 모두 같은 내용을 본다.
  재수집해도 공고번호-차수가 같으면 그대로 남는다.

화면의 "사전규격" 칩은 비활성이다 — 지금 수집 로직은 본공고만 가져온다.

## 실행 (Windows PowerShell 기준)

두 레포를 **같은 폴더에 나란히** 클론한다 (jiil-past-contracts는 비공개 — 과거 실적이 여기서만 읽힌다).

```powershell
cd C:\work
git clone https://github.com/kdh-007/Negotiate-Contract-Logic-First-try.git
git clone https://github.com/kdh-007/jiil-past-contracts.git
cd Negotiate-Contract-Logic-First-try
py -m pip install -r requirements.txt

$env:NARA_SERVICE_KEY = "공공데이터포털 서비스키(Decoding)"
# $env:ANTHROPIC_API_KEY = "..."    # AI 판단을 쓸 때만
py -m webapp                         # 이 PC에서만: http://localhost:8765/
```

팀원과 같이 쓰기:

```powershell
$env:HOST = "0.0.0.0"
py -m webapp
#  → 입찰 공고 싱크로율 탐색기: http://0.0.0.0:8765/?t=XXXXXXXX
```

- 출력된 주소의 `0.0.0.0`을 이 PC의 사내 IP(`ipconfig`의 IPv4)로 바꿔 팀원에게 공유한다.
  `?t=` 토큰이 없으면 API가 거부된다. 토큰은 서버를 다시 켤 때마다 바뀌므로, 고정하려면 `$env:APP_TOKEN = "원하는값"`.
- 처음 실행 때 Windows 방화벽 창이 뜨면 "개인 네트워크" 허용. 포트를 바꾸려면 `$env:PORT = "8765"`.
- jiil 레포가 다른 곳에 있으면 `$env:JIIL_REPO = "D:\...\jiil-past-contracts"`.
- 서비스키는 서버 프로세스에만 있고 브라우저로는 내려가지 않는다.

## 참고

- 수집은 한 번에 하나만 돈다(누가 돌리는 중이면 버튼이 잠김). 진행 로그가 버튼 아래에 뜬다.
- "첨부 자격판정"을 켜면 후보마다 첨부파일을 내려받아 시간이 걸린다(후보 수 × 첨부 수). 추출한 원문은
  `output/attachment_text/`에 남는다 — CLI와 같은 위치.
- Supabase 저장·텔레그램 발송은 하지 않는다(9/28 결정: 수집 기능 완성 전까지 끔).
- 테스트: `py -m unittest tests.test_webapp`
