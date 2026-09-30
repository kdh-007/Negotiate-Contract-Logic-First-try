"""입찰 공고 싱크로율 탐색기 — 사내 웹앱.

`python -m webapp`으로 띄운다. 수집·자격판정은 `nego` 파이프라인을 그대로 호출하고,
싱크로율은 비공개 레포 jiil-past-contracts의 과업 프로필(`evaluation/task_profile.py`,
`docs/summaries/past_task_profiles.json`)로 계산한다 — 과거 실적 자료는 이 공개 레포에
들어오지 않고, 서버가 옆에 클론된 jiil 레포에서 읽기만 한다.
"""
