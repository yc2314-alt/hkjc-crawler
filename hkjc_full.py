import argparse
import json
import random
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

from hkjc_crawler import (
    MARKET_PAGE_NAMES,
    MATCH_INFO_SELECTOR,
    RESULTS_URL,
    TARGET_MARKETS,
    _text,
    extract_match_data,
    find_match,
    return_to_results,
)

BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"
PROGRESS_FILE = BASE_DIR / "progress.json"
BACKFILL_PROGRESS_FILE = BASE_DIR / "backfill_progress.json"
ALL_RESULTS_FILE = BASE_DIR / "all_results.json"

SCHEMA_VERSION = 2
SOURCE = "bet.hkjc.com/ch/football/results"
DEFAULT_BACKFILL_FROM = date(2025, 1, 1)
PAUSE_BETWEEN_MATCHES = 5

SIMPLE_MARKETS = {"主客和", "半場主客和"}
HANDICAP_1X2_MARKETS = {"讓球主客和"}
HANDICAP_MARKETS = {"讓球"}
LINE_MARKETS = {"入球大細", "半場入球大細", "角球大細", "半場角球大細"}

ADDITIONAL_MARKET_KEYS = [
    "team_totals",
    "first_half_team_totals",
    "correct_score",
    "first_half_correct_score",
    "first_team_to_score",
    "total_goals",
    "odd_even",
    "half_full_time",
    "same_game_parlay",
]

# 擴充抽取：分開賽前最後賠率同即場投注之最後賠率；保留所有盤口線原文。
EXTRACT_LAST_ODDS_JS = r"""
() => {
  const clean = (value) => (value || "").replace(/\s+/g, " ").trim();
  const oddsText = (node) => {
    if (!node) return "";
    const span = node.querySelector(".add-to-slip, .oddsColor");
    return clean(span ? span.textContent : "");
  };
  const baseName = (desc) => clean(desc.replace(/-\s*即場投注之最後賠率.*$/, "").replace(/即場投注之最後賠率/, ""));
  const isInplay = (desc) => desc.includes("即場投注");

  const parseLineBlocks = (row) => {
    const teamNodes = [...row.querySelectorAll(".teamHiloTeamName")];
    if (teamNodes.length) {
      const teams = [];
      for (const teamNode of teamNodes) {
        let cursor = teamNode.nextElementSibling;
        while (cursor && !cursor.classList.contains("oddsLine") && !cursor.classList.contains("teamHiloTeamName")) {
          cursor = cursor.nextElementSibling;
        }
        if (!cursor || !cursor.classList.contains("oddsLine")) continue;
        const lines = [];
        const kids = [...cursor.children];
        for (let i = 0; i < kids.length; i++) {
          const child = kids[i];
          if (!child.classList.contains("lineNum")) continue;
          let oddsBox = child.nextElementSibling;
          while (oddsBox && !oddsBox.classList.contains("odds")) oddsBox = oddsBox.nextElementSibling;
          const values = oddsBox
            ? [...oddsBox.querySelectorAll(".oddsCheckboxGrid")].map((grid) => oddsText(grid)).filter(Boolean)
            : [];
          lines.push({ line: clean(child.textContent), values });
        }
        teams.push({ team: clean(teamNode.textContent), side: teamNode.getAttribute("data-testid") || "", lines });
      }
      return { kind: "team_line", teams };
    }

    const lineNums = [...row.querySelectorAll(".lineNum")];
    if (lineNums.length) {
      return {
        kind: "line",
        lines: lineNums.map((lineNum) => {
          let oddsBox = lineNum.nextElementSibling;
          while (oddsBox && !oddsBox.classList.contains("odds")) oddsBox = oddsBox.nextElementSibling;
          const values = oddsBox
            ? [...oddsBox.querySelectorAll(".oddsCheckboxGrid")].map((grid) => oddsText(grid)).filter(Boolean)
            : [];
          return { line: clean(lineNum.textContent), values };
        }),
      };
    }
    return null;
  };

  const parseHandicap = (row) => {
    const blocks = [...row.querySelectorAll(".odds")].filter((el) => el.querySelector(".hdcOddsItem"));
    if (!blocks.length) return null;
    return {
      kind: "handicap",
      lines: blocks.map((block) =>
        [...block.querySelectorAll(".hdcOddsItem")].map((item) => ({
          cond: clean(item.querySelector(".cond") && item.querySelector(".cond").textContent),
          odds: oddsText(item),
        }))
      ),
    };
  };

  const parseCorrectScore = (row) => {
    const cells = [...row.querySelectorAll(".odds")].filter((el) => el.querySelector(".crsSel"));
    if (!cells.length) return null;
    return {
      kind: "correct_score",
      selections: cells.map((cell) => ({
        score: clean(cell.querySelector(".crsSel") && cell.querySelector(".crsSel").textContent),
        odds: oddsText(cell),
      })),
    };
  };

  const parseLabeledGoals = (row) => {
    const items = [...row.querySelectorAll(".odds > div, .oddsTTG > div, .oddsHFT > div")].filter((el) =>
      el.querySelector(".goals-number")
    );
    if (!items.length) return null;
    return {
      kind: "labeled",
      selections: items.map((item) => ({
        label: clean(item.querySelector(".goals-number") && item.querySelector(".goals-number").textContent),
        odds: oddsText(item),
      })),
    };
  };

  const parseSimple = (row) => ({
    kind: "simple",
    labels: [...row.querySelectorAll(".team span")].map((el) => clean(el.textContent)).filter(Boolean),
    values: [...row.querySelectorAll(".oddsCheckboxGrid")].map((grid) => oddsText(grid)).filter(Boolean),
  });

  const panel = document.querySelector(".LAST_ODDS");
  if (!panel) return { prematch: [], inplay: [] };

  const prematch = [];
  const inplay = [];
  const children = [...panel.children];
  for (let i = 0; i < children.length; i++) {
    const node = children[i];
    if (!node.classList || !node.classList.contains("coupon")) continue;
    const desc = clean(node.querySelector(".desc") && node.querySelector(".desc").textContent);
    if (!desc) continue;
    const row = children[i + 1];
    if (!row || !row.classList.contains("match-row-container")) continue;

    let parsed =
      parseLineBlocks(row) ||
      parseHandicap(row) ||
      parseCorrectScore(row) ||
      parseLabeledGoals(row) ||
      parseSimple(row);
    const item = { name: desc, base_name: baseName(desc), ...parsed };
    if (isInplay(desc)) inplay.push(item);
    else prematch.push(item);
  }
  return { prematch, inplay };
}
"""


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def dump_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def load_progress():
    empty = {"completed": [], "pending": [], "updated_at": None, "failed": {}}
    if not PROGRESS_FILE.exists():
        return empty
    try:
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"progress.json 讀取失敗，會重新建立：{exc}")
        return empty
    data.setdefault("completed", [])
    data.setdefault("pending", [])
    data.setdefault("updated_at", None)
    data.setdefault("failed", {})
    return data


