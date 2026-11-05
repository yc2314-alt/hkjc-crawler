# HKJC 完場／未開波資料契約（DATA CONTRACT）

本文件對應倉庫實際程式：`hkjc_full.py`（完場 v2）、既有 `results/*.json`（多為 v1）、以及 `hkjc_upcoming.py` 輸出。範例見 `tests/fixtures/example_record_v2.json`。

**適用讀者：** Research Desk 及其他只讀 GitHub JSON 的消費端。

**原則：** 完場詳情頁的「最後賠率」是賽後／賽果頁公布的歷史盤口，**不是**開賽當下已鎖存的賽前實時快照。

---

## 1. v1／v2 完場 JSON 欄位及差異

### 1.1 共同核心欄位（v1、v2 皆有）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `match_id` | string | 如 `FB6802` |
| `match_date` | string \| null | `YYYY-MM-DD`（見第 4 節） |
| `match_time` | string \| null | `HH:MM` 24 小時（見第 4 節） |
| `home_team` | string | 主隊中文名 |
| `away_team` | string | 客隊中文名 |
| `half_time_score` | string \| null | 半場比分，正規化後為 `H:A`（如 `1:1`）；無法正規化則 `null` |
| `full_time_score` | string | 全場比分，須為 `H:A`；爬蟲只把能正規化為 `\d+:\d+` 的場當完場 |
| 八個頂層市場 key | object \| array \| null | 見第 2 節 |

八個頂層市場 key（順序固定）：

1. `主客和`
2. `半場主客和`
3. `讓球主客和`
4. `讓球`
5. `入球大細`
6. `半場入球大細`
7. `角球大細`
8. `半場角球大細`

頁面顯示名映射（角球）：

| JSON key | 賽果詳情頁 coupon 名 |
|---|---|
| `角球大細` | `開出角球大細` |
| `半場角球大細` | `半場開出角球大細` |

### 1.2 僅 v2 新增（`schema_version >= 2`）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `schema_version` | number | 目前為 `2` |
| `source` | string | 固定 `bet.hkjc.com/ch/football/results` |
| `collected_at` | string | 本機擷取時間，ISO 8601 含時區偏移（見第 4 節） |
| `odds_phase` | string | 頂層賽前最後賠率固定為 `prematch_last_odds` |
| `additional_markets` | object | 擴充市場；缺市為 `null`（見第 2 節） |
| `inplay_last_odds` | object \| null | 即場投注之最後賠率區塊（見第 3 節） |
| `data_completeness` | object | 完整度摘要（見第 7 節） |
| `backfill_metadata` | object \| 可缺 | 僅歷史補數寫入時出現（見第 7 節） |

### 1.3 v1 與 v2 差異摘要

| 項目 | v1（現有多數 `results/`） | v2（新爬取） |
|---|---|---|
| `schema_version` | 無（消費端應視為 `1`） | `2` |
| 擷取時間 | **無** | `collected_at` |
| 賠率時段標示 | **無**（語意上仍是賽果頁最後賠率） | `odds_phase` |
| 盤口原文 | 多數只有去括號後字串 | 另有 `盤口_原文`／`主隊讓球_原文` 等 |
| 擴充市場 | 無 | `additional_markets` |
| 即場最後賠率 | 舊抽取會跳過「即場投注」coupon，檔內通常無 | 分開存於 `inplay_last_odds` |
| Research Desk | 只讀頂層八市＋比分即可 | 可忽略新欄位；勿把 v2 賠率當實時賽前 |

向後兼容：v2 **保留**全部 v1 頂層欄位與八市 key；Desk 可只讀舊欄位。

---

## 2. 原有八個市場與 `additional_markets` 結構

### 2.1 原有八個市場

缺市或無法解析 → JSON `null`（不成空物件）。

#### 主客和／半場主客和

```json
{
  "主勝": 2.1,
  "和": 3.4,
  "客勝": 3.2
}
```

賠率為 number（由字串 parse float）。

#### 讓球主客和（可多線）

```json
[
  {
    "主隊讓球": "+1",
    "主隊讓球_原文": "[+1]",
    "主勝": 1.82,
    "和": 3.6,
    "客勝": 3.15
  }
]
```

- `主隊讓球`：去中括號後字串（v1／v2 皆有）。
- `主隊讓球_原文`：頁面原文含 `[]`（v2；v1 檔通常無此 key）。
- 每條線對應一組主／和／客；**收集所有可見線，不是只第一條**。

