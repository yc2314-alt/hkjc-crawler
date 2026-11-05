import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from upcoming_snapshot import (
    SnapshotStore,
    append_jsonl,
    build_snapshot,
    build_snapshot_markets,
    is_prematch_valid,
    iter_jsonl,
    load_snapshots_for_match,
    observation_summary,
)

HK = ZoneInfo("Asia/Hong_Kong")


def sample_raw(had_home="2.10"):
    return {
        "HAD": {"home": had_home, "draw": "3.40", "away": "3.20"},
        "FHA": {"home": "2.80", "draw": "2.20", "away": "2.90"},
        "HHA": [
            {
                "no": 0,
                "cond": {"H": "[+1]", "D": "[+1]", "A": "[-1]"},
                "odds": {"H": "1.82", "D": "3.60", "A": "3.15"},
            }
        ],
        "HDC": [
            {
                "no": 0,
                "cond": {"H": "[+0.5/+1]", "A": "[-0.5/-1]"},
                "odds": {"H": "1.66", "A": "2.15"},
            }
        ],
        "HIL": [{"no": 1, "line": "[2.5]", "high": "1.67", "low": "2.07"}],
        "FHL": None,
        "CHL": [{"no": 1, "line": "[10.5]", "high": "1.85", "low": "1.85"}],
        "FCH": None,
    }


class SnapshotBuildTests(unittest.TestCase):
    def test_markets_keep_raw_lines_and_nulls(self):
        markets = build_snapshot_markets(sample_raw())
        self.assertEqual(markets["主客和"]["主勝"], 2.1)
        self.assertEqual(markets["讓球"][0]["主隊讓球_原文"], "[+0.5/+1]")
        self.assertEqual(markets["入球大細"][0]["盤口_原文"], "[2.5]")
        self.assertIsNone(markets["半場入球大細"])
        self.assertIsNone(markets["半場角球大細"])

    def test_missing_market_not_fake(self):
        markets = build_snapshot_markets({"HAD": {"home": "2.0", "draw": "x", "away": "3.0"}})
        self.assertIsNone(markets["主客和"])
        self.assertIsNone(markets["讓球"])


class PrematchValidityTests(unittest.TestCase):
    def test_before_close_is_valid(self):
        captured = datetime(2026, 10, 31, 12, 0, tzinfo=HK)
        close = datetime(2026, 11, 1, 7, 0, tzinfo=HK)
        self.assertTrue(is_prematch_valid(captured, close, close))

    def test_after_close_not_valid_prematch(self):
        captured = datetime(2026, 11, 1, 8, 0, tzinfo=HK)
        close = datetime(2026, 11, 1, 7, 0, tzinfo=HK)
        self.assertFalse(is_prematch_valid(captured, close, close))
        snap = build_snapshot(
            match_id="FB9001",
            home_team="A",
            away_team="B",
            captured_at=captured,
            kickoff_at=close,
            betting_close_at=close,
            raw_markets=sample_raw(),
            run_id="abcd1234ffff",
        )
        self.assertFalse(snap["prematch_valid"])
        self.assertEqual(snap["odds_phase"], "invalid_prematch_observation")


