import random
import re
import time
from playwright.sync_api import sync_playwright

# ========== 設定區 ==========
SHEET_ID = "1IYRPw6A7vqYcVP75svud0C_1quTd5l6A07IvmYE-bc8"
RESULTS_URL = "https://bet.hkjc.com/ch/football/results"
SERVICE_ACCOUNT_FILE = "service_account.json"

# 你要嘅賠率種類。角球兩項喺頁面上叫「開出角球大細」。
TARGET_MARKETS = [
    "主客和",
    "半場主客和",
    "讓球主客和",
    "讓球",
    "入球大細",
    "半場入球大細",
    "角球大細",
    "半場角球大細",
]

MARKET_PAGE_NAMES = {
    "主客和": "主客和",
    "半場主客和": "半場主客和",
    "讓球主客和": "讓球主客和",
    "讓球": "讓球",
    "入球大細": "入球大細",
    "半場入球大細": "半場入球大細",
    "角球大細": "開出角球大細",
    "半場角球大細": "半場開出角球大細",
}

TARGET_MATCH_IDS = [
    "FB6978",
    "FB7219",
    "FB7187",
    "FB7077",
    "FB6802",
]
# ==========================

MATCH_INFO_SELECTOR = 'div[data-testid*="matchInfo"]'

EXTRACT_ODDS_JS = """
() => {
  const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
  const oddsText = (node) => {
    if (!node) return "";
    const span = node.querySelector(".add-to-slip, .oddsColor");
    return clean(span ? span.textContent : "");
  };
  const panel = document.querySelector(".LAST_ODDS");
  if (!panel) return [];

  const markets = [];
  const children = [...panel.children];
  for (let i = 0; i < children.length; i++) {
    const node = children[i];
    if (!node.classList || !node.classList.contains("coupon")) continue;
    const desc = clean(node.querySelector(".desc") && node.querySelector(".desc").textContent);
    if (!desc || desc.includes("即場投注")) continue;

    const row = children[i + 1];
    if (!row || !row.classList.contains("match-row-container")) continue;

    const lineNums = [...row.querySelectorAll(".lineNum")];
    if (lineNums.length) {
      markets.push({
        name: desc,
        kind: "line",
        lines: lineNums.map((lineNum) => {
          let oddsBox = lineNum.nextElementSibling;
          while (oddsBox && !oddsBox.classList.contains("odds")) {
            oddsBox = oddsBox.nextElementSibling;
          }
          const values = oddsBox
            ? [...oddsBox.querySelectorAll(".oddsCheckboxGrid")].map((grid) => oddsText(grid)).filter(Boolean)
            : [];
          return { line: clean(lineNum.textContent), values };
        }),
      });
      continue;
    }

    const blocks = [...row.querySelectorAll(".odds")].filter((el) => el.querySelector(".hdcOddsItem"));
    if (blocks.length) {
      markets.push({
        name: desc,
        kind: "handicap",
        lines: blocks.map((block) =>
          [...block.querySelectorAll(".hdcOddsItem")].map((item) => ({
            cond: clean(item.querySelector(".cond") && item.querySelector(".cond").textContent),
            odds: oddsText(item),
          }))
        ),
      });
      continue;
    }

    markets.push({
      name: desc,
      kind: "simple",
      labels: [...row.querySelectorAll(".team span")].map((el) => clean(el.textContent)).filter(Boolean),
      values: [...row.querySelectorAll(".oddsCheckboxGrid")].map((grid) => oddsText(grid)).filter(Boolean),
    });
  }
  return markets;
}
"""


def _text(locator):
    if locator.count() == 0:
        return ""
    return " ".join(locator.first.inner_text().split())


def extract_match_data(match_element):
    """從單場賽事元素抽取場次編號、對賽隊伍、半場同全場比數。"""
    try:
        testid = match_element.get_attribute("data-testid") or ""
        found = re.search(r"(FB\d+)", testid)
        match_id = found.group(1) if found else ""
        if not match_id:
            return None

        teams = _text(match_element.locator(f'[data-testid="matchResults_{match_id}_team"]'))

        half_time_locator = match_element.locator(
            f'[data-testid="matchResults_{match_id}_halfTime"] span'
        )
        if half_time_locator.count() == 0:
            half_time_locator = match_element.locator(
                f'[data-testid="matchResults_{match_id}_halfTime"]'
            )
        half_time_score = _text(half_time_locator)
        full_time_score = _text(
            match_element.locator(f'[data-testid="matchResults_{match_id}_fullTime"]')
        )

        return {
            "match_id": match_id,
            "teams": teams,
            "half_time_score": half_time_score,
            "full_time_score": full_time_score,
        }
    except Exception as exc:
        print(f"抽取賽事資料失敗：{exc}")
        return None