#### 讓球（可多線）

```json
[
  {
    "主隊讓球": "+0.5/+1",
    "主隊讓球_原文": "[+0.5/+1]",
    "客隊讓球_原文": "[-0.5/-1]",
    "主勝": 1.66,
    "客勝": 2.15
  }
]
```

無「和」。

#### 入球大細／半場入球大細／角球大細／半場角球大細（可多線）

```json
[
  {
    "盤口": "2.5/3",
    "盤口_原文": "[2.5/3]",
    "大": 1.88,
    "細": 1.82
  }
]
```

- `盤口`：去括號。
- `盤口_原文`：含括號原文（v2）。
- **所有 `.lineNum` 線都會收集。**

### 2.2 `additional_markets`（僅 v2）

固定 key（缺則該值為 `null`）：

| key | 頁面名稱 | 結構摘要 |
|---|---|---|
| `team_totals` | 球隊入球大細 | 見下 |
| `first_half_team_totals` | 球隊半場入球大細 | 同上 |
| `correct_score` | 波膽 | `[{ "比分", "賠率" }, ...]` |
| `first_half_correct_score` | 半場波膽 | 同上 |
| `first_team_to_score` | 第一隊入球 | 見下 |
| `total_goals` | 總入球 | `{ "0": 10.5, "7+": 26.0, ... }` |
| `odd_even` | 入球單雙 | `{ "單", "雙" }` |
| `half_full_time` | 半全場 | `{ "主-主", "客-客", ... }` |
| `same_game_parlay` | 同場過關 | 僅頁面有可解析數據時非 null |

#### `team_totals` / `first_half_team_totals`

```json
{
  "主隊": {
    "隊伍": "甲隊 (主隊)",
    "盤口": [
      { "盤口_原文": "[1.5]", "盤口": "1.5", "大": 2.38, "細": 1.51 }
    ]
  },
  "客隊": { "隊伍": "...", "盤口": [ ... ] },
  "其他": null
}
```

#### `correct_score`

```json
[
  { "比分": "1:0", "賠率": 9.25 },
  { "比分": "主其他 (客無入球)", "賠率": 600.0 }
]
```

「其他」類選項以頁面文字原樣作 `比分`。

#### `first_team_to_score`

```json
{
  "主隊": 2.02,
  "無入球": 10.5,
  "客隊": 1.81,
  "原文": { "主隊勝": 2.02, "無入球": 10.5, "客隊勝": 1.81 }
}
```

#### `same_game_parlay`

```json
{
  "賠率": [12.0],
  "原文名稱": "同場過關"
}
```

結構刻意從簡；若頁面無可解析賠率則整個為 `null`。

---

## 3. `inplay_last_odds` 與賽前最後賠率的區別

### 3.1 頂層賽前（`odds_phase: "prematch_last_odds"`）

- 來源：`.LAST_ODDS` 內 **coupon 名稱不含「即場投注」** 的區塊。
- 寫入：頂層八市 + `additional_markets`。
- 語意：馬會賽果／最後賠率頁公布的**開賽前最後受注盤**（賽後才在此頁看到），**不是**爬蟲在開賽前某一刻鎖存的實時盤。

### 3.2 `inplay_last_odds`

```json
{
  "odds_phase": "inplay_last_odds",
  "markets": { "主客和": { ... }, "入球大細": [ ... ], "...": null },
  "additional_markets": { "correct_score": [ ... ], "team_totals": null, "...": null },
  "raw_names": [
    "主客和- 即場投注之最後賠率",
    "入球大細- 即場投注之最後賠率"
  ]
}
```

- 來源：coupon 名含「即場投注」者（如 `主客和- 即場投注之最後賠率`）。
- **禁止**與頂層賽前八市混用或互相覆蓋。
- 無即場區塊時可為 `null`，或僅有空／多為 null 的結構（視當次解析結果）。
- `raw_names`：原始 coupon 標題，供核對。

---

## 4. 時間欄位：語義、時區、可信程度

