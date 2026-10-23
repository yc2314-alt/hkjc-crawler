import json
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

HAD_URL = "https://bet.hkjc.com/ch/football/had"
HK = ZoneInfo("Asia/Hong_Kong")
# 包含今日剩餘場次，直到後日結束。23 號跑就收 23、24、25 號。
LOOKAHEAD_DAYS = 3
PAUSE_BETWEEN_MATCHES = 5

BASE_DIR = Path(__file__).resolve().parent
UPCOMING_DIR = BASE_DIR / "upcoming"

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
SIMPLE_CODES = {"HAD", "FHA"}
HANDICAP_1X2_CODES = {"HHA"}
HANDICAP_CODES = {"HDC"}
LINE_CODES = {"HIL", "FHL", "CHL", "FCH"}

LIST_JS = r"""
() => {
  const clean = (value) => (value || "").replace(/\s+/g, " ").trim();
  const textOf = (id) => {
    const el = document.querySelector(`[data-testid="${id}"]`);
    return clean(el ? el.textContent : "");
  };
  const oddsOf = (id, side) => {
    const el = document.querySelector(`[data-testid^="${id}_HAD_"][data-testid$="_${side}_odds"]`);
    return clean(el ? el.textContent : "");
  };
  return [...document.querySelectorAll('[data-testid$="_matchTime"]')].map((timeEl) => {
    const matchId = (timeEl.getAttribute("data-testid") || "").replace(/_matchTime$/, "");
    return {
      match_id: textOf(`${matchId}_frontID`) || matchId,
      deadline: clean(timeEl.textContent),
      home_team: textOf(`${matchId}_homeTeam`),
      away_team: textOf(`${matchId}_awayTeam`),
      home_odds: oddsOf(matchId, "H"),
      draw_odds: oddsOf(matchId, "D"),
      away_odds: oddsOf(matchId, "A"),
    };
  }).filter((row) => /^FB\d+$/.test(row.match_id));
}
"""

EXTRACT_JS = r"""
(matchId) => {
  const clean = (value) => (value || "").replace(/\s+/g, " ").trim();
  const map = {};
  for (const el of document.querySelectorAll(`[data-testid^="${matchId}_"]`)) {
    map[el.getAttribute("data-testid")] = clean(el.textContent);
  }

  const simple = (code) => {
    const groups = {};
    const pattern = new RegExp(`^${matchId}_${code}_(\\d+)_\\d+_(\\d+)_([HDA])_odds$`);
    for (const [key, value] of Object.entries(map)) {
      const found = key.match(pattern);
      if (!found || !value) continue;
      const groupId = `${found[1]}_${found[2]}`;
      groups[groupId] = groups[groupId] || { no: Number(found[1]), odds: {} };
      groups[groupId].odds[found[3]] = value;
    }
    const rows = Object.values(groups)
      .filter((row) => row.odds.H && row.odds.D && row.odds.A)
      .sort((a, b) => a.no - b.no);
    if (!rows.length) return null;
    return { home: rows[0].odds.H, draw: rows[0].odds.D, away: rows[0].odds.A };
  };

  const handicap = (code) => {
    const groups = {};
    const textPattern = new RegExp(`^${matchId}_${code}_(\\d+)_(\\d+)_([HDA])_text$`);
    const oddsPattern = new RegExp(`^${matchId}_${code}_(\\d+)_\\d+_(\\d+)_([HDA])_odds$`);
    for (const [key, value] of Object.entries(map)) {
      let found = key.match(textPattern);
      if (found) {
        const groupId = `${found[1]}_${found[2]}`;
        groups[groupId] = groups[groupId] || { no: Number(found[1]), cond: {}, odds: {} };
        groups[groupId].cond[found[3]] = value;
        continue;
      }
      found = key.match(oddsPattern);
      if (found && value) {
        const groupId = `${found[1]}_${found[2]}`;
        groups[groupId] = groups[groupId] || { no: Number(found[1]), cond: {}, odds: {} };
        groups[groupId].odds[found[3]] = value;
      }
    }
    return Object.values(groups).sort((a, b) => a.no - b.no);
  };

  const lines = (code) => {
    const groups = {};
    const linePattern = new RegExp(`^${matchId}_${code}_(\\d+)_(\\d+)_line$`);
    const highPattern = new RegExp(`^${matchId}_${code}_(\\d+)_1_(\\d+)_H_odds$`);
    const lowPattern = new RegExp(`^${matchId}_${code}_(\\d+)_2_(\\d+)_L_odds$`);
    for (const [key, value] of Object.entries(map)) {
      let found = key.match(linePattern);
      if (found) {
        const groupId = `${found[1]}_${found[2]}`;
        groups[groupId] = groups[groupId] || { no: Number(found[1]) };
        groups[groupId].line = value;
        continue;
      }
      found = key.match(highPattern);
      if (found && value) {
        const groupId = `${found[1]}_${found[2]}`;
        groups[groupId] = groups[groupId] || { no: Number(found[1]) };
        groups[groupId].high = value;
        continue;
      }
      found = key.match(lowPattern);
      if (found && value) {
        const groupId = `${found[1]}_${found[2]}`;
        groups[groupId] = groups[groupId] || { no: Number(found[1]) };
        groups[groupId].low = value;
      }
    }
    return Object.values(groups).sort((a, b) => a.no - b.no);
  };

  return {
    HAD: simple("HAD"),
    FHA: simple("FHA"),
    HHA: handicap("HHA"),
    HDC: handicap("HDC"),
    HIL: lines("HIL"),
    FHL: lines("FHL"),
    CHL: lines("CHL"),
    FCH: lines("FCH"),
  };
}
"""