def find_match(page, match_id):
    locator = page.locator(f'div[data-testid*="{match_id}_matchInfo"]')
    if locator.count() == 0:
        return None
    return locator.first


def format_market(name, market):
    page_name = MARKET_PAGE_NAMES[name]
    title = name if page_name == name else f"{name}（頁面：{page_name}）"
    if not market:
        return f"{title}：冇呢種賠率"

    if market["kind"] == "simple":
        labels = market.get("labels") or []
        values = market.get("values") or []
        if not values:
            return f"{title}：冇呢種賠率"
        parts = []
        for index, value in enumerate(values):
            label = labels[index] if index < len(labels) else ""
            parts.append(f"{label} {value}".strip())
        return f"{title}：" + " / ".join(parts)

    if market["kind"] == "line":
        rows = []
        for item in market.get("lines") or []:
            values = item.get("values") or []
            over = values[0] if values else "-"
            under = values[1] if len(values) > 1 else "-"
            rows.append(f"  {item.get('line', '')} 大 {over} / 細 {under}")
        if not rows:
            return f"{title}：冇呢種賠率"
        return f"{title}：\n" + "\n".join(rows)

    rows = []
    for line in market.get("lines") or []:
        parts = [f"{item.get('cond', '')} {item.get('odds', '')}".strip() for item in line]
        parts = [part for part in parts if part]
        if parts:
            rows.append("  " + " / ".join(parts))
    if not rows:
        return f"{title}：冇呢種賠率"
    return f"{title}：\n" + "\n".join(rows)


def print_odds(markets):
    by_name = {market["name"]: market for market in markets}
    for name in TARGET_MARKETS:
        page_name = MARKET_PAGE_NAMES[name]
        print(format_market(name, by_name.get(page_name)))


def return_to_results(page):
    back = page.locator(".back-btn")
    if back.count():
        back.first.click()
    else:
        page.goto(RESULTS_URL, wait_until="domcontentloaded", timeout=30000)

    try:
        page.wait_for_function(
            """() => {
                const panel = document.querySelector('.LAST_ODDS');
                const list = document.querySelector('div[data-testid*="matchInfo"]');
                const panelGone = !panel || panel.offsetParent === null;
                return panelGone && !!list;
            }""",
            timeout=10000,
        )
    except Exception:
        print("返回掣未關到賠率頁，重新載入賽果頁...")
        page.goto(RESULTS_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector(MATCH_INFO_SELECTOR, timeout=20000)


def read_one_match(page, match_id, index, total):
    print(f"\n========== {index}/{total} {match_id} ==========")
    match = find_match(page, match_id)
    if match is None:
        print("賽果頁搵唔到呢場")
        return

    info = extract_match_data(match) or {"teams": "", "half_time_score": "", "full_time_score": ""}
    print(f"隊伍：{info['teams']}")
    print(f"半場 {info['half_time_score']} | 全場 {info['full_time_score']}")

    match.scroll_into_view_if_needed()
    time.sleep(1)

    odds_button = match.locator(".detail-btn", has_text="%")
    if odds_button.count() == 0:
        print("搵唔到呢場嘅賠率掣（%）")
        return

    print("撳開最後賠率...")
    odds_button.first.click()
    page.wait_for_selector(".LAST_ODDS", timeout=15000)
    time.sleep(random.uniform(2, 3))

    markets = page.evaluate(EXTRACT_ODDS_JS)
    print_odds(markets)
    print("返回賽果頁...")
    return_to_results(page)


def crawl(headless=False):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1400, "height": 900})

        print("正在前往馬會賽果頁面...")
        page.goto(RESULTS_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_selector(MATCH_INFO_SELECTOR, timeout=20000)
        time.sleep(1)
        print(f"頁面標題：{page.title()}")
        print(f"今次只處理 {len(TARGET_MATCH_IDS)} 場：{', '.join(TARGET_MATCH_IDS)}")

        total = len(TARGET_MATCH_IDS)
        for index, match_id in enumerate(TARGET_MATCH_IDS, start=1):
            try:
                read_one_match(page, match_id, index, total)
            except Exception as exc:
                print(f"{match_id} 處理失敗：{exc}")
                try:
                    return_to_results(page)
                except Exception:
                    page.goto(RESULTS_URL, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_selector(MATCH_INFO_SELECTOR, timeout=20000)

            if index < total:
                print("停 5 秒，再處理下一場...")
                time.sleep(5)

        browser.close()
        print("\n測試完成")


def main():
    crawl(headless=False)


if __name__ == "__main__":
    main()
