import json
import random
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from hkjc_crawler import (
    EXTRACT_ODDS_JS,
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
ALL_RESULTS_FILE = BASE_DIR / "all_results.json"

SIMPLE_MARKETS = {"主客和", "半場主客和"}
HANDICAP_1X2_MARKETS = {"讓球主客和"}
HANDICAP_MARKETS = {"讓球"}
LINE_MARKETS = {"入球大細", "半場入球大細", "角球大細", "半場角球大細"}


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
    """kickOffTime 例子：2026-10-18T10:00:00.000+08:00。"""
    if not value:
        return None, None
    try:
        kickoff = datetime.fromisoformat(value)
    except ValueError:
        return None, None
    return kickoff.strftime("%Y-%m-%d"), kickoff.strftime("%H:%M")


def parse_date_cell(value):
    """賽果行日期格例子：18/10/2026。"""
    found = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", (value or "").strip())
    if not found:
        return None
    day, month, year = found.groups()
    return f"{year}-{month}-{day}"


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
        home = to_float(line[0].get("odds"))
        draw = to_float(line[1].get("odds"))
        away = to_float(line[2].get("odds"))
        if not home_line or None in (home, draw, away):
            continue
        rows.append({"主隊讓球": home_line, "主勝": home, "和": draw, "客勝": away})
    return rows or None


def build_handicap(market):
    rows = []
    for line in (market or {}).get("lines") or []:
        if len(line) < 2:
            continue
        home_line = strip_brackets(line[0].get("cond"))
        home = to_float(line[0].get("odds"))
        away = to_float(line[1].get("odds"))
        if not home_line or None in (home, away):
            continue
        rows.append({"主隊讓球": home_line, "主勝": home, "客勝": away})
    return rows or None


def build_line(market):
    rows = []
    for item in (market or {}).get("lines") or []:
        values = item.get("values") or []
        if len(values) < 2:
            continue
        line = strip_brackets(item.get("line"))
        over = to_float(values[0])
        under = to_float(values[1])
        if not line or None in (over, under):
            continue
        rows.append({"盤口": line, "大": over, "細": under})
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


def build_record(info, markets):
    by_name = {market["name"]: market for market in markets}
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
        record[name] = convert_market(name, by_name.get(MARKET_PAGE_NAMES[name]))
    return record


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

    markets = page.evaluate(EXTRACT_ODDS_JS)
    record = build_record(info, markets)
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


def write_all_results(ordered_ids):
    rows = []
    used = set()
    for match_id in ordered_ids:
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


def run(headless=False):
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
                    dump_json(RESULTS_DIR / f"{match_id}.json", record)
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
                    print("停 5 秒，再處理下一場...")
                    time.sleep(5)

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


def main():
    run(headless=False)


if __name__ == "__main__":
    main()
