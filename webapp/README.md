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

- **사전규격**: "사전규격 포함"을 켜면(기본) 같은 기간의 사전규격도 받아 본공고와 같은 키워드·예산 필터를 태운다.
  조달청 **사전규격정보서비스**는 본공고와 별도 서비스라 공공데이터포털에서 **따로 활용신청**해야 한다(같은 서비스키로 호출됨).
  신청이 안 돼 있으면 사전규격만 실패하고 본공고는 정상으로 나온다 — 화면 요약줄에 "사전규격 조회 실패"가 뜬다.
  사전규격은 낙찰방법·면허제한·공동수급 정보가 없어 자격은 첨부 규격서로만 판정되고, 마감은 "의견등록 마감"이다.

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

## 상시 접속 (PC를 켜면 서버가 자동으로 뜨게)

1. 레포 루트의 `.env.example`을 `.env`로 복사하고 `NARA_SERVICE_KEY`를 채운다
   (`HOST=0.0.0.0`, `APP_TOKEN=jiil2026` 기본값 그대로 두면 주소가 고정된다). `.env`는 깃에 안 올라간다.
2. PowerShell에서 한 번만:
   ```powershell
   cd C:\Users\HJE\Negotiate-Contract-Logic-First-try
   powershell -ExecutionPolicy Bypass -File webapp\install_autostart.ps1
   ```
   작업 스케줄러에 `SyncRateExplorer`가 등록되어 **윈도우 로그온 때마다** 서버가 최소화 창으로 뜬다.
   서버가 죽으면 10초 뒤 자동으로 다시 켜진다(`webapp/start_webapp.bat`). 로그는 `data/webapp.log`.
   해제: `powershell -ExecutionPolicy Bypass -File webapp\install_autostart.ps1 -Remove`
3. PC가 **절전·최대 절전으로 들어가지 않게**(관리자 PowerShell): `powercfg /change standby-timeout-ac 0`,
   `powercfg /change hibernate-timeout-ac 0`. 꺼져 있거나 잠들면 아무도 접속할 수 없다.
4. 주소가 바뀌지 않게: IP 대신 **PC 이름**으로 접속하면 IP가 바뀌어도 된다 — `http://PC이름:8765/?t=jiil2026`
   (PC 이름은 `hostname`). 안 되면 공유기(또는 IT 담당)에서 이 PC에 IP를 고정(DHCP 예약)한다.

코드를 업데이트(`git pull`)한 뒤에는 최소화된 서버 창을 닫으면 된다 — 10초 뒤 새 코드로 다시 켜진다.

## 참고

- 수집은 한 번에 하나만 돈다(누가 돌리는 중이면 버튼이 잠김). 진행 로그가 버튼 아래에 뜬다.
- "첨부 자격판정"을 켜면 후보마다 첨부파일을 내려받아 시간이 걸린다(후보 수 × 첨부 수). 추출한 원문은
  `output/attachment_text/`에 남는다 — CLI와 같은 위치.
- Supabase 저장·텔레그램 발송은 하지 않는다(9/28 결정: 수집 기능 완성 전까지 끔).
- 테스트: `py -m unittest tests.test_webapp`