| 欄位 | 出現於 | 語義 | 時區／格式 | 可信程度 |
|---|---|---|---|---|
| `match_date` | 完場 | 開賽日期（優先 GraphQL `kickOffTime`；否則日期格／testid） | 日期字串，對應香港開賽日 | 高（馬會賽果資料）；缺資料時可 null |
| `match_time` | 完場 | 開賽時刻 | `HH:MM`，來自 `kickOffTime` 的本地鐘面（`+08:00` 解析後） | 高；少數場可能缺而為 null |
| `collected_at` | 完場 v2 | **爬蟲寫檔當刻** | ISO 8601，含本機偏移，例如 `2026-11-01T00:00:00+08:00` | 高（本機時鐘）；**不是**賠率在盤口上的生效時刻 |
| `backfill_metadata.collected_at` | 歷史寫入 | 該次補數寫入時刻 | 同上 ISO | 同 `collected_at` |
| `match_date` + `kickoff` | upcoming | 截止／列表時間（程式當開賽／截止用） | 日期 + `HH:MM`；另有 `timezone: "Asia/Hong_Kong"` | 來自主客和列表；語意是列表上的時間，不是完場 `kickOffTime` API |
| `updated_at` | upcoming | 該檔被爬蟲覆蓋寫入的時間 | `YYYY-MM-DD HH:MM:SS`（香港 `now_hk()`），**字串本身無偏移** | 高（擷取時間）；與完場 `collected_at` 格式不同 |

**不可推論：**

- 有 `collected_at` ≠ 賠率在 `collected_at` 當刻仍可投注。
- 完場頂層賠率 ≠ 開賽前 T 時刻的實時快照。
- `match_time` ≠ 爬取時間。

---

## 5. `odds_phase`、市場代碼、盤口方向、賠率格式

### 5.1 `odds_phase`

| 值 | 位置 | 意義 |
|---|---|---|
| `prematch_last_odds` | 記錄頂層 | 賽前最後受注盤（賽果頁公布） |
| `inplay_last_odds` | 僅 `inplay_last_odds` 物件內 | 即場投注之最後賠率 |

v1 無此欄：消費端仍應按「賽果頁最後賠率」理解，不可當實時賽前。

### 5.2 頁面市場代碼（DOM／testid 常見）

| 中文 | 常見代碼 |
|---|---|
| 主客和 | HAD |
| 半場主客和 | FHA |
| 讓球主客和 | HHA |
| 讓球 | HDC |
| 入球大細 | HIL |
| 半場入球大細 | FHL |
| 開出角球大細 | CHL |
| 半場開出角球大細 | FCH |
| 球隊入球大細 | TEAMHIL / HLH·HLA |
| 球隊半場入球大細 | TEAMFHL / FLH·FLA |
| 波膽 | CRS |
| 半場波膽 | FCS |
| 第一隊入球 | FTS |
| 總入球 | TTG |
| 入球單雙 | OOE |
| 半全場 | HFT |

JSON **不以**這些代碼作頂層 key（八市用中文；擴充用英文 snake_case）。

### 5.3 盤口方向

- **讓球／讓球主客和：** `主隊讓球` 以**主隊**視角；`+` 主隊受讓，`-` 主隊讓出。客隊對應盤見 `客隊讓球_原文`（讓球）。
- **大細：** `大`／`細` 相對該行 `盤口`／`盤口_原文`。
- **球隊大細：** 分 `主隊`／`客隊` 區塊，各自多線。
- 原始字串（含 `[]`、`/` 分割盤）以 `*_原文` 為準；去括號欄位僅方便顯示。

### 5.4 原始賠率格式

- 頁面文字 → `float`（如 `"9.25"` → `9.25`，`"600.0"` → `600.0`）。
- 無法 parse → 該選項丟棄；整市無有效選項 → `null`。
- **不**存貨幣符號；**不**把即場賠率寫入頂層賽前欄位。

---

## 6. 完場比分、取消、延期、腰斬、賽果更正

### 6.1 已支援

| 狀態 | 表示方式 |
|---|---|
| 正常完場 | `full_time_score` 為 `H:A`；`half_time_score` 可為 `H:A` 或 `null` |
| 列表上非完場（無合法全場比分） | **不會**寫入／列入待爬完場清單 |
| 某市場缺失 | 該市場 `null`；**不刪**整場檔 |
| 合併時比分由 A 改為 B | 程式可更新檔內 `full_time_score`／`half_time_score`，並在執行 log 印出更正；見下 |

### 6.2 明確尚未支援（契約層）

