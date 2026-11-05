"""未開波實際觀察快照：只保存成功擷取到的盤口，不造假、不補中間價。"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HK = ZoneInfo("Asia/Hong_Kong")
SNAPSHOT_SCHEMA_VERSION = 1
SOURCE = "HKJC"

MARKET_CODES = {
    "主客和": "HAD",
    "半場主客和": "FHA",
    "讓球主客和": "HHA",
    "讓球": "HDC",
    "入球大細": "HIL",
    "半場入球大細": "FHL",
    "角球大細": "CHL",
    "半場角球大細": "FCH",
}


def to_float(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def keep_text(value):
    return (value or "").strip()


def strip_brackets(value):
    text = keep_text(value)
    if text.startswith("[") and text.endswith("]"):
        return text[1:-1].strip()
    return text


def iso_hk(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=HK)
    else:
        dt = dt.astimezone(HK)
    return dt.isoformat(timespec="seconds")


def compact_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=HK)
    else:
        dt = dt.astimezone(HK)
    # 例：20261031T225131p0800（香港）
    return dt.strftime("%Y%m%dT%H%M%S") + "p0800"


def new_run_id() -> str:
    return uuid.uuid4().hex


def make_snapshot_id(match_id: str, captured_at: datetime, run_id: str) -> str:
    return f"{match_id}_{compact_iso(captured_at)}_{run_id[:8]}"


def is_prematch_valid(captured_at: datetime, betting_close_at: datetime | None, kickoff_at: datetime | None) -> bool:
    """只有擷取時刻嚴格早於投注截止（及開賽，若有）先算有效賽前觀察。"""
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=HK)
    limits = [t for t in (betting_close_at, kickoff_at) if t is not None]
    if not limits:
        return False
    for limit in limits:
        if limit.tzinfo is None:
            limit = limit.replace(tzinfo=HK)
        if captured_at >= limit:
            return False
    return True


def build_simple_market(raw):
    if not raw:
        return None
    home = to_float(raw.get("home"))
    draw = to_float(raw.get("draw"))
    away = to_float(raw.get("away"))
    if None in (home, draw, away):
        return None
    return {"主勝": home, "和": draw, "客勝": away}


def build_handicap_1x2_market(lines):
    rows = []
    for line in lines or []:
        cond = line.get("cond") or {}
        odds = line.get("odds") or {}
        home_raw = keep_text(cond.get("H"))
        draw_raw = keep_text(cond.get("D"))
        away_raw = keep_text(cond.get("A"))
        home_line = strip_brackets(home_raw)
        home = to_float(odds.get("H"))
        draw = to_float(odds.get("D"))
        away = to_float(odds.get("A"))
        if not home_line or None in (home, draw, away):
            continue
        rows.append(
            {
                "主隊讓球": home_line,
                "主隊讓球_原文": home_raw,
                "和盤口_原文": draw_raw or None,
                "客隊讓球_原文": away_raw or None,
                "主勝": home,
                "和": draw,
                "客勝": away,
            }
        )
    return rows or None


def build_handicap_market(lines):
    rows = []
    for line in lines or []:
        cond = line.get("cond") or {}
        odds = line.get("odds") or {}
        home_raw = keep_text(cond.get("H"))
        away_raw = keep_text(cond.get("A"))
        home_line = strip_brackets(home_raw)
        home = to_float(odds.get("H"))
        away = to_float(odds.get("A"))
        if not home_line or None in (home, away):
            continue
        rows.append(
            {
                "主隊讓球": home_line,
                "主隊讓球_原文": home_raw,
                "客隊讓球_原文": away_raw or None,
                "主勝": home,
                "客勝": away,
            }
        )
    return rows or None


def build_line_market(lines):
    rows = []
    for line in lines or []:
        raw = keep_text(line.get("line"))
        label = strip_brackets(raw)
        over = to_float(line.get("high"))
        under = to_float(line.get("low"))
        if not label or None in (over, under):
            continue
        rows.append({"盤口": label, "盤口_原文": raw, "大": over, "細": under})
    return rows or None


def build_snapshot_markets(raw_markets: dict) -> dict:
    """八個市場；缺市為 null，不填假數值。含盤口原文。"""
    raw_markets = raw_markets or {}
    return {
        "主客和": build_simple_market(raw_markets.get("HAD")),
        "半場主客和": build_simple_market(raw_markets.get("FHA")),
        "讓球主客和": build_handicap_1x2_market(raw_markets.get("HHA")),
        "讓球": build_handicap_market(raw_markets.get("HDC")),
        "入球大細": build_line_market(raw_markets.get("HIL")),
        "半場入球大細": build_line_market(raw_markets.get("FHL")),
        "角球大細": build_line_market(raw_markets.get("CHL")),
        "半場角球大細": build_line_market(raw_markets.get("FCH")),
    }


def build_snapshot(
    *,
    match_id: str,
    home_team: str,
    away_team: str,
    captured_at: datetime,
    kickoff_at: datetime | None,
    betting_close_at: datetime | None,
    raw_markets: dict,
    run_id: str,
) -> dict:
    prematch_valid = is_prematch_valid(captured_at, betting_close_at, kickoff_at)
    return {
        "snapshot_id": make_snapshot_id(match_id, captured_at, run_id),
        "match_id": match_id,
        "source": SOURCE,
        "run_id": run_id,
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "captured_at": iso_hk(captured_at),
        "kickoff_at": iso_hk(kickoff_at) if kickoff_at else None,
        "betting_close_at": iso_hk(betting_close_at) if betting_close_at else None,
        "home_team": home_team,
        "away_team": away_team,
        "prematch_valid": prematch_valid,
        "odds_phase": "prematch_observed" if prematch_valid else "invalid_prematch_observation",
        "markets": build_snapshot_markets(raw_markets),
    }


def iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                yield json.loads(text)
            except json.JSONDecodeError:
                # 中斷寫入造成嘅壞行：略過，唔中斷讀取
                continue


def append_jsonl(path: Path, obj: dict) -> None:
    """追加一行 JSONL；flush + fsync。先寫 staging 再 append，減低半行殘留風險。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if "\n" in line or "\r" in line:
        raise ValueError("snapshot JSON 不可含換行")
    staging = path.with_suffix(path.suffix + ".staging")
    staging.write_text(line + "\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        staging.unlink(missing_ok=True)
    except OSError:
        pass


def day_file(root: Path, captured_at: datetime) -> Path:
    day = captured_at.astimezone(HK).strftime("%Y-%m-%d")
    return root / "by_day" / f"{day}.jsonl"


def match_file(root: Path, match_id: str) -> Path:
    if not re.fullmatch(r"FB\d+", match_id):
        raise ValueError(f"無效 match_id: {match_id}")
    return root / "by_match" / f"{match_id}.jsonl"


class SnapshotStore:
    """一次爬蟲執行一個 store；同輪同一 match_id 只寫一次。"""

    def __init__(self, root: Path, run_id: str | None = None):
        self.root = Path(root)
        self.run_id = run_id or new_run_id()
        self._saved_match_ids: set[str] = set()

    def already_saved_this_run(self, match_id: str) -> bool:
        return match_id in self._saved_match_ids

    def save(
        self,
        *,
        match_id: str,
        home_team: str,
        away_team: str,
        captured_at: datetime,
        kickoff_at: datetime | None,
        betting_close_at: datetime | None,
        raw_markets: dict,
    ) -> dict | None:
        if self.already_saved_this_run(match_id):
            return None
        snapshot = build_snapshot(
            match_id=match_id,
            home_team=home_team,
            away_team=away_team,
            captured_at=captured_at,
            kickoff_at=kickoff_at,
            betting_close_at=betting_close_at,
            raw_markets=raw_markets,
            run_id=self.run_id,
        )
        # 寫 by_day 同 by_match；本機成功先標記，Git push 失敗唔影響
        append_jsonl(day_file(self.root, captured_at), snapshot)
        append_jsonl(match_file(self.root, match_id), snapshot)
        self._saved_match_ids.add(match_id)
        return snapshot


def load_snapshots_for_match(root: Path, match_id: str) -> list[dict]:
    rows = list(iter_jsonl(match_file(root, match_id)))
    rows.sort(key=lambda item: item.get("captured_at") or "")
    return rows


def observation_summary(snapshots: list[dict]) -> dict:
    """只描述已觀察次數限制，唔產生虛假變盤。"""
    valid = [row for row in snapshots if row.get("prematch_valid")]
    count = len(valid)
    if count == 0:
        return {
            "valid_observation_count": 0,
            "change_analysis": "unavailable",
            "note": "冇有效賽前觀察",
        }
    if count == 1:
        return {
            "valid_observation_count": 1,
            "change_analysis": "single_observation",
            "note": "只有單次觀察，不能計算變盤，亦不能斷言期間無變動",
        }
    return {
        "valid_observation_count": count,
        "change_analysis": "observed_points_only",
        "note": "只可比較已觀察到的時間點；未觀察時段不得補價",
    }