def save_progress(progress):
    progress["updated_at"] = now_text()
    progress["completed"] = list(dict.fromkeys(progress["completed"]))
    progress["pending"] = list(dict.fromkeys(progress["pending"]))
    dump_json(PROGRESS_FILE, progress)


def empty_backfill_progress():
    return {
        "from_date": None,
        "to_date": None,
        "next_date": None,
        "updated_at": None,
        "days": {},
        "completed_match_ids": [],
        "failed": {},
        "stopped_reason": None,
    }


def load_backfill_progress():
    if not BACKFILL_PROGRESS_FILE.exists():
        return empty_backfill_progress()
    try:
        data = json.loads(BACKFILL_PROGRESS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"backfill_progress.json 讀取失敗，會重新建立：{exc}")
        return empty_backfill_progress()
    base = empty_backfill_progress()
    base.update(data or {})
    base.setdefault("days", {})
    base.setdefault("completed_match_ids", [])
    base.setdefault("failed", {})
    return base


def save_backfill_progress(progress):
    progress["updated_at"] = now_text()
    progress["completed_match_ids"] = list(dict.fromkeys(progress.get("completed_match_ids") or []))
    dump_json(BACKFILL_PROGRESS_FILE, progress)


def to_float(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def strip_brackets(value):
    text = (value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        return text[1:-1].strip()
    return text


def keep_line_text(value):
    """保留盤口原文（含中括號）。"""
    return (value or "").strip()


def normalize_score(score):
    text = re.sub(r"\s+", "", score or "")
    if re.fullmatch(r"\d+:\d+", text):
        return text
    return ""


def split_teams(teams):
    text = " ".join((teams or "").split())
    for separator in (" 對 ", "對"):
        if separator in text:
            home, away = text.split(separator, 1)
            return home.strip(), away.strip()
    return "", ""


def parse_kickoff(value):
    if not value:
        return None, None
    try:
        kickoff = datetime.fromisoformat(value)
    except ValueError:
        return None, None
    return kickoff.strftime("%Y-%m-%d"), kickoff.strftime("%H:%M")


def parse_date_cell(value):
    found = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", (value or "").strip())
    if not found:
        return None
    day, month, year = found.groups()
    return f"{year}-{month}-{day}"


def parse_iso_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def daterange(start, end):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def remember_schedule(response, responses):
    if "kickOffTime" not in response.url:
        return
    responses.append(response)


def read_schedule(responses):
    schedule = {}
    for response in responses:
        try:
            payload = response.json()
        except Exception:
            continue
        matches = ((payload or {}).get("data") or {}).get("matches") or []
        for item in matches:
            match_id = item.get("frontEndId")
            match_date, match_time = parse_kickoff(item.get("kickOffTime"))
            if match_id and match_date and match_time:
                schedule[match_id] = {"match_date": match_date, "match_time": match_time}
    return schedule


def schedule_for_match(match_element, match_id, schedule):
    saved = schedule.get(match_id, {})
    match_date = saved.get("match_date")
    match_time = saved.get("match_time")
    if not match_date:
        match_date = parse_date_cell(_text(match_element.locator(".date-cell")))
    if not match_date:
        testid = match_element.get_attribute("data-testid") or ""
        found = re.search(r"_(\d{4})(\d{2})(\d{2})$", testid)
        if found:
            match_date = "-".join(found.groups())
    return match_date, match_time


def list_finished_matches(page, schedule):
    matches = page.locator(MATCH_INFO_SELECTOR)
    total = matches.count()
    finished = []
    seen = set()
    for index in range(total):
        data = extract_match_data(matches.nth(index))
        if not data or data["match_id"] in seen:
            continue
        full_time = normalize_score(data["full_time_score"])
        if not full_time:
            continue
        home, away = split_teams(data["teams"])
        match_date, match_time = schedule_for_match(matches.nth(index), data["match_id"], schedule)
        seen.add(data["match_id"])
        finished.append(
            {
                "match_id": data["match_id"],
                "match_date": match_date,
                "match_time": match_time,
                "home_team": home,
                "away_team": away,
                "half_time_score": normalize_score(data["half_time_score"]) or None,
                "full_time_score": full_time,
            }
        )
    return finished


def build_simple(market):
    values = (market or {}).get("values") or []
    if len(values) < 3:
        return None
    home, draw, away = to_float(values[0]), to_float(values[1]), to_float(values[2])
    if None in (home, draw, away):
        return None
    return {"主勝": home, "和": draw, "客勝": away}


def build_handicap_1x2(market):
    rows = []
    for line in (market or {}).get("lines") or []:
        if len(line) < 3:
            continue
        home_line = strip_brackets(line[0].get("cond"))
        home_line_raw = keep_line_text(line[0].get("cond"))
        home = to_float(line[0].get("odds"))
        draw = to_float(line[1].get("odds"))
        away = to_float(line[2].get("odds"))
        if not home_line or None in (home, draw, away):
            continue
        rows.append(
            {
                "主隊讓球": home_line,
                "主隊讓球_原文": home_line_raw,
                "主勝": home,
                "和": draw,
                "客勝": away,
            }
        )
    return rows or None


def build_handicap(market):
    rows = []
    for line in (market or {}).get("lines") or []:
        if len(line) < 2:
            continue
        home_line = strip_brackets(line[0].get("cond"))
        home_line_raw = keep_line_text(line[0].get("cond"))
        away_line_raw = keep_line_text(line[1].get("cond"))
        home = to_float(line[0].get("odds"))
        away = to_float(line[1].get("odds"))
        if not home_line or None in (home, away):
            continue
        rows.append(
            {
                "主隊讓球": home_line,
                "主隊讓球_原文": home_line_raw,
                "客隊讓球_原文": away_line_raw,
                "主勝": home,
                "客勝": away,
            }
        )
    return rows or None


def build_line(market):
    rows = []
    for item in (market or {}).get("lines") or []:
        values = item.get("values") or []
        if len(values) < 2:
            continue
        line_raw = keep_line_text(item.get("line"))
        line = strip_brackets(item.get("line"))
        over = to_float(values[0])
        under = to_float(values[1])
        if not line or None in (over, under):
            continue
        rows.append({"盤口": line, "盤口_原文": line_raw, "大": over, "細": under})
    return rows or None


def convert_market(name, market):
    if not market:
        return None
    if name in SIMPLE_MARKETS:
        return build_simple(market)
    if name in HANDICAP_1X2_MARKETS:
        return build_handicap_1x2(market)
    if name in HANDICAP_MARKETS:
        return build_handicap(market)
    if name in LINE_MARKETS:
        return build_line(market)
    return None


def side_from_team_label(team, side_attr):
    text = f"{team} {side_attr}".lower()
    if "away" in text or "客隊" in team:
        return "away"
    if "home" in text or "主隊" in team:
        return "home"
    return "unknown"


def build_team_totals(market):
    if not market or market.get("kind") != "team_line":
        return None
    result = {"主隊": [], "客隊": [], "其他": []}
    for block in market.get("teams") or []:
        rows = []
        for item in block.get("lines") or []:
            values = item.get("values") or []
            if len(values) < 2:
                continue
            line_raw = keep_line_text(item.get("line"))
            over = to_float(values[0])
            under = to_float(values[1])
            if not line_raw or None in (over, under):
                continue
            rows.append({"盤口_原文": line_raw, "盤口": strip_brackets(line_raw), "大": over, "細": under})
        if not rows:
            continue
        side = side_from_team_label(block.get("team") or "", block.get("side") or "")
        payload = {"隊伍": block.get("team"), "盤口": rows}
        if side == "home":
            result["主隊"].append(payload)
        elif side == "away":
            result["客隊"].append(payload)
        else:
            result["其他"].append(payload)
    if not any(result.values()):
        return None
    return {
        "主隊": (result["主隊"][0] if result["主隊"] else None),
        "客隊": (result["客隊"][0] if result["客隊"] else None),
        "其他": result["其他"] or None,
    }


def build_correct_score(market):
    if not market:
        return None
    selections = market.get("selections") or []
    rows = []
    for item in selections:
        score = keep_line_text(item.get("score"))
        odds = to_float(item.get("odds"))
        if not score or odds is None:
            continue
        rows.append({"比分": score, "賠率": odds})
    return rows or None


def build_labeled_map(market):
    if not market:
        return None
    if market.get("kind") == "labeled":
        mapping = {}
        for item in market.get("selections") or []:
            label = keep_line_text(item.get("label"))
            odds = to_float(item.get("odds"))
            if label and odds is not None:
                mapping[label] = odds
        return mapping or None
    if market.get("kind") == "simple":
        labels = market.get("labels") or []
        values = market.get("values") or []
        mapping = {}
        for index, label in enumerate(labels):
            if index >= len(values):
                break
            odds = to_float(values[index])
            if label and odds is not None:
                mapping[label] = odds
        return mapping or None
    return None


def build_first_team_to_score(market):
    mapping = build_labeled_map(market)
    if not mapping:
        return None
    values = list(mapping.values())
    labels = list(mapping.keys())
    if len(values) >= 3 and any("無入球" in label for label in labels):
        return {"主隊": values[0], "無入球": values[1], "客隊": values[2], "原文": mapping}
    return mapping


def build_same_game_parlay(market):
    if not market:
        return None
    values = [to_float(v) for v in (market.get("values") or [])]
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {"賠率": values, "原文名稱": market.get("name")}


def index_markets(markets):
    by_base = {}
    for market in markets or []:
        key = market.get("base_name") or market.get("name")
        if key and key not in by_base:
            by_base[key] = market
    return by_base


def build_additional_markets(by_base):
    additional = {
        "team_totals": build_team_totals(by_base.get("球隊入球大細")),
        "first_half_team_totals": build_team_totals(by_base.get("球隊半場入球大細")),
        "correct_score": build_correct_score(by_base.get("波膽")),
        "first_half_correct_score": build_correct_score(by_base.get("半場波膽")),
        "first_team_to_score": build_first_team_to_score(by_base.get("第一隊入球")),
        "total_goals": build_labeled_map(by_base.get("總入球")),
        "odd_even": build_labeled_map(by_base.get("入球單雙")),
        "half_full_time": build_labeled_map(by_base.get("半全場")),
        "same_game_parlay": build_same_game_parlay(by_base.get("同場過關")),
    }
    return additional


def build_inplay_last_odds(inplay_markets):
    if not inplay_markets:
        return None
    by_base = index_markets(inplay_markets)
    payload = {
        "odds_phase": "inplay_last_odds",
        "markets": {},
    }
    for name in TARGET_MARKETS:
        page_name = MARKET_PAGE_NAMES[name]
        payload["markets"][name] = convert_market(name, by_base.get(page_name))
    payload["additional_markets"] = build_additional_markets(by_base)
    # 保留原始 coupon 名稱，方便核對
    payload["raw_names"] = [item.get("name") for item in inplay_markets]
    if not any(payload["markets"].values()) and not any(payload["additional_markets"].values()):
        return {"odds_phase": "inplay_last_odds", "raw_names": payload["raw_names"], "markets": payload["markets"], "additional_markets": payload["additional_markets"]}
    return payload


def completeness_for(record):
    core = []
    missing = []
    for name in TARGET_MARKETS:
        if record.get(name) is None:
            missing.append(name)
            core.append(False)
        else:
            core.append(True)
    additional = record.get("additional_markets") or {}
    add_flags = []
    for key in ADDITIONAL_MARKET_KEYS:
        value = additional.get(key)
        present = value is not None
        add_flags.append(present)
        if not present:
            missing.append(key)
    if all(core):
        core_state = "full"
    elif any(core):
        core_state = "partial"
    else:
        core_state = "none"
    if all(add_flags):
        add_state = "full"
    elif any(add_flags):
        add_state = "partial"
    else:
        add_state = "none"
    return {
        "core_eight": core_state,
        "additional": add_state,
        "missing_markets": missing,
    }


def richness_score(record):
    if not record:
        return -1
    score = 0
    for name in TARGET_MARKETS:
        if record.get(name) is not None:
            score += 2
    additional = record.get("additional_markets") or {}
    for key in ADDITIONAL_MARKET_KEYS:
        if additional.get(key) is not None:
            score += 1
    if record.get("inplay_last_odds"):
        score += 1
    if record.get("full_time_score"):
        score += 3
    if record.get("half_time_score"):
        score += 1
    return score


def build_record(info, extracted):
    prematch = (extracted or {}).get("prematch") or []
    inplay = (extracted or {}).get("inplay") or []
    by_name = {market["name"]: market for market in prematch}
    by_base = index_markets(prematch)

    record = {
        "match_id": info["match_id"],
        "match_date": info.get("match_date"),
        "match_time": info.get("match_time"),
        "home_team": info["home_team"],
        "away_team": info["away_team"],
        "half_time_score": info["half_time_score"],
        "full_time_score": info["full_time_score"],
    }
    for name in TARGET_MARKETS:
        page_name = MARKET_PAGE_NAMES[name]
        record[name] = convert_market(name, by_name.get(page_name) or by_base.get(page_name))

    record["schema_version"] = SCHEMA_VERSION
    record["source"] = SOURCE
    record["collected_at"] = now_iso()
    record["odds_phase"] = "prematch_last_odds"
    record["additional_markets"] = build_additional_markets(by_base)
    record["inplay_last_odds"] = build_inplay_last_odds(inplay)
    record["data_completeness"] = completeness_for(record)
    return record


def prefer_value(old, new):
    if new is None:
        return old
    if old is None:
        return new
    return new


def prefer_score(old, new):
    old_norm = normalize_score(old) if isinstance(old, str) else old
    new_norm = normalize_score(new) if isinstance(new, str) else new
    if new_norm and not old_norm:
        return new
    if old_norm and not new_norm:
        return old
    if old_norm and new_norm and old_norm != new_norm:
        # 賽果更正：保留較新一次，但標註由呼叫端處理
        return new
    return prefer_value(old, new)


def merge_records(existing, incoming, *, allow_score_update=True):
    if not existing:
        return incoming, {"created": True}
    if not incoming:
        return existing, {"unchanged": True}

    notes = {}
    merged = dict(existing)

    for key in ("match_id", "match_date", "match_time", "home_team", "away_team"):
        if incoming.get(key):
            merged[key] = incoming[key]

    old_ft = existing.get("full_time_score")
    new_ft = incoming.get("full_time_score")
    if allow_score_update:
        if normalize_score(old_ft or "") and normalize_score(new_ft or "") and normalize_score(old_ft) != normalize_score(new_ft):
            notes["score_correction"] = {"from": old_ft, "to": new_ft}
            merged["full_time_score"] = new_ft
            merged["half_time_score"] = prefer_score(existing.get("half_time_score"), incoming.get("half_time_score"))
        else:
            merged["full_time_score"] = prefer_score(old_ft, new_ft)
            merged["half_time_score"] = prefer_score(existing.get("half_time_score"), incoming.get("half_time_score"))
    else:
        merged["full_time_score"] = old_ft or new_ft
        merged["half_time_score"] = existing.get("half_time_score") or incoming.get("half_time_score")

    for name in TARGET_MARKETS:
        if existing.get(name) is None and incoming.get(name) is not None:
            merged[name] = incoming[name]
        elif existing.get(name) is not None and incoming.get(name) is None:
            merged[name] = existing[name]
        elif incoming.get(name) is not None:
            # 兩邊都有：若新資料更豐富先用新，否則保留舊
            if richness_score({name: incoming[name]}) >= richness_score({name: existing[name]}):
                merged[name] = incoming[name]
            else:
                merged[name] = existing[name]

    old_add = existing.get("additional_markets") or {}
    new_add = incoming.get("additional_markets") or {}
    merged_add = dict(old_add)
    for key in ADDITIONAL_MARKET_KEYS:
        if merged_add.get(key) is None and new_add.get(key) is not None:
            merged_add[key] = new_add[key]
        elif merged_add.get(key) is not None and new_add.get(key) is None:
            continue
        elif new_add.get(key) is not None:
            merged_add[key] = new_add[key]
    merged["additional_markets"] = merged_add or None

    if existing.get("inplay_last_odds") is None and incoming.get("inplay_last_odds") is not None:
        merged["inplay_last_odds"] = incoming["inplay_last_odds"]
    elif incoming.get("inplay_last_odds") is not None and existing.get("inplay_last_odds") is not None:
        merged["inplay_last_odds"] = incoming["inplay_last_odds"]
    else:
        merged["inplay_last_odds"] = existing.get("inplay_last_odds")

    # 唔好用較空嘅整體資料覆蓋較齊嘅舊檔
    if richness_score(incoming) < richness_score(existing) and not notes.get("score_correction"):
        # 仍然允許補缺欄
        if richness_score(merged) < richness_score(existing):
            return existing, {"kept_richer_existing": True}

    for key in ("schema_version", "source", "collected_at", "odds_phase", "backfill_metadata"):
        if incoming.get(key) is not None:
            merged[key] = incoming[key]
        elif key not in merged and existing.get(key) is not None:
            merged[key] = existing[key]

    merged["schema_version"] = max(int(existing.get("schema_version") or 1), int(incoming.get("schema_version") or 1), SCHEMA_VERSION)
    merged["data_completeness"] = completeness_for(merged)
    notes["merged"] = True
    return merged, notes


def record_is_complete_enough(record):
    if not record:
        return False
    if int(record.get("schema_version") or 1) < SCHEMA_VERSION:
        return False
    completeness = record.get("data_completeness") or completeness_for(record)
    if completeness.get("core_eight") == "none":
        return False
    # 有 schema 2 同埋至少試過 additional_markets 結構就當可 skip（即使部分 null）
    return "additional_markets" in record


def scrape_match(page, info):
    match = find_match(page, info["match_id"])
    if match is None:
        raise RuntimeError("賽果頁搵唔到呢場")

    match.scroll_into_view_if_needed()
    time.sleep(1)

    odds_button = match.locator(".detail-btn", has_text="%")
    if odds_button.count() == 0:
        raise RuntimeError("搵唔到賠率掣（%）")

    print("撳開最後賠率...")
    odds_button.first.click()
    page.wait_for_selector(".LAST_ODDS", timeout=15000)
    time.sleep(random.uniform(2, 3))

    extracted = page.evaluate(EXTRACT_LAST_ODDS_JS)
    record = build_record(info, extracted)
    try:
        print("返回賽果頁...")
        return_to_results(page)
    except Exception as exc:
        print(f"返回賽果頁失敗，改為重新載入：{exc}")
        page.goto(RESULTS_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector(MATCH_INFO_SELECTOR, timeout=20000)
    return record


def recover_results_page(page):
    if page.is_closed():
        return False
    try:
        return_to_results(page)
    except Exception:
        page.goto(RESULTS_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector(MATCH_INFO_SELECTOR, timeout=20000)
    return True


def with_schedule(record, info):
    updated = {
        "match_id": record.get("match_id") or info["match_id"],
        "match_date": info.get("match_date"),
        "match_time": info.get("match_time"),
        "home_team": record.get("home_team"),
        "away_team": record.get("away_team"),
        "half_time_score": record.get("half_time_score"),
        "full_time_score": record.get("full_time_score"),
    }
    for key, value in record.items():
        if key not in updated:
            updated[key] = value
    return updated


def backfill_saved_matches(finished):
    updated = 0
    missing_time = []
    for info in finished:
        path = RESULTS_DIR / f"{info['match_id']}.json"
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        revised = with_schedule(record, info)
        if revised == record:
            continue
        dump_json(path, revised)
        updated += 1
        if not info.get("match_time"):
            missing_time.append(info["match_id"])
    return updated, missing_time


def write_all_results(ordered_ids=None):
    rows = []
    used = set()
    for match_id in ordered_ids or []:
        path = RESULTS_DIR / f"{match_id}.json"
        if not path.exists():
            continue
        rows.append(json.loads(path.read_text(encoding="utf-8")))
        used.add(match_id)
    for path in sorted(RESULTS_DIR.glob("FB*.json"), key=lambda item: int(item.stem[2:])):
        if path.stem in used:
            continue
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    dump_json(ALL_RESULTS_FILE, rows)
    return len(rows)


def save_match_record(path, incoming, *, backfill_meta=None):
    existing = None
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    if backfill_meta:
        incoming = dict(incoming)
        incoming["backfill_metadata"] = backfill_meta
    merged, notes = merge_records(existing, incoming)
    dump_json(path, merged)
    return merged, notes


def chinese_month(value):
    return f"{value}月"


def english_ordinal(day):
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def aria_day_label(value):
    return value.strftime(f"Choose %A, %B {english_ordinal(value.day)}, %Y")


def expand_search_box(page):
    page.evaluate(
        """() => {
          const body = document.querySelector('.fb-results-searchbox-body');
          if (body) body.classList.remove('searchbox-body-hide');
          const header = document.querySelector('.fb-search-title, .searchBoxExpand');
          if (header && body && body.classList.contains('searchbox-body-hide')) header.click();
        }"""
    )
    page.wait_for_timeout(200)


def goto_calendar_month(page, target):
    target_month = chinese_month(target.month)
    target_year = str(target.year)
    for _ in range(240):
        label = page.evaluate(
            """() => [
              document.querySelector('.dateRangePickerMonth')?.innerText || '',
              document.querySelector('.dateRangePickerYear')?.innerText || ''
            ]"""
        )
        if label == [target_month, target_year]:
            return True
        current_year = int(label[1]) if label[1].isdigit() else target.year
        current_month = 0
        found = re.match(r"(\d+)月", label[0] or "")
        if found:
            current_month = int(found.group(1))
        current_index = current_year * 12 + current_month
        target_index = target.year * 12 + target.month
        if current_index > target_index:
            page.evaluate("() => document.querySelector('.prev-month')?.click()")
        else:
            page.evaluate("() => document.querySelector('.next-month')?.click()")
        page.wait_for_timeout(60)
    return False


def select_date_range(page, start, end):
    expand_search_box(page)
    page.locator(".date-input").click(force=True)
    page.wait_for_timeout(300)
    if not goto_calendar_month(page, start):
        raise RuntimeError(f"日曆搵唔到 {start}")
    page.locator(f'[aria-label="{aria_day_label(start)}"]').click(force=True)
    if end != start:
        if end.month != start.month or end.year != start.year:
            if not goto_calendar_month(page, end):
                raise RuntimeError(f"日曆搵唔到 {end}")
        page.locator(f'[aria-label="{aria_day_label(end)}"]').click(force=True)
    else:
        page.locator(f'[aria-label="{aria_day_label(start)}"]').click(force=True)
    page.evaluate(
        """() => {
          const node = [...document.querySelectorAll('button, div, span')]
            .find((item) => (item.textContent || '').trim() === '確定');
          if (node) node.click();
        }"""
    )
    page.wait_for_timeout(200)


def click_results_search(page):
    button = page.locator('[data-testid="matchResults_search_button"]')
    if button.count() == 0:
        raise RuntimeError("搵唔到賽果搜尋掣")
    with page.expect_response(lambda response: "matchNumByDate" in response.url, timeout=30000) as caught:
        button.first.evaluate("node => node.click()")
    payload = caught.value.json()
    page.wait_for_timeout(800)
    return payload


def read_search_total(payload):
    data = (payload or {}).get("data") or {}
    total = (data.get("matchNumByDate") or {}).get("total")
    return int(total or 0)


def list_page_numbers(page):
    return page.evaluate(
        """() => {
          const footer = [...document.querySelectorAll('div, span')]
            .map(n => (n.innerText || '').replace(/\\s+/g, ' ').trim())
            .find(t => t.includes('本頁顯示') && t.includes('共找到'));
          const buttons = [...document.querySelectorAll('button, a, div')]
            .map(n => (n.innerText || '').trim())
            .filter(t => /^\\d+$/.test(t));
          return { footer: footer || '', pages: [...new Set(buttons)].map(Number).filter(n => n > 0).sort((a,b)=>a-b) };
        }"""
    )


def open_results_search_day(page, day):
    page.goto(f"{RESULTS_URL}#search", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_selector(".date-input", timeout=20000)
    time.sleep(1)
    select_date_range(page, day, day)
    payload = click_results_search(page)
    total = read_search_total(payload)
    # 等 DOM；零場都要明確等一陣，避免未載入就當冇賽事
    deadline = time.time() + 8
    while time.time() < deadline:
        count = page.locator(MATCH_INFO_SELECTOR).count()
        if total == 0:
            page.wait_for_timeout(500)
            break
        if count > 0:
            break
        page.wait_for_timeout(400)
    return total


def collect_finished_on_current_list(page, schedule, forced_date=None):
    finished = list_finished_matches(page, schedule)
    if forced_date:
        for item in finished:
            item["match_date"] = item.get("match_date") or forced_date.isoformat()
    return finished


def go_results_page_number(page, number):
    clicked = page.evaluate(
        """(number) => {
          const nodes = [...document.querySelectorAll('button, a, div, span')];
          const target = nodes.find((node) => (node.innerText || '').trim() === String(number));
          if (!target) return false;
          target.click();
          return true;
        }""",
        number,
    )
    if not clicked:
        return False
    page.wait_for_timeout(1000)
    return True


def collect_day_matches(page, day, schedule_responses):
    total = open_results_search_day(page, day)
    if total == 0:
        # 再確認一次 DOM，避免誤判
        page.wait_for_timeout(1000)
        if page.locator(MATCH_INFO_SELECTOR).count() == 0:
            return [], 0

    schedule = read_schedule(schedule_responses)
    collected = {}
    page_info = list_page_numbers(page)
    page_numbers = page_info.get("pages") or [1]
    if 1 not in page_numbers:
        page_numbers = [1] + page_numbers

    for number in page_numbers:
        if number != 1:
            if not go_results_page_number(page, number):
                print(f"{day.isoformat()} 第 {number} 頁撳唔到，停止翻頁")
                break
            page.wait_for_timeout(700)
        schedule = read_schedule(schedule_responses)
        for item in collect_finished_on_current_list(page, schedule, forced_date=day):
            collected[item["match_id"]] = item

    # 若 API 總數明顯多過已收集，嘗試繼續撳未見過頁碼
    guard = 0
    while len(collected) < total and guard < 50:
        guard += 1
        page_info = list_page_numbers(page)
        unseen = [n for n in (page_info.get("pages") or []) if n not in page_numbers]
        if not unseen:
            break
        for number in unseen:
            page_numbers.append(number)
            if not go_results_page_number(page, number):
                continue
            schedule = read_schedule(schedule_responses)
            for item in collect_finished_on_current_list(page, schedule, forced_date=day):
                collected[item["match_id"]] = item

    return list(collected.values()), total


def run_daily(headless=False):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    progress = load_progress()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        schedule_responses = []
        page.on("response", lambda response: remember_schedule(response, schedule_responses))
        try:
            print("正在前往馬會賽果頁面...")
            page.goto(RESULTS_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_selector(MATCH_INFO_SELECTOR, timeout=20000)
            time.sleep(1)
            print(f"頁面標題：{page.title()}")

            schedule = read_schedule(schedule_responses)
            print(f"由賽果資料讀到 {len(schedule)} 場開波時間")
            finished = list_finished_matches(page, schedule)
            finished_ids = [item["match_id"] for item in finished]
            completed = [match_id for match_id in progress["completed"] if match_id in finished_ids]
            completed_set = set(completed)
            pending = [match_id for match_id in finished_ids if match_id not in completed_set]
            progress["completed"] = completed
            progress["pending"] = pending
            progress["failed"] = {
                match_id: message
                for match_id, message in progress.get("failed", {}).items()
                if match_id in pending
            }
            save_progress(progress)

            total = len(finished)
            updated, missing_time = backfill_saved_matches(finished)
            if updated:
                print(f"已為 {updated} 場已保存賽事補上日期同時間")
            if missing_time:
                print(f"有 {len(missing_time)} 場搵唔到開波時間")
            print(f"完場賽事 {total} 場，已完成 {len(completed)} 場，今次處理 {len(pending)} 場")
            if not pending:
                count = write_all_results(finished_ids)
                print(f"全部已經完成，合併 {count} 場到 {ALL_RESULTS_FILE.name}")
                return

            info_by_id = {item["match_id"]: item for item in finished}
            position = {match_id: index for index, match_id in enumerate(finished_ids, start=1)}

            for offset, match_id in enumerate(pending):
                if page.is_closed():
                    print("瀏覽器已關閉，停止。已完成嘅場次已寫入 progress.json")
                    break

                info = info_by_id[match_id]
                half_time = info["half_time_score"] or "-"
                print(
                    f"\n[{position[match_id]}/{total}] {match_id} "
                    f"{info.get('match_date') or '-'} {info.get('match_time') or '-'} "
                    f"{info['home_team']} 對 {info['away_team']} "
                    f"半場 {half_time} 全場 {info['full_time_score']}"
                )
                try:
                    record = scrape_match(page, info)
                    save_match_record(RESULTS_DIR / f"{match_id}.json", record)
                    if match_id not in progress["completed"]:
                        progress["completed"].append(match_id)
                    progress["pending"] = [item for item in progress["pending"] if item != match_id]
                    progress["failed"].pop(match_id, None)
                    save_progress(progress)
                    print(f"[{position[match_id]}/{total}] {match_id} 完成")
                except Exception as exc:
                    message = str(exc).strip() or exc.__class__.__name__
                    progress["failed"][match_id] = message
                    save_progress(progress)
                    print(f"[{position[match_id]}/{total}] {match_id} 失敗：{message}")
                    if not recover_results_page(page):
                        print("瀏覽器已關閉，停止。已完成嘅場次已寫入 progress.json")
                        break

                if offset < len(pending) - 1:
                    print(f"停 {PAUSE_BETWEEN_MATCHES} 秒，再處理下一場...")
                    time.sleep(PAUSE_BETWEEN_MATCHES)

            progress = load_progress()
            if not progress["pending"]:
                count = write_all_results(finished_ids)
                print(f"\n全部完成，合併 {count} 場到 {ALL_RESULTS_FILE.name}")
            else:
                print(f"\n未完成 {len(progress['pending'])} 場，下次啟動會繼續")
        finally:
            if browser.is_connected():
                browser.close()
            print("程式結束")


def run_backfill(start, end, *, headless=False, max_matches=None, merge_all=False, live_approved=False):
    if not live_approved:
        raise SystemExit(
            "歷史模式需要明確批准先可連線馬會。"
            "請加 --live-approved 之後先跑。"
            "正式長跑前請先用本機測試：python -m pytest tests/test_hkjc_full.py"
        )
    if start > end:
        raise SystemExit("--from 不可以大於 --to")
    if (end - start).days > 31:
        print("注意：馬會搜尋每次最多 31 日；本模式會逐日搜尋，唔會一次揀超過一日。")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    progress = load_backfill_progress()
    progress["from_date"] = start.isoformat()
    progress["to_date"] = end.isoformat()
    if not progress.get("next_date"):
        progress["next_date"] = start.isoformat()
    else:
        # 續跑：由較後嘅 next_date 開始，但仍受今次 --from/--to 限制
        next_date = parse_iso_date(progress["next_date"])
        if next_date < start:
            progress["next_date"] = start.isoformat()
        if next_date > end:
            print(f"進度已到 {progress['next_date']}，今次範圍 {start}..{end} 無需再跑")
            return

    processed = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        schedule_responses = []
        page.on("response", lambda response: remember_schedule(response, schedule_responses))
        try:
            current = parse_iso_date(progress["next_date"])
            if current < start:
                current = start
            while current <= end:
                day_key = current.isoformat()
                day_stats = progress["days"].setdefault(
                    day_key,
                    {"found": 0, "success": 0, "failed": 0, "skipped": 0, "missing": 0, "status": "pending"},
                )
                print(f"\n===== 歷史補數 {day_key} =====")
                try:
                    matches, api_total = collect_day_matches(page, current, schedule_responses)
                except Exception as exc:
                    message = str(exc).strip() or exc.__class__.__name__
                    day_stats["status"] = "search_failed"
                    day_stats["error"] = message
                    progress["stopped_reason"] = f"{day_key}: {message}"
                    save_backfill_progress(progress)
                    print(f"{day_key} 搜尋失敗：{message}")
                    print("已停止。檢查網站限制後再續跑。")
                    return

                day_stats["found"] = len(matches)
                day_stats["api_total"] = api_total
                if api_total == 0 and not matches:
                    day_stats["status"] = "empty"
                    day_stats["missing"] = 0
                    progress["next_date"] = (current + timedelta(days=1)).isoformat()
                    save_backfill_progress(progress)
                    print(f"{day_key} 冇賽事")
                    current += timedelta(days=1)
                    continue

                for index, info in enumerate(matches, start=1):
                    if max_matches is not None and processed >= max_matches:
                        progress["next_date"] = current.isoformat()
                        progress["stopped_reason"] = "reached_max_matches"
                        save_backfill_progress(progress)
                        print(f"已達 --max-matches {max_matches}，停止。下次由 {current.isoformat()} 續跑")
                        return

                    match_id = info["match_id"]
                    path = RESULTS_DIR / f"{match_id}.json"
                    existing = None
                    if path.exists():
                        existing = json.loads(path.read_text(encoding="utf-8"))
                        if record_is_complete_enough(existing):
                            day_stats["skipped"] += 1
                            if match_id not in progress["completed_match_ids"]:
                                progress["completed_match_ids"].append(match_id)
                            print(f"[{index}/{len(matches)}] {match_id} 已有完整資料，跳過")
                            continue

                    print(
                        f"[{index}/{len(matches)}] {match_id} "
                        f"{info.get('match_date')} {info.get('match_time') or '-'} "
                        f"{info['home_team']} 對 {info['away_team']}"
                    )
                    try:
                        # 搜尋後列表可能跨頁，確保回到有呢場嘅頁再 scrape
                        if find_match(page, match_id) is None:
                            # 重新打開當日並翻頁搵
                            collect_day_matches(page, current, schedule_responses)
                        record = scrape_match(page, info)
                        meta = {
                            "mode": "backfill",
                            "day": day_key,
                            "collected_at": now_iso(),
                        }
                        _, notes = save_match_record(path, record, backfill_meta=meta)
                        if notes.get("score_correction"):
                            print(f"  留意：比分更正 {notes['score_correction']}")
                        day_stats["success"] += 1
                        progress["failed"].pop(match_id, None)
                        if match_id not in progress["completed_match_ids"]:
                            progress["completed_match_ids"].append(match_id)
                        processed += 1
                        save_backfill_progress(progress)
                        print(f"[{index}/{len(matches)}] {match_id} 完成")
                    except Exception as exc:
                        message = str(exc).strip() or exc.__class__.__name__
                        day_stats["failed"] += 1
                        progress["failed"][match_id] = message
                        save_backfill_progress(progress)
                        print(f"[{index}/{len(matches)}] {match_id} 失敗：{message}")
                        if "拒絕" in message or "blocked" in message.lower() or "captcha" in message.lower():
                            progress["stopped_reason"] = message
                            save_backfill_progress(progress)
                            print("疑似網站限制，已停止。")
                            return
                        if not recover_results_page(page):
                            progress["stopped_reason"] = "browser_closed"
                            save_backfill_progress(progress)
                            return
                        # 返回後重新定位當日列表
                        try:
                            collect_day_matches(page, current, schedule_responses)
                        except Exception:
                            pass

                    print(f"停 {PAUSE_BETWEEN_MATCHES} 秒...")
                    time.sleep(PAUSE_BETWEEN_MATCHES)

                day_stats["status"] = "done"
                progress["next_date"] = (current + timedelta(days=1)).isoformat()
                progress["stopped_reason"] = None
                save_backfill_progress(progress)
                current += timedelta(days=1)

            if merge_all:
                count = write_all_results()
                print(f"合併 {count} 場到 {ALL_RESULTS_FILE.name}")
            print("歷史補數段落完成")
        finally:
            if browser.is_connected():
                browser.close()
            print("程式結束")


def build_parser():
    parser = argparse.ArgumentParser(description="HKJC 完場賽果爬蟲")
    parser.add_argument("--mode", choices=["daily", "backfill"], default="daily")
    parser.add_argument("--from", dest="date_from", help="歷史開始日期 YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="歷史結束日期 YYYY-MM-DD")
    parser.add_argument("--date", help="單一日期 YYYY-MM-DD")
    parser.add_argument("--month", help="指定月份 YYYY-MM")
    parser.add_argument("--max-matches", type=int, default=None, help="今次最多處理幾場（安全上限）")
    parser.add_argument("--merge-all", action="store_true", help="歷史段落完成後重建 all_results.json")
    parser.add_argument("--headless", action="store_true", help="無頭模式")
    parser.add_argument(
        "--live-approved",
        action="store_true",
        help="明確批准歷史模式連線馬會（正式長跑前請先跑本機測試）",
    )
    return parser


def resolve_backfill_range(args):
    if args.date:
        day = parse_iso_date(args.date)
        return day, day
    if args.month:
        year, month = args.month.split("-")
        start = date(int(year), int(month), 1)
        if month == "12":
            end = date(int(year), 12, 31)
        else:
            end = date(int(year), int(month) + 1, 1) - timedelta(days=1)
        return start, end
    start = parse_iso_date(args.date_from) if args.date_from else DEFAULT_BACKFILL_FROM
    end = parse_iso_date(args.date_to) if args.date_to else date.today()
    return start, end


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.mode == "daily":
        run_daily(headless=args.headless)
        return
    start, end = resolve_backfill_range(args)
    run_backfill(
        start,
        end,
        headless=args.headless,
        max_matches=args.max_matches,
        merge_all=args.merge_all,
        live_approved=args.live_approved,
    )


# 兼容舊呼叫
def run(headless=False):
    run_daily(headless=headless)


if __name__ == "__main__":
    main()
