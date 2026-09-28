"""텔레그램 발송(`nego/telegram.py`)과 매일/주간 실행용 조회 기간·저장 규칙 테스트.
실제 텔레그램 API는 부르지 않는다.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import telegram as T  # noqa: E402
from nego.config import load_config  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.pipeline import RunStats, build_candidates, complete_days_window  # noqa: E402
from nego.supabase_repo import rejected_row  # noqa: E402
from tests import fixtures  # noqa: E402

NOW = datetime(2026, 9, 1, 8, 5)


def _candidates(n: int, award: str = fixtures.QUALIFY):
    config = load_config()
    config.screen.keywords = ["전시관"]
    raws = [fixtures.notice(f"R26TG{i:04d}", title=f"○○전시관 전시물 제작설치 {i}", award=award) for i in range(n)]
    stats = RunStats(period_begin=datetime(2026, 8, 31), period_end=datetime(2026, 8, 31, 23, 59))
    candidates = build_candidates([notice_from_raw(r, "용역") for r in raws], config, {}, {}, NOW, stats, {"입찰"})
    return candidates, stats


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {"ok": status == 200}
        self.text = str(self._body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses=None):
        self.calls = []
        self._responses = list(responses or [])

    def post(self, url, timeout=None, data=None, files=None):
        self.calls.append({"url": url, "data": data, "files": files})
        return self._responses.pop(0) if self._responses else FakeResponse()


class TestCompleteDaysWindow(unittest.TestCase):
    def test_daily_window_is_yesterday_whole_day(self):
        begin, end = complete_days_window(datetime(2026, 9, 29, 8, 0), 1)
        self.assertEqual(begin, datetime(2026, 9, 28, 0, 0))
        self.assertEqual(end, datetime(2026, 9, 28, 23, 59))

    def test_consecutive_runs_do_not_overlap_or_gap(self):
        """스케줄이 늦게 돌아도(08:47) 구간은 같다 — 같은 공고를 두 번 보내지 않는다."""
        _, end_mon = complete_days_window(datetime(2026, 9, 28, 8, 0), 1)
        begin_tue, _ = complete_days_window(datetime(2026, 9, 29, 8, 47), 1)
        self.assertEqual((begin_tue - end_mon).total_seconds(), 60)

    def test_weekly_window(self):
        begin, end = complete_days_window(datetime(2026, 9, 28, 8, 10), 7)
        self.assertEqual(begin, datetime(2026, 9, 21, 0, 0))
        self.assertEqual(end, datetime(2026, 9, 27, 23, 59))


class TestBuildMessages(unittest.TestCase):
    def test_lists_candidates_with_category_and_short_money(self):
        candidates, stats = _candidates(2)
        messages = T.build_messages(candidates, stats)
        self.assertEqual(len(messages), 1)
        text = messages[0]
        self.assertIn("나라장터 입찰 모니터링 리포트 (입찰)", text)
        self.assertIn("후보 <b>2건</b>", text)
        self.assertIn("[입찰]", text)
        self.assertIn("5.0억", text)
        self.assertIn('href="https://www.g2b.go.kr/detail/R26TG0000"', text)

    def test_empty_run_still_reports(self):
        _, stats = _candidates(0)
        messages = T.build_messages([], stats)
        self.assertEqual(len(messages), 1)
        self.assertIn("새 공고가 없습니다", messages[0])

    def test_long_list_is_split_without_breaking_a_notice(self):
        candidates, stats = _candidates(40)
        messages = T.build_messages(candidates, stats)
        self.assertGreater(len(messages), 1)
        for m in messages:
            self.assertLessEqual(len(m), T.MESSAGE_LIMIT)
        joined = "\n\n".join(messages)
        for i in range(1, 41):
            self.assertEqual(joined.count(f"<b>{i}. ["), 1)

    def test_title_is_html_escaped(self):
        candidates, stats = _candidates(1)
        candidates[0].notice.title = "A <B> & C"
        self.assertIn("A &lt;B&gt; &amp; C", T.build_messages(candidates, stats)[0])


class TestSendReport(unittest.TestCase):
    def setUp(self):
        self.config = T.TelegramConfig(token="123:SECRET", chat_ids=["111", "222"])

    def test_sends_message_and_report_to_every_chat(self):
        candidates, stats = _candidates(1)
        html_path = Path(__file__).resolve().parent / "fixtures" / "attachments" / "README.md"
        session = FakeSession()
        errors = T.send_report(self.config, candidates, stats, html_path, session=session)
        self.assertEqual(errors, [])
        methods = [(c["url"].rsplit("/", 1)[1], c["data"]["chat_id"]) for c in session.calls]
        self.assertEqual(
            methods,
            [("sendMessage", "111"), ("sendDocument", "111"), ("sendMessage", "222"), ("sendDocument", "222")],
        )

    def test_no_document_when_no_candidates(self):
        _, stats = _candidates(0)
        session = FakeSession()
        T.send_report(self.config, [], stats, Path("없는파일.html"), session=session)
        self.assertTrue(all(c["url"].endswith("sendMessage") for c in session.calls))

    def test_client_error_is_reported_with_token_masked(self):
        _, stats = _candidates(0)
        session = FakeSession([FakeResponse(400, {"ok": False, "description": "Bad Request: chat not found"})])
        errors = T.send_report(
            T.TelegramConfig(token="123:SECRET", chat_ids=["999"]), [], stats, None, session=session
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("chat not found", errors[0])
        self.assertNotIn("SECRET", errors[0])
        self.assertEqual(len(session.calls), 1, "4xx는 다시 시도해도 같으니 재시도하지 않는다")

    def test_config_from_env(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1, 2"}):
            self.assertEqual(T.TelegramConfig.from_env().chat_ids, ["1", "2"])
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "1"}):
            self.assertIsNone(T.TelegramConfig.from_env())


class TestRejectedRow(unittest.TestCase):
    def test_non_negotiated_rejected_rows_drop_raw(self):
        candidates, _ = _candidates(1)
        row = rejected_row(candidates[0])
        self.assertIsNone(row["raw"])
        self.assertEqual(row["title"], candidates[0].notice.title)

    def test_negotiated_rejected_rows_keep_raw(self):
        candidates, _ = _candidates(1)
        candidates[0].category = "협상"
        self.assertIsNotNone(rejected_row(candidates[0])["raw"])


if __name__ == "__main__":
    unittest.main()