| 狀態 | 現況 |
|---|---|
| 取消 | 無專用欄位／枚舉；不會因「取消」字樣建檔 |
| 延期 | 同上 |
| 腰斬／改期 | 同上 |
| 賽果更正的持久標記 | `merge` 的 `score_correction` **只在執行過程 notes／log**，**不會**寫入 JSON 的穩定欄位（無 `score_history`） |
| 走水／注碼狀態 | 不收集 |
| 即場比分過程 | 不收集 |

消費端若需「取消／延期／腰斬」，**不可**假設本倉庫 JSON 已編碼這些狀態。

---

## 7. `backfill_metadata` 與 `data_completeness`

### 7.1 `backfill_metadata`（歷史模式寫入時）

```json
{
  "mode": "backfill",
  "day": "2025-01-07",
  "collected_at": "2026-11-01T00:30:00+08:00"
}
```

| 欄位 | 說明 |
|---|---|
| `mode` | 固定 `"backfill"` |
| `day` | 該次補數搜尋的目標日 `YYYY-MM-DD` |
| `collected_at` | 該次寫入時刻（ISO） |

每日模式新檔通常**沒有**此物件。

另：倉庫根目錄 `backfill_progress.json` 是爬蟲進度檔（非單場契約），含 `next_date`、`days`、`completed_match_ids`、`failed` 等；Research Desk **不應**依賴它作為賽事資料源。

### 7.2 `data_completeness`

```json
{
  "core_eight": "full | partial | none",
  "additional": "full | partial | none",
  "missing_markets": ["半場主客和", "team_totals", "..."]
}
```

| 欄位 | 說明 |
|---|---|
| `core_eight` | 八個頂層市場是否皆非 null |
| `additional` | `additional_markets` 內各 key 是否皆非 null |
| `missing_markets` | 仍為 null 的八市中文名 **或** additional 的英文 key |

注意：`additional: "full"` 極嚴（所有擴充 key 都有值）；歷史場常見 `partial`。  
`record_is_complete_enough`（爬蟲內部 skip 用）只要求 schema≥2 且存在 `additional_markets` 結構，**不**要求 additional 全滿。

---

## 8. Research Desk：何時只能當歷史研究、不能當賽前實時快照

請同時滿足理解與過濾：

1. **路徑／來源**  
   - `results/*.json` 或 `all_results.json` → 完場賽果頁產物。  
   - `source == "bet.hkjc.com/ch/football/results"`（v2）更可確認。

2. **時段**  
   - 頂層 `odds_phase == "prematch_last_odds"`（或 v1 無欄位但來自 results）→ **賽前最後盤的事後公布值**。  
   - `inplay_last_odds` → 只可標成即場最後盤研究，不可當賽前。

3. **時間先後**  
   - 若 `collected_at`（或檔案更新時間）**晚於** `match_date` + `match_time`（香港），賠率必然是事後收集，不能當開賽前實時。  
   - 即使 `collected_at` 在開賽前（少見於完場爬蟲），完場頁「最後賠率」語意仍不是「當刻盤口快照服務」。

4. **產品用途建議**  
   - ✅ 歷史盤口、模型訓練、賽後對盤、研究。  
   - ❌ 開賽前即時定價、套利觸發、標成「當刻 HKJC 可投注價」。

5. **未開波對照**  
   - `upcoming/*.json` 才是未開波列表當刻附近的盤；仍受更新頻率限制，見第 9 節與下方 upcoming 說明。

---

## 9. GitHub 路徑與更新方式

| 資料 | 倉庫路徑 | 產生程式 | 典型推送方式 |
|---|---|---|---|
| 單場完場 | `results/FBXXXX.json` | `hkjc_full.py`（daily 或 backfill） | `daily_run.bat`：跑完 → `git add` → commit「每日更新」→ `push` |
| 合併完場 | `all_results.json` | `hkjc_full.py` 在每日待辦清空或 `--merge-all` 時重建 | 同上 |
| 每日進度 | `progress.json` | 僅 daily | 可能隨 bat 一併提交；非賽事契約 |
| 歷史進度 | `backfill_progress.json` | 僅 backfill | 不自動 push；需人手提交 |
| 未開波最新盤 | `upcoming/FBXXXX.json` | `hkjc_upcoming.py` | `upcoming_run.bat`：commit「更新未開波賽事」→ `push` |
| 未開波觀察快照 | `upcoming_snapshots/by_day/YYYY-MM-DD.jsonl`、`upcoming_snapshots/by_match/FBXXXX.jsonl` | `hkjc_upcoming.py`（每次成功擷取追加） | 隨 `upcoming_run.bat` 的 `git add .` 一併提交；**push 失敗不刪本機快照** |