def now_hk():
    return datetime.now(HK)


def dump_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


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


def parse_deadline(value):
    try:
        parsed = datetime.strptime(value, "%d/%m/%Y %H:%M")
    except ValueError:
        return None
    return parsed.replace(tzinfo=HK)


def within_window(deadline, now):
    if deadline <= now:
        return False
    last_day = (now + timedelta(days=LOOKAHEAD_DAYS - 1)).date()
    return deadline.date() <= last_day


def build_simple(market):
    if not market:
        return None
    home = to_float(market.get("home"))
    draw = to_float(market.get("draw"))
    away = to_float(market.get("away"))
    if None in (home, draw, away):
        return None
    return {"主勝": home, "和": draw, "客勝": away}


def build_handicap_1x2(lines):
    rows = []
    for line in lines or []:
        cond = line.get("cond") or {}
        odds = line.get("odds") or {}
        home_line = strip_brackets(cond.get("H"))
        home = to_float(odds.get("H"))
        draw = to_float(odds.get("D"))
        away = to_float(odds.get("A"))
        if not home_line or None in (home, draw, away):
            continue
        rows.append({"主隊讓球": home_line, "主勝": home, "和": draw, "客勝": away})
    return rows or None


def build_handicap(lines):
    rows = []
    for line in lines or []:
        cond = line.get("cond") or {}
        odds = line.get("odds") or {}
        home_line = strip_brackets(cond.get("H"))
        home = to_float(odds.get("H"))
        away = to_float(odds.get("A"))
        if not home_line or None in (home, away):
            continue
        rows.append({"主隊讓球": home_line, "主勝": home, "客勝": away})
    return rows or None


def build_line(lines):
    rows = []
    for line in lines or []:
        label = strip_brackets(line.get("line"))
        over = to_float(line.get("high"))
        under = to_float(line.get("low"))
        if not label or None in (over, under):
            continue
        rows.append({"盤口": label, "大": over, "細": under})
    return rows or None


def convert_market(code, raw):
    if code in SIMPLE_CODES:
        return build_simple(raw)
    if code in HANDICAP_1X2_CODES:
        return build_handicap_1x2(raw)
    if code in HANDICAP_CODES:
        return build_handicap(raw)
    if code in LINE_CODES:
        return build_line(raw)
    return None


