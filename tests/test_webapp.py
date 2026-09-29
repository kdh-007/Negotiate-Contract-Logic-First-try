"""사내 웹앱(webapp/) 테스트 — 수집은 픽스처로 대신하고, API·상태 저장·직렬화를 확인한다.

jiil-past-contracts(비공개)가 옆에 없으면 싱크로율 실계산 테스트만 건너뛴다.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import qualify  # noqa: E402
from nego.config import load_config  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.pipeline import RunStats, build_candidates  # noqa: E402
from tests import fixtures  # noqa: E402
from webapp.collect import Collector, serialize  # noqa: E402
from webapp.server import App, make_handler  # noqa: E402
from webapp.store import Store  # noqa: E402
from webapp.sync import PastIndex, default_jiil_repo, level  # noqa: E402

NOW = datetime(2026, 9, 14, 9, 0)


class FakePast:
    """과거 실적 없이도 돌도록 점수를 고정으로 돌려주는 대역."""

    error = None
    projects = [{"year": 2025, "title": "○○과학관 전시", "overview": "전시물 제작", "exhibition": "", "tags": {}}]

    def score(self, title, text="", top=5):
        return {"score": 0.7, "level": level(0.7), "basis": "과업 원문" if text else "공고명만",
                "tags": {}, "top": [{"index": 0, "year": 2025, "title": "○○과학관 전시", "score": 0.7, "shared": []}]}

    def public_list(self):
        return self.projects


def fixture_run(config, days, attachments, cats, prespec=False):
    config.screen.keywords = ["전시관", "박물관", "과학관", "체험관", "전시디자인", "전시물"]
    stats = RunStats()
    notices = [notice_from_raw(raw, "용역") for raw in fixtures.service_notices()]
    groups = qualify.group_license_rows(fixtures.license_rows())
    return build_candidates(notices, config, groups, {}, NOW, stats, cats), stats


def config_with_key():
    config = load_config()
    config.api.service_key = "TESTKEY-0123456789"
    return config


class TestSyncLevel(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(level(None), "판정 불가")
        self.assertEqual(level(0.8), "높음")
        self.assertEqual(level(0.5), "경계선")
        self.assertEqual(level(0.1), "낮음")

    def test_missing_repo_degrades(self):
        past = PastIndex.load(Path(tempfile.mkdtemp()))
        self.assertFalse(past.available)
        self.assertIn("JIIL_REPO", past.error)
        self.assertEqual(past.score("박물관 전시")["level"], "판정 불가")

    @unittest.skipUnless((default_jiil_repo() / "docs/summaries/past_task_profiles.json").exists(), "jiil 레포 없음")
    def test_real_profiles_rank_similar_project(self):
        past = PastIndex.load()
        museum = past.score("어린이박물관 전시 설계 및 제작설치", "어린이박물관 체험 전시물\n어린이박물관 모형 제작\n전시 그래픽")
        lighting = past.score("공원 야간경관 조명 설치", "경관조명 기구 설치\n연출제어시스템")
        self.assertGreater(museum["score"], 0)
        self.assertTrue(museum["top"])
        self.assertEqual(museum["basis"], "과업 원문")
        self.assertNotEqual(museum["top"][0]["title"], lighting["top"][0]["title"])


class TestSerialize(unittest.TestCase):
    def test_candidate_card_has_qualification_and_sync(self):
        candidates, stats = fixture_run(load_config(), 7, False, {"협상"})
        cards = {c["notice_no"]: c for c in (serialize(x, FakePast()) for x in candidates)}
        flagged = cards["R26TEST00009"]
        self.assertTrue(flagged["qualification"]["checked"])
        self.assertFalse(flagged["qualification"]["passes"])
        self.assertTrue(flagged["qualification"]["missing"])
        # 배지 분수와 팝업 줄 수가 같아야 한다 (사용자 지적: 5/5인데 4줄, 0/2인데 4줄)
        q = flagged["qualification"]
        self.assertEqual(q["total"], 2)
        self.assertEqual(q["satisfied"], 0)
        self.assertEqual(len(q["missing"]), q["total"] - q["satisfied"])
        self.assertTrue(any(" 또는 " in line for line in q["missing"]), q["missing"])
        self.assertIn("g2b.go.kr", flagged["detail_url"])
        self.assertEqual(sum(p["total"] for p in q["parts"]), q["total"])
        self.assertEqual(flagged["category"], "협상")
        self.assertEqual(flagged["key"], "R26TEST00009-000")
        self.assertEqual(flagged["sync"]["basis"], "공고명만")
        json.dumps(flagged, ensure_ascii=False)  # 화면으로 보낼 수 있어야 한다
        rejected = [serialize(x, FakePast()) for x in stats.rejected]
        self.assertTrue(all(r["excluded_reason"] for r in rejected))


class TestQualificationLines(unittest.TestCase):
    def test_duplicate_groups_merge_and_counts_match(self):
        from nego.qualify import LicenseGroup, QualificationResult
        from webapp.collect import _group_labels

        sat = [LicenseGroup("1", ["실내건축공사업(4990)"]), LicenseGroup("2", ["실내건축공사업/4990", "실내건축공사업"]),
               LicenseGroup("3", ["실물모형및전시물(6010989901)"])]
        miss = [LicenseGroup("4", ["토목공사업(0001)", "토목건축공사업"])]
        self.assertEqual(_group_labels(sat), ["실내건축공사업(4990)", "실물모형및전시물(6010989901)"])
        self.assertEqual(_group_labels(miss), ["토목공사업(0001) 또는 토목건축공사업"])

    def test_split_industry_and_product(self):
        from webapp.collect import _split_parts

        parts = _split_parts(
            missing=["조명용제어장치(3912110702)", "토목공사업(0001) 또는 토목건축공사업"],
            satisfied=["실내건축공사업(4990)", "실물모형및전시물(6010989901)", "전시사업자"],
        )
        by = {p["key"]: p for p in parts}
        self.assertEqual((by["industry"]["satisfied"], by["industry"]["total"]), (2, 3))
        self.assertEqual(by["industry"]["missing"], ["토목공사업(0001) 또는 토목건축공사업"])
        self.assertEqual((by["product"]["satisfied"], by["product"]["total"]), (1, 2))
        self.assertEqual(by["product"]["missing"], ["조명용제어장치(3912110702)"])
        self.assertEqual([p["key"] for p in _split_parts([], ["실내건축공사업(4990)"])], ["industry"])

    def test_detail_url_fallback(self):
        from nego.models import prespec_from_raw
        from webapp.collect import _g2b_url

        n = notice_from_raw(fixtures.notice("R26BK01745222", bidNtceDtlUrl=""), "공사")
        self.assertEqual(_g2b_url(n), "https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo=R26BK01745222&bidPbancOrd=000")
        self.assertIsNone(_g2b_url(prespec_from_raw({"bfSpecRgstNo": "R26BD1"}, "물품")))


class TestStore(unittest.TestCase):
    def setUp(self):
        self.store = Store(Path(tempfile.mkdtemp()) / "t.sqlite3")

    def test_state_and_comments(self):
        self.store.set_state("A-000", by="김", status="검토중")
        self.store.set_state("A-000", by="이", assignee="이")
        st = self.store.states()["A-000"]
        self.assertEqual((st["status"], st["assignee"], st["updated_by"]), ("검토중", "이", "이"))
        self.store.set_state("A-000", by="이", clear_assignee=True)
        self.assertIsNone(self.store.states()["A-000"]["assignee"])
        with self.assertRaises(ValueError):
            self.store.set_state("A-000", by="김", status="아무거나")
        self.store.add_comment("A-000", "김", "공동수급 가능?")
        self.assertEqual(self.store.comment_counts(), {"A-000": 1})


class TestServer(unittest.TestCase):
    def setUp(self):
        store = Store(Path(tempfile.mkdtemp()) / "t.sqlite3")
        past = FakePast()
        self.collector = Collector(store, past, runner=fixture_run, config_loader=config_with_key)
        app = App(store, past, self.collector, token="secret-token")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def call(self, path, body=None, token="secret-token"):
        req = urllib.request.Request(self.base + path, headers={"X-App-Token": token})
        if body is not None:
            req.data = json.dumps(body).encode()
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def test_token_required_for_api_not_for_page(self):
        self.assertEqual(self.call("/api/meta", token="wrong")[0], 401)
        with urllib.request.urlopen(self.base + "/") as res:
            self.assertIn("싱크로율 탐색기", res.read().decode())
        self.assertEqual(self.call("/../server.py")[0], 404)

    def test_collect_then_state_and_comments(self):
        status, _ = self.call("/api/collect", {"days": 7, "attachments": False, "categories": ["협상"]})
        self.assertEqual(status, 202)
        for _ in range(100):
            job = self.call("/api/job")[1]
            if not job["running"]:
                break
            time.sleep(0.05)
        self.assertIsNone(job["error"])
        run = self.call("/api/results")[1]["run"]
        keys = [c["key"] for c in run["candidates"]]
        self.assertIn("R26TEST00009-000", keys)
        self.assertTrue(run["rejected"])

        self.assertEqual(self.call("/api/state", {"key": keys[0], "status": "참가"})[0], 400, "이름 없이 거부")
        status, body = self.call("/api/state", {"key": keys[0], "status": "참가", "take": True, "name": "김지일"})
        self.assertEqual((status, body["state"]["status"], body["state"]["assignee"]), (200, "참가", "김지일"))
        _, body = self.call("/api/comments", {"key": keys[0], "name": "김지일", "body": "검토 부탁"})
        self.assertEqual(body["comments"][0]["body"], "검토 부탁")
        res = self.call("/api/results")[1]
        self.assertEqual(res["comment_counts"][keys[0]], 1)
        self.assertEqual(res["states"][keys[0]]["status"], "참가")

    def test_bad_period_and_missing_key(self):
        self.assertEqual(self.call("/api/collect", {"days": 5})[0], 400)
        self.collector._config_loader = load_config  # 서비스키 없는 설정
        import os
        saved = os.environ.pop("NARA_SERVICE_KEY", None)
        try:
            self.call("/api/collect", {"days": 1, "attachments": False})
            for _ in range(100):
                job = self.call("/api/job")[1]
                if not job["running"]:
                    break
                time.sleep(0.05)
            self.assertIn("NARA_SERVICE_KEY", job["error"])
        finally:
            if saved is not None:
                os.environ["NARA_SERVICE_KEY"] = saved


if __name__ == "__main__":
    unittest.main()