遠端：`yc2314-alt/hkjc-crawler` 的 `main`。  
Research Desk 應讀 **已 push** 的 GitHub 內容；本機未 push 則 Desk 不可見。

未開波快照契約詳見 **§10**。

歷史補數：**不會**由 `daily_run.bat` 自動長跑；需顯式：

```text
python hkjc_full.py --mode backfill ... --live-approved
```

且預設不自動 Git push。

---

## 10. 未開波觀察快照（`upcoming_snapshots`）

馬會網頁**只顯示當刻最新盤**，不提供同一場多時點歷史。本倉庫只在**人手執行爬蟲並成功讀盤**時追加一筆真實觀察；不補造未執行時段、不要求每日三次、不設自動排程。

### 10.1 儲存路徑與格式

| 路徑 | 格式 | 用途 |
|---|---|---|
| `upcoming/FBXXXX.json` | JSON（覆蓋） | 該場**最新一次**成功盤口（舊契約不變） |
| `upcoming_snapshots/by_day/YYYY-MM-DD.jsonl` | JSONL 追加 | 按 `captured_at` 香港日期分組，方便增量匯入 |
| `upcoming_snapshots/by_match/FBXXXX.jsonl` | JSONL 追加 | 同一 `match_id` 全部已保存觀察，可按 `captured_at` 排序 |

- 每一成功場次會**同時** append 到 by_day 與 by_match。  
- 行格式：一行一個 JSON 物件，UTF-8，無 pretty-print。  
- 讀取時略過空白行與無法 parse 的壞行（中斷寫入殘留）。  
- 寫入：staging 後 append，並 `flush` + `fsync`；**不可覆寫**已寫入行。

### 10.2 快照 JSON 結構

```json
{
  "snapshot_id": "FB9001_20261031T210000p0800_abcd1234",
  "match_id": "FB9001",
  "source": "HKJC",
  "run_id": "…32 hex…",
  "schema_version": 1,
  "captured_at": "2026-10-31T21:00:00+08:00",
  "kickoff_at": "2026-11-01T07:00:00+08:00",
  "betting_close_at": "2026-11-01T07:00:00+08:00",
  "home_team": "甲隊",
  "away_team": "乙隊",
  "prematch_valid": true,
  "odds_phase": "prematch_observed",
  "markets": {
    "主客和": { "主勝": 2.1, "和": 3.4, "客勝": 3.2 },
    "半場主客和": null,
    "讓球主客和": [ { "主隊讓球": "+1", "主隊讓球_原文": "[+1]", "主勝": 1.82, "和": 3.6, "客勝": 3.15 } ],
    "讓球": [ { "主隊讓球": "+0.5/+1", "主隊讓球_原文": "[+0.5/+1]", "客隊讓球_原文": "[-0.5/-1]", "主勝": 1.66, "客勝": 2.15 } ],
    "入球大細": [ { "盤口": "2.5", "盤口_原文": "[2.5]", "大": 1.67, "細": 2.07 } ],
    "半場入球大細": null,
    "角球大細": null,
    "半場角球大細": null
  }
}
```

缺市為 `null`，不填假賠率。盤口保留 `*_原文`（含 `[]`）。

### 10.3 `snapshot_id` 生成方式

```text
{match_id}_{YYYYMMDDTHHMMSS}p0800_{run_id 前 8 位}
```

例：`FB9001_20261031T210000p0800_abcd1234`  
同一 `run_id` + 同一 `match_id` 在同輪只會寫一次（見 10.6）。

### 10.4 `captured_at` 語義及時區

| 欄位 | 語義 |
|---|---|
| `captured_at` | 成功 `EXTRACT` 盤口後立即取的香港時間，ISO 8601 **含 `+08:00`**。**不是** Git commit 時間，**不是**檔案 mtime，**不可**用來補造未執行時段。 |
| `kickoff_at` / `betting_close_at` | 目前皆來自主客和列表 `matchTime`（預定截止投注時間）。頁面未另給獨立開賽鐘時兩者相同。 |

### 10.5 `updated_at`（upcoming JSON）與快照關係