def build_record(info, raw_markets):
    record = {
        "match_id": info["match_id"],
        "match_date": info["deadline"].strftime("%Y-%m-%d"),
        "kickoff": info["deadline"].strftime("%H:%M"),
        "timezone": "Asia/Hong_Kong",
        "home_team": info["home_team"],
        "away_team": info["away_team"],
        "status": "未開波",
        "updated_at": now_hk().strftime("%Y-%m-%d %H:%M:%S"),
    }
    for name, code in MARKET_CODES.items():
        record[name] = convert_market(code, raw_markets.get(code))
    return record


def scroll_list(page, distance):
    page.evaluate(
        """(distance) => {
          const start = document.querySelector('[data-testid$="_matchTime"]');
          let node = start;
          while (node && node.scrollHeight <= node.clientHeight + 20) node = node.parentElement;
          if (!node) return;
          if (distance == null) node.scrollTop = 0;
          else node.scrollTop += distance;
        }""",
        distance,
    )


def absorb_visible_rows(page, found):
    for row in page.evaluate(LIST_JS):
        found[row["match_id"]] = row


def collect_visible_block(page, found):
    scroll_list(page, None)
    page.wait_for_timeout(300)
    stable_rounds = 0
    last_count = -1
    for _ in range(60):
        absorb_visible_rows(page, found)
        if len(found) == last_count:
            stable_rounds += 1
            if stable_rounds >= 3:
                break
        else:
            stable_rounds = 0
            last_count = len(found)
        scroll_list(page, 700)
        page.wait_for_timeout(250)


def show_more(page):
    button = page.get_by_text("顯示更多", exact=True)
    if button.count() == 0:
        return False
    button.first.scroll_into_view_if_needed()
    button.first.click()
    page.wait_for_timeout(900)
    return True


def collect_rows(page, now):
    found = {}
    labels = [(now + timedelta(days=offset)).strftime("%d/%m") for offset in range(LOOKAHEAD_DAYS)]
    for label in labels:
        page.locator('[data-testid="date_button"]').click()
        page.wait_for_timeout(400)
        chip = page.locator("div.date", has_text=label)
        if chip.count() == 0:
            print(f"頁面冇 {label} 呢日")
            continue
        chip.first.click()
        page.wait_for_timeout(1000)
        before = len(found)
        for _ in range(6):
            collect_visible_block(page, found)
            if not show_more(page):
                break
        print(f"{label} 新增 {len(found) - before} 場，累計 {len(found)} 場")
    scroll_list(page, None)
    return list(found.values())


def select_matches(rows, now):
    selected = []
    for row in rows:
        deadline = parse_deadline(row.get("deadline"))
        if deadline is None or not within_window(deadline, now):
            continue
        if None in (
            to_float(row.get("home_odds")),
            to_float(row.get("draw_odds")),
            to_float(row.get("away_odds")),
        ):
            continue
        if not row.get("home_team") or not row.get("away_team"):
            continue
        row["deadline"] = deadline
        selected.append(row)
    selected.sort(key=lambda item: item["deadline"])
    return selected