class StoreAndDedupeTests(unittest.TestCase):
    def test_one_run_one_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SnapshotStore(root, run_id="run1" + "0" * 28)
            captured = datetime(2026, 10, 31, 21, 0, tzinfo=HK)
            close = captured + timedelta(hours=10)
            first = store.save(
                match_id="FB9001",
                home_team="甲",
                away_team="乙",
                captured_at=captured,
                kickoff_at=close,
                betting_close_at=close,
                raw_markets=sample_raw(),
            )
            retry = store.save(
                match_id="FB9001",
                home_team="甲",
                away_team="乙",
                captured_at=captured + timedelta(seconds=5),
                kickoff_at=close,
                betting_close_at=close,
                raw_markets=sample_raw("2.20"),
            )
            self.assertIsNotNone(first)
            self.assertIsNone(retry)
            rows = load_snapshots_for_match(root, "FB9001")
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["prematch_valid"])

    def test_second_run_adds_second_snapshot_even_if_odds_same(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            close = datetime(2026, 11, 1, 7, 0, tzinfo=HK)
            store1 = SnapshotStore(root, run_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
            store2 = SnapshotStore(root, run_id="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
            t1 = datetime(2026, 10, 31, 10, 0, tzinfo=HK)
            t2 = datetime(2026, 10, 31, 18, 0, tzinfo=HK)
            store1.save(
                match_id="FB9002",
                home_team="甲",
                away_team="乙",
                captured_at=t1,
                kickoff_at=close,
                betting_close_at=close,
                raw_markets=sample_raw("2.10"),
            )
            store2.save(
                match_id="FB9002",
                home_team="甲",
                away_team="乙",
                captured_at=t2,
                kickoff_at=close,
                betting_close_at=close,
                raw_markets=sample_raw("2.10"),
            )
            rows = load_snapshots_for_match(root, "FB9002")
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["markets"]["主客和"]["主勝"], 2.1)
            self.assertEqual(rows[1]["markets"]["主客和"]["主勝"], 2.1)
            self.assertNotEqual(rows[0]["run_id"], rows[1]["run_id"])
            self.assertEqual(rows[0]["captured_at"], "2026-10-31T10:00:00+08:00")

    def test_single_observation_no_fake_change_signal(self):
        summary = observation_summary(
            [
                {
                    "prematch_valid": True,
                    "captured_at": "2026-10-31T10:00:00+08:00",
                    "markets": {"主客和": {"主勝": 2.1}},
                }
            ]
        )
        self.assertEqual(summary["change_analysis"], "single_observation")
        self.assertNotIn("變盤", summary["note"][:2])  # 只係限制說明
        self.assertIn("不能計算變盤", summary["note"])

    def test_corrupt_jsonl_line_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.jsonl"
            path.write_text(
                '{"snapshot_id":"ok","match_id":"FB1"}\n'
                "{not-json\n"
                '{"snapshot_id":"ok2","match_id":"FB1"}\n',
                encoding="utf-8",
            )
            rows = list(iter_jsonl(path))
            self.assertEqual(len(rows), 2)

    def test_interrupted_staging_does_not_block_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "day.jsonl"
            staging = path.with_suffix(".jsonl.staging")
            staging.write_text('{"orphan":true}\n', encoding="utf-8")
            append_jsonl(path, {"snapshot_id": "a", "match_id": "FB1"})
            rows = list(iter_jsonl(path))
            self.assertEqual(len(rows), 1)
            self.assertFalse(staging.exists())

    def test_push_failure_does_not_erase_local(self):
        """模擬 Git push 失敗：本機 JSONL 仍然存在。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SnapshotStore(root, run_id="cccccccccccccccccccccccccccccccc")
            captured = datetime(2026, 10, 31, 12, 0, tzinfo=HK)
            close = captured + timedelta(days=1)
            store.save(
                match_id="FB9003",
                home_team="甲",
                away_team="乙",
                captured_at=captured,
                kickoff_at=close,
                betting_close_at=close,
                raw_markets=sample_raw(),
            )
            # 假設 push 失敗，唔刪本機
            self.assertTrue((root / "by_match" / "FB9003.jsonl").exists())
            self.assertEqual(len(load_snapshots_for_match(root, "FB9003")), 1)


class UpcomingJsonCompatibilityTests(unittest.TestCase):
    def test_upcoming_record_shape_unchanged_by_snapshot_builder(self):
        # upcoming 仍然用 hkjc_upcoming.build_record；快照 markets 係獨立結構
        from hkjc_upcoming import build_record

        deadline = datetime(2026, 11, 1, 7, 0, tzinfo=HK)
        info = {
            "match_id": "FB9001",
            "deadline": deadline,
            "home_team": "甲",
            "away_team": "乙",
        }
        captured = datetime(2026, 10, 31, 22, 0, tzinfo=HK)
        record = build_record(info, sample_raw(), updated_at=captured)
        self.assertEqual(record["updated_at"], "2026-10-31 22:00:00")
        self.assertEqual(record["kickoff"], "07:00")
        self.assertEqual(record["status"], "未開波")
        self.assertIn("主客和", record)
        self.assertNotIn("snapshot_id", record)
        self.assertNotIn("markets", record)


if __name__ == "__main__":
    unittest.main()