| | `upcoming/FBXXXX.json` | 快照 JSONL |
|---|---|---|
| 更新方式 | 每次成功**覆蓋** | 每次成功**追加**一行 |
| 時間欄 | `updated_at`：`YYYY-MM-DD HH:MM:SS`（香港，無偏移字串） | `captured_at`：ISO 含 `+08:00` |
| 關係 | 與同一次成功擷取使用同一刻鐘寫入 | 同一次成功會多寫快照；歷史觀察只在 JSONL |

Desk 若要「歷次觀察」→ 讀 JSONL；若只要「而家最新」→ 讀 `upcoming/FBXXXX.json`。

### 10.6 同輪重試去重

- 每次 `run()` 產生一個 `run_id`（UUID hex）。  
- `SnapshotStore` 記憶體集合：同一 `run_id` 下同一 `match_id` 第二次 `save` 直接返回 `None`，不寫檔。  
- **不同** `run_id`（不同人手執行）即使賠率完全相同，仍各寫一筆（兩次真實觀察）。

### 10.7 賽前有效性

- `prematch_valid == true` 且 `odds_phase == "prematch_observed"`：`captured_at` **嚴格早於** `betting_close_at` 與 `kickoff_at`（若有）。  
- 已過截止／開賽：`prematch_valid == false`，`odds_phase == "invalid_prematch_observation"`——**不得**當有效賽前快照用。  
- 正常選場流程已過濾截止已過場次；旗標供防呆與測試。

### 10.8 GitHub 發布與 Desk 增量讀取

1. 人手跑 `upcoming_run.bat`（或 `python hkjc_upcoming.py` 後自行 git）。  
2. bat 仍 `git add .` → commit「更新未開波賽事」→ `push`；**新 JSONL 會一併上船**。  
3. Push 失敗：本機 `upcoming/` 與 `upcoming_snapshots/` **已寫入保留**。  
4. Desk 建議：  
   - 增量：讀尚未消化過的 `by_day/*.jsonl` 新行，或以 `snapshot_id`／`(run_id, match_id, captured_at)` 去重。  
   - 單場歷史：讀 `by_match/FBXXXX.jsonl`，按 `captured_at` 排序。  
   - **不要**用 Git blame／commit time 當 `captured_at`。

### 10.9 單次觀察的資料限制

| 有效觀察筆數 | 允許 | 禁止 |
|---|---|---|
| 0 | 標示無有效觀察 | 造假快照 |
| 1 | 標「單次觀察」；可用作該刻盤口 | 計算變盤；斷言期間從未變動 |
| ≥2 | 比較**已觀察**時間點之差 | 補造中間價；把缺口當連續走勢 |

輔助函式 `observation_summary()`（爬蟲庫內）只回傳上述限制說明，**不**做六席決策或交叉分析。

### 10.10 與完場 `prematch_last_odds` 的區別

| | 未開波快照 `prematch_observed` | 完場 `prematch_last_odds` |
|---|---|---|
| 何時收集 | 開賽／截止**前**人手爬取當刻盤 | 賽後在賽果頁讀「最後賠率」 |
| 語意 | 真實觀察到的當刻受注盤（受執行頻率限制） | 事後公布的開賽前最後盤 |

---

## 附錄 A — upcoming 最新檔時間欄（摘要）

| 欄位 | 例子 | 說明 |
|---|---|---|
| `updated_at` | `"2026-10-31 22:51:31"` | 該次覆蓋寫入 upcoming JSON 的香港時間（無偏移） |
| `timezone` | `"Asia/Hong_Kong"` | 時區名 |
| `kickoff` | `"07:00"` | 列表時間（現況＝截止投注時間鐘面），**不是**擷取時間 |
| `match_date` | `"2026-11-01"` | 列表日期 |

歷次觀察請用 §10 快照，不要用檔案修改時間回填。

---

## 附錄 B — 快速判斷表（Desk）

| 問題 | 看法 |
|---|---|
| 這場完場賠率能否當開賽前實時價？ | **否** |
| v1 無 `odds_phase` 是否仍是最後賠率？ | **是**（來源仍是賽果頁） |
| `inplay_last_odds` 可否併入賽前模型特徵而不標記？ | **否**，必須分開 |
| 市場 `null` 是否代表賠率為 0？ | **否**，代表缺失／未開／未解析 |
| 取消波去邊搵？ | **本契約未提供** |
| 未開波只有一筆快照能否做變盤？ | **否**（單次觀察） |
| 未執行時段有無盤口？ | **無**；不得補造 |
