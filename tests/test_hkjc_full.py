import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

import hkjc_full as full

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_extracted(html_name):
    html = (FIXTURES / html_name).read_text(encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html, wait_until="domcontentloaded")
        extracted = page.evaluate(full.EXTRACT_LAST_ODDS_JS)
        browser.close()
    return extracted


class ExtractAndBuildTests(unittest.TestCase):
    def test_mixed_fixture_separates_prematch_and_inplay(self):
        extracted = load_extracted("last_odds_mixed.html")
        pre_names = [item["name"] for item in extracted["prematch"]]
        in_names = [item["name"] for item in extracted["inplay"]]
        self.assertIn("主客和", pre_names)
        self.assertIn("波膽", pre_names)
        self.assertIn("球隊入球大細", pre_names)
        self.assertTrue(any("即場投注" in name for name in in_names))
        self.assertFalse(any("即場投注" in name for name in pre_names))

    def test_core_eight_collect_all_lines(self):
        extracted = load_extracted("last_odds_mixed.html")
        info = {
            "match_id": "FB9001",
            "match_date": "2025-01-07",
            "match_time": "04:15",
            "home_team": "甲隊",
            "away_team": "乙隊",
            "half_time_score": "0:0",
            "full_time_score": "1:0",
        }
        record = full.build_record(info, extracted)
        self.assertEqual(record["主客和"]["主勝"], 2.1)
        self.assertEqual(len(record["讓球主客和"]), 2)
        self.assertEqual(len(record["入球大細"]), 3)
        self.assertEqual(record["入球大細"][1]["盤口_原文"], "[2.5/3]")
        self.assertEqual(record["讓球"][0]["主隊讓球_原文"], "[+0.5/+1]")
        self.assertEqual(record["讓球"][0]["客隊讓球_原文"], "[-0.5/-1]")
        self.assertEqual(record["角球大細"][0]["盤口"], "10.5")
        self.assertEqual(record["odds_phase"], "prematch_last_odds")
        self.assertNotEqual(record["主客和"]["主勝"], 1.55)  # 唔好食即場

    def test_team_totals_and_correct_score(self):
        extracted = load_extracted("last_odds_mixed.html")
        info = {
            "match_id": "FB9001",
            "match_date": "2025-01-07",
            "match_time": "04:15",
            "home_team": "甲隊",
            "away_team": "乙隊",
            "half_time_score": "0:0",
            "full_time_score": "1:0",
        }
        record = full.build_record(info, extracted)
        team = record["additional_markets"]["team_totals"]
        self.assertEqual(len(team["主隊"]["盤口"]), 2)
        self.assertEqual(team["客隊"]["盤口"][0]["盤口_原文"], "[0.5/1]")
        fh = record["additional_markets"]["first_half_team_totals"]
        self.assertEqual(fh["主隊"]["盤口"][0]["盤口"], "0.5")
        scores = record["additional_markets"]["correct_score"]
        self.assertEqual(scores[0]["比分"], "1:0")
        self.assertEqual(scores[0]["賠率"], 9.25)
        self.assertTrue(any(item["比分"].startswith("主其他") for item in scores))
        self.assertEqual(record["additional_markets"]["odd_even"]["單"], 1.88)
        self.assertEqual(record["additional_markets"]["half_full_time"]["主-主"], 4.4)
        self.assertIsNotNone(record["additional_markets"]["same_game_parlay"])
        self.assertIsNotNone(record["inplay_last_odds"])
        self.assertEqual(record["inplay_last_odds"]["odds_phase"], "inplay_last_odds")
        self.assertEqual(record["inplay_last_odds"]["markets"]["主客和"]["主勝"], 1.55)

    def test_partial_history_missing_markets_are_null(self):
        extracted = load_extracted("last_odds_partial.html")
        info = {
            "match_id": "FB5800",
            "match_date": "2025-01-01",
            "match_time": "20:00",
            "home_team": "A",
            "away_team": "B",
            "half_time_score": None,
            "full_time_score": "2:1",
        }
        record = full.build_record(info, extracted)
        self.assertEqual(record["主客和"]["主勝"], 1.9)
        self.assertIsNone(record["半場主客和"])
        self.assertIsNone(record["additional_markets"]["team_totals"])
        self.assertEqual(record["additional_markets"]["correct_score"][0]["比分"], "1:0")
        self.assertEqual(record["data_completeness"]["core_eight"], "partial")
        self.assertEqual(record["data_completeness"]["additional"], "partial")