def open_had(page):
    page.goto(HAD_URL, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_selector('[data-testid$="_matchTime"]', timeout=20000)
    time.sleep(1)


def show_date(page, deadline):
    label = deadline.strftime("%d/%m")
    for _ in range(2):
        date_button = page.locator('[data-testid="date_button"]')
        if date_button.count() == 0:
            break
        date_button.first.click()
        page.wait_for_timeout(300)
        chip = page.locator("div.date", has_text=label)
        if chip.count():
            chip.first.click()
            page.wait_for_timeout(700)
            return
    print(f"揀唔到日期 {label}，會喺而家個列表繼續搵")


def find_team_in_loaded_list(page, match_id):
    selector = f'[data-testid="{match_id}_homeTeam"]'
    scroll_list(page, None)
    page.wait_for_timeout(200)
    for _ in range(50):
        locator = page.locator(selector)
        if locator.count():
            locator.first.scroll_into_view_if_needed()
            return locator.first
        scroll_list(page, 650)
        page.wait_for_timeout(120)
    return None


def ensure_match_visible(page, match_id):
    for _ in range(8):
        team = find_team_in_loaded_list(page, match_id)
        if team is not None:
            return team
        if not show_more(page):
            break
    raise RuntimeError("列表搵唔到呢場，無法入所有賠率")


def return_to_list(page):
    try:
        page.go_back(wait_until="domcontentloaded", timeout=20000)
        page.wait_for_selector('[data-testid$="_matchTime"]', timeout=10000)
    except Exception:
        open_had(page)


def scrape_match(page, info):
    match_id = info["match_id"]
    show_date(page, info["deadline"])
    try:
        team = ensure_match_visible(page, match_id)
    except RuntimeError:
        print("呢版列表未見到，重新開啟主客和頁再搵...")
        open_had(page)
        show_date(page, info["deadline"])
        team = ensure_match_visible(page, match_id)
    time.sleep(1)
    print("撳主隊名，入所有賠率。唔會撳賠率掣。")
    team.click()
    page.wait_for_selector(f'[data-testid="{match_id}_frontID"]', timeout=15000)
    time.sleep(random.uniform(2, 3))
    front_id = " ".join(page.locator(f'[data-testid="{match_id}_frontID"]').inner_text().split())
    if front_id and front_id != match_id:
        raise RuntimeError(f"入錯場：{front_id}")
    raw_markets = page.evaluate(EXTRACT_JS, match_id)
    record = build_record(info, raw_markets)
    print("返回主客和列表...")
    try:
        return_to_list(page)
    except Exception as exc:
        print(f"返回列表失敗，重新開啟主客和頁：{exc}")
        open_had(page)
    return record


def prune_stale(keep_ids):
    if not UPCOMING_DIR.exists():
        return 0
    removed = 0
    for path in UPCOMING_DIR.glob("FB*.json"):
        if path.stem not in keep_ids:
            path.unlink()
            removed += 1
    return removed


def run(headless=False):
    UPCOMING_DIR.mkdir(parents=True, exist_ok=True)
    now = now_hk()
    print(f"香港時間：{now.strftime('%Y-%m-%d %H:%M')}")
    print(f"只收截止時間未過、而且喺今日起 {LOOKAHEAD_DAYS} 日內、已有主客和賠率嘅場")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        finished = False
        try:
            print("正在前往馬會主客和頁...")
            open_had(page)
            print(f"頁面標題：{page.title()}")
            rows = collect_rows(page, now)
            selected = select_matches(rows, now)
            print(f"列表見到 {len(rows)} 場，符合條件 {len(selected)} 場")

            for index, info in enumerate(selected, start=1):
                if page.is_closed():
                    print("瀏覽器已關閉，停止。已寫低嘅場次仍然保留。")
                    break
                print(
                    f"\n[{index}/{len(selected)}] {info['match_id']} "
                    f"{info['deadline'].strftime('%Y-%m-%d %H:%M')} "
                    f"{info['home_team']} 對 {info['away_team']}"
                )
                try:
                    record = scrape_match(page, info)
                    dump_json(UPCOMING_DIR / f"{info['match_id']}.json", record)
                    print(f"[{index}/{len(selected)}] {info['match_id']} 已覆蓋寫入 upcoming/")
                except Exception as exc:
                    print(f"[{index}/{len(selected)}] {info['match_id']} 失敗：{exc}")
                    if page.is_closed() or not return_to_list_safe(page):
                        print("瀏覽器已關閉，停止。")
                        break
                if index < len(selected):
                    print(f"停 {PAUSE_BETWEEN_MATCHES} 秒，再處理下一場...")
                    time.sleep(PAUSE_BETWEEN_MATCHES)
            else:
                removed = prune_stale({item["match_id"] for item in selected})
                if removed:
                    print(f"移除 {removed} 個已過期或唔再符合條件嘅舊檔")
                finished = True
        finally:
            if browser.is_connected():
                browser.close()
            if finished:
                print("\n未開波賽事更新完成")
            print("程式結束")


def return_to_list_safe(page):
    if page.is_closed():
        return False
    if "/football/had" in page.url:
        return True
    try:
        return_to_list(page)
        return True
    except Exception:
        try:
            open_had(page)
            return True
        except Exception:
            return not page.is_closed()


def main():
    run(headless=False)


if __name__ == "__main__":
    main()