class MergeAndProgressTests(unittest.TestCase):
    def test_merge_fills_missing_without_wiping_richer(self):
        existing = {
            "match_id": "FB1",
            "full_time_score": "1:0",
            "half_time_score": "0:0",
            "主客和": {"主勝": 2.0, "和": 3.0, "客勝": 3.5},
            "半場主客和": {"主勝": 2.5, "和": 2.2, "客勝": 3.0},
            "讓球主客和": None,
            "讓球": None,
            "入球大細": None,
            "半場入球大細": None,
            "角球大細": None,
            "半場角球大細": None,
            "schema_version": 1,
            "additional_markets": None,
        }
        incoming = {
            "match_id": "FB1",
            "full_time_score": "1:0",
            "half_time_score": "0:0",
            "主客和": {"主勝": 2.1, "和": 3.1, "客勝": 3.2},
            "半場主客和": None,
            "讓球主客和": None,
            "讓球": None,
            "入球大細": None,
            "半場入球大細": None,
            "角球大細": None,
            "半場角球大細": None,
            "schema_version": 2,
            "additional_markets": {
                "team_totals": None,
                "first_half_team_totals": None,
                "correct_score": [{"比分": "1:0", "賠率": 8.0}],
                "first_half_correct_score": None,
                "first_team_to_score": None,
                "total_goals": None,
                "odd_even": None,
                "half_full_time": None,
                "same_game_parlay": None,
            },
            "odds_phase": "prematch_last_odds",
        }
        merged, notes = full.merge_records(existing, incoming)
        self.assertEqual(merged["半場主客和"]["主勝"], 2.5)  # 保留舊
        self.assertEqual(merged["additional_markets"]["correct_score"][0]["賠率"], 8.0)
        self.assertTrue(notes.get("merged"))

    def test_score_correction_detected(self):
        existing = {
            "match_id": "FB2",
            "full_time_score": "1:0",
            "half_time_score": "1:0",
            "主客和": None,
            "半場主客和": None,
            "讓球主客和": None,
            "讓球": None,
            "入球大細": None,
            "半場入球大細": None,
            "角球大細": None,
            "半場角球大細": None,
        }
        incoming = {
            "match_id": "FB2",
            "full_time_score": "2:0",
            "half_time_score": "1:0",
            "主客和": {"主勝": 1.5, "和": 4.0, "客勝": 6.0},
            "半場主客和": None,
            "讓球主客和": None,
            "讓球": None,
            "入球大細": None,
            "半場入球大細": None,
            "角球大細": None,
            "半場角球大細": None,
            "schema_version": 2,
            "additional_markets": {key: None for key in full.ADDITIONAL_MARKET_KEYS},
        }
        merged, notes = full.merge_records(existing, incoming)
        self.assertEqual(merged["full_time_score"], "2:0")
        self.assertEqual(notes["score_correction"]["from"], "1:0")

    def test_skip_complete_enough(self):
        record = {
            "schema_version": 2,
            "主客和": {"主勝": 2.0, "和": 3.0, "客勝": 3.5},
            "additional_markets": {key: None for key in full.ADDITIONAL_MARKET_KEYS},
            "data_completeness": {"core_eight": "partial", "additional": "none", "missing_markets": []},
        }
        self.assertTrue(full.record_is_complete_enough(record))
        self.assertFalse(full.record_is_complete_enough({"schema_version": 1, "主客和": {"主勝": 1}}))

    def test_atomic_save_and_resume_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            results = base / "results"
            results.mkdir()
            progress_path = base / "backfill_progress.json"
            old_results = full.RESULTS_DIR
            old_progress = full.BACKFILL_PROGRESS_FILE
            full.RESULTS_DIR = results
            full.BACKFILL_PROGRESS_FILE = progress_path
            try:
                progress = full.empty_backfill_progress()
                progress["next_date"] = "2025-01-02"
                full.save_backfill_progress(progress)
                loaded = full.load_backfill_progress()
                self.assertEqual(loaded["next_date"], "2025-01-02")

                incoming = {
                    "match_id": "FB7777",
                    "match_date": "2025-01-02",
                    "match_time": "01:00",
                    "home_team": "X",
                    "away_team": "Y",
                    "half_time_score": "0:0",
                    "full_time_score": "0:1",
                    "主客和": {"主勝": 3.0, "和": 3.2, "客勝": 2.2},
                    "半場主客和": None,
                    "讓球主客和": None,
                    "讓球": None,
                    "入球大細": None,
                    "半場入球大細": None,
                    "角球大細": None,
                    "半場角球大細": None,
                    "schema_version": 2,
                    "additional_markets": {key: None for key in full.ADDITIONAL_MARKET_KEYS},
                    "odds_phase": "prematch_last_odds",
                }
                path = results / "FB7777.json"
                full.save_match_record(path, incoming, backfill_meta={"mode": "backfill", "day": "2025-01-02"})
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(saved["match_id"], "FB7777")
                self.assertEqual(saved["backfill_metadata"]["day"], "2025-01-02")

                # 重複執行：較空資料唔好覆蓋
                poorer = dict(incoming)
                poorer["主客和"] = None
                merged, notes = full.save_match_record(path, poorer)
                self.assertEqual(merged["主客和"]["主勝"], 3.0)
            finally:
                full.RESULTS_DIR = old_results
                full.BACKFILL_PROGRESS_FILE = old_progress

    def test_resolve_ranges(self):
        args = full.build_parser().parse_args(["--mode", "backfill", "--date", "2025-01-03"])
        start, end = full.resolve_backfill_range(args)
        self.assertEqual(start, date(2025, 1, 3))
        self.assertEqual(end, date(2025, 1, 3))
        args = full.build_parser().parse_args(["--mode", "backfill", "--month", "2025-02"])
        start, end = full.resolve_backfill_range(args)
        self.assertEqual(start, date(2025, 2, 1))
        self.assertEqual(end, date(2025, 2, 28))

    def test_backfill_requires_live_approval(self):
        with self.assertRaises(SystemExit):
            full.run_backfill(date(2025, 1, 1), date(2025, 1, 1), live_approved=False)

    def test_daily_cli_default_mode(self):
        args = full.build_parser().parse_args([])
        self.assertEqual(args.mode, "daily")

    def test_old_json_still_readable_shape(self):
        # 向後兼容：舊欄位仍在，新欄位可選
        extracted = load_extracted("last_odds_mixed.html")
        info = {
            "match_id": "FB9001",
            "match_date": "2025-01-07",
            "match_time": "04:15",
            "home_team": "甲隊",
            "away_team": "乙隊",
            "half_time_score": "0:0",
            "full_time_score": "1:0",
        }
        record = full.build_record(info, extracted)
        for key in [
            "match_id",
            "match_date",
            "match_time",
            "home_team",
            "away_team",
            "half_time_score",
            "full_time_score",
            *full.TARGET_MARKETS,
        ]:
            self.assertIn(key, record)


class SearchFlowUnitTests(unittest.TestCase):
    def test_aria_day_label(self):
        self.assertEqual(
            full.aria_day_label(date(2025, 1, 1)),
            "Choose Wednesday, January 1st, 2025",
        )
        self.assertEqual(
            full.aria_day_label(date(2025, 1, 2)),
            "Choose Thursday, January 2nd, 2025",
        )
        self.assertEqual(
            full.aria_day_label(date(2025, 1, 3)),
            "Choose Friday, January 3rd, 2025",
        )


if __name__ == "__main__":
    unittest.main()
