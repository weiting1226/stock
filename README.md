# 美股監控系統 — 每日自動爬蟲 + 儀表板

兩個獨立模組，各自每天自動更新一次，共用同一組靜態網頁儀表板（GitHub Pages）：

| 模組 | 內容 | 頁面 | 排程 |
|---|---|---|---|
| **模組一** | 市場流動性雙軌監控系統 v3.0（總體擇時） | `docs/index.html` | 平日 22:30 UTC |
| **模組二** | 分析師估值與市價差異比較（個股選股） | `docs/valuation.html` | 週二至週六 23:10 UTC |

---

# 模組一：市場流動性雙軌監控系統 v3.0

依據《市場流動性雙軌監控與交易策略系統 v3.0》文件，自動抓取文件「一、資料抓取規範」
列出的權威資料源、依「二、軌道一計分」與「三、三道閘門」規則計算分數與部位建議，
每天執行一次，並輸出成靜態網頁儀表板（GitHub Pages）供查詢。

## 架構

```
liquidity_monitor/        資料抓取 + 計分引擎（純 Python，可離線單元測試）
  config.py                資料源代碼、發布時滯、權重、門檻表（抄錄自 v3.0 文件）
  sources/                 各資料源的抓取器（FRED / Yahoo Finance / FINRA / 維基百科 / Fed）
  timeseries.py            時滯位移、交易日對齊、年增率/區間變化等純函式工具
  scoring.py                18項子項計分、類別平均、綜合分數、燈號、結構性警示
  gates.py                  Gate A/B/C 判定 + 軌道二簡化版每日近似訊號
  pipeline.py                串接以上模組，產出當日完整報告(dict)
  storage.py                 把報告寫進 docs/data/（給前端儀表板讀取）
scripts/run_daily.py       每日排程進入點（CLI）
.github/workflows/daily-scrape.yml   GitHub Actions 排程（平日 22:30 UTC）
docs/                        GitHub Pages 網站根目錄（純前端，讀取 docs/data/ 的 JSON/CSV）
tests/                        pytest，全部使用合成/mock資料，不需要網路連線
```

## 快速開始

```bash
pip install -r requirements.txt
python3 scripts/run_daily.py -v          # 抓今天的資料，寫到 docs/data/
python3 -m pytest tests/ -v               # 跑離線測試（純函式 + mock網路的整合測試）
```

### 開啟 GitHub Pages（一次性手動設定，本專案的程式碼無法自動開啟）

1. Repo → Settings → Pages
2. Source 選「Deploy from a branch」，Branch 選 `main` / 資料夾選 `/docs`
3. 存好後幾分鐘網址會是 `https://<你的帳號>.github.io/<repo>/`

### 每日自動更新的注意事項

GitHub Actions 的 `schedule` (cron) **只會在預設分支 (通常是 main) 上生效**。
這個工作流程檔案目前是跟著這次的 PR 一起開發在功能分支上，**merge 進 main 之後
排程才會真的開始每天執行**；在那之前可以用 Actions 頁籤手動點
「Run workflow」(workflow_dispatch) 測試。

## v3.0 文件18項指標 ↔ 資料來源對照

| 類別 | 子項 | 資料來源 | 狀態 |
|---|---|---|---|
| ①總體貨幣流動性(10%) | Fed資產負債表年增率3個月變化 | FRED `WALCL` | ✅ 自動 |
| ① | ON RRP 12個月百分位 | FRED `RRPONTSYD`（含結構性休眠例外條款） | ✅ 自動 |
| ① | M2年增率3個月變化 | FRED `M2SL`（已套用約4週發布時滯） | ✅ 自動 |
| ②資金成本與信用(20%) | HY OAS 水準(鐘型計分) | FRED `BAMLH0A0HYM2` | ✅ 自動 |
| ② | HY OAS 20日變化 | FRED `BAMLH0A0HYM2` | ✅ 自動 |
| ② | 2Y-10Y公債利差 | FRED `DGS10`/`DGS2` | ✅ 自動 |
| ② | SOFR-IORB利差 | FRED `SOFR`/`IORB` | ✅ 自動 |
| ③市場微觀結構(10%) | MOVE指數 | Yahoo Finance `^MOVE` | ✅ 自動 |
| ③ | NDX站上200日線比例 | 維基百科NASDAQ-100成分股 + Yahoo Finance批次收盤價 | ✅ 自動（成分股清單為目前值，非時點正確） |
| ④風險偏好情緒(15%) | VIX | Yahoo Finance `^VIX` | ✅ 自動 |
| ④ | VIX/VIX3M期限結構 | Yahoo Finance `^VIX`/`^VIX3M` | ✅ 自動 |
| ④ | 融資餘額年增率(鐘型計分) | FINRA margin statistics（已套用文件規定之2個月發布時滯） | ✅ 自動，網頁解析未經即時流量驗證 |
| ⑤跨資產資金流向(15%) | DXY美元指數月變動 | Yahoo Finance `DX-Y.NYB`（失敗時退回FRED `DTWEXBGS`） | ✅ 自動 |
| ⑤ | 股票型ETF資金流 | — | ❌ 暫缺，需人工填入 `docs/data/manual_overrides.json` |
| ⑤ | USD/JPY 20日變化 | Yahoo Finance `JPY=X` | ✅ 自動 |
| ⑥政策方向(30%) | FOMC決議偏向(升降息+異議票) | federalreserve.gov 新聞稿 + FRED `DFEDTARU`(規則式文字解析) | ✅ 自動，規則式解析，非語意理解 |
| ⑥ | CME FedWatch隱含路徑 | — | ❌ 暫缺，需人工填入 `docs/data/manual_overrides.json` |
| ⑥ | 30Y殖利率60日動能 | FRED `DGS30` | ✅ 自動（bp門檻為本專案量化假設，見`scoring.py`註解） |

Gate B 脆弱度閘門另外用到：融資餘額/GDP（FINRA + FRED `GDP`）、VIX/SKEW 同時觸發
（Yahoo Finance `^VIX`/`^SKEW`）、NDX前瞻本益比（❌ 無自動資料源，恆暫缺，可人工填入
`ndx_fwd_pe`）。

## 資料真實性 / 誠實原則（比照文件第285-292行）

- 抓不到、解析失敗、或資料源本身不存在（ETF資金流、CME FedWatch、NDX前瞻本益比），
  一律標記「暫缺」，**絕不用其他數字頂替**，也不會讓整個計分流程失敗——該子項在
  「類別平均分」中被排除，該類別缺一整類時權重按比例分攤給其餘類別（`scoring.composite_score`）。
- 每一項在 `docs/data/latest.json` 都附有信心等級（高/中/低/暫缺）與資料日期，
  對應儀表板「當日18項數據查證清單」。
- Gate B 的「過去10年百分位」若歷史資料不足會改用文件表格的2026年參考門檻，並在
  UI 上標註 `approx: true`（近似值），不會偷換成固定門檻假裝是真百分位。
- 30Y殖利率60日動能、Gate A「NDX在200日線之上但均線未明確上彎」這類文件只給
  質化描述、沒給精確數字門檻的地方，程式碼裡都用中文註解明確標出「本專案自訂假設」，
  供人工複核調整。

## 已知限制

1. **軌道二（即時應變層）本質是盤中事件監控**，每日批次爬蟲無法取得真正盤中資料。
   儀表板「軌道二」區塊是用「日收盤對日收盤」變化回推的簡化近似（僅第一層四項訊號），
   非真正熔斷判定，UI 上已明確標註不得作為加碼依據。第二、三層（SOFR盤中異常、
   熔斷紀錄、亞股/原油盤中異常）完全不在本專案範圍內。
2. **FOMC異議票偵測**是規則式文字比對（搜尋"voting against"），非語意理解；聯準會
   新聞稿改版格式時會直接拋出錯誤而非給錯誤答案。
3. **NDX 200日線廣度**用的是「目前」NASDAQ-100成分股清單回推過去價格，屬於
   look-back bias（成分股是會變動的），不是嚴格的時點正確計算。
4. 本專案開發環境的沙盒沒有對外網路權限（`fred.stlouisfed.org` / `finance.yahoo.com` /
   `finra.org` / `federalreserve.gov` 皆連不到），所有抓取邏輯是用合成資料的整合測試
   （`tests/test_pipeline.py`）驗證流程正確性，**尚未對真實網站的即時回應格式做過驗證**。
   GitHub Actions runner 有一般網路權限，理論上可以正常執行，但第一次實際跑通後
   請人工檢查 `docs/data/latest.json`，特別留意 FINRA 融資餘額與 FOMC 新聞稿解析
   （這兩個資料源版面最容易改版）。若解析失敗，函式會拋出清楚的錯誤訊息而不是
   回傳錯誤數字，可用 `--margin-override-csv` 或 `manual_overrides.json` 人工備援。

## 人工手動填入的三個欄位

`docs/data/manual_overrides.json`：

```json
{
  "etf_fund_flow": {"score": 1, "as_of": "2026-08-05", "note": "..."},
  "fedwatch_path": {"score": -1, "as_of": "2026-08-05", "note": "..."},
  "ndx_fwd_pe": {"value": 28.5, "as_of": "2026-08-05", "note": "..."}
}
```

`score` 需為 -2..2 的整數（依文件表格人工判斷後填入）；超過45天沒更新會被標記
「暫缺(逾45天未更新)」並停止採計，避免用陳舊資料當成今日訊號。

---

# 模組二：分析師估值與市價差異比較

蒐集美股分析師共識目標價與每日收盤價，計算「上漲空間」
（=（共識目標價 − 收盤價）／收盤價），並提供可依類股篩選、排序的比較儀表板，
用來尋找分析師共識價明顯高於市價的標的。

## 架構

```
analyst_valuation/
  config.py               股票池來源、資料源設定、品質門檻
  sources/universe.py     S&P 500 成分股 + GICS 類股別（維基百科）
  sources/yahoo_targets.py    Yahoo Finance 共識目標價（免金鑰，預設啟用）
  sources/finnhub_targets.py  Finnhub 共識目標價（選用，需免費API金鑰）
  sources/prices.py       批次收盤價 + 近一週/近一月漲跌幅
  aggregate.py            多來源平均、上漲空間、信心等級（純函式）
  pipeline.py             併發抓取與彙整
  storage.py              輸出到 docs/data/valuation/
scripts/run_valuation.py  進入點
docs/valuation.html + valuation.js   儀表板頁面
```

## 使用方式

```bash
python3 scripts/run_valuation.py -v              # 全部 S&P 500
python3 scripts/run_valuation.py --limit 20 -v   # 只跑前20檔，快速驗證
FINNHUB_API_KEY=xxx python3 scripts/run_valuation.py -v   # 啟用第二個目標價來源
```

## 儀表板功能

- **統計方塊**：資料基準日、涵蓋檔數、中位數上漲空間、共識價高於現價的比例、
  中位數機構數、中位數離散度
- **各類股中位數上漲空間**：橫向長條圖，一眼看出哪些類股整體被看好
- **個股比較表**：代號／公司／類股／收盤價／共識目標價／上漲空間／**機構數**／
  **目標價區間（低－高）**／**離散度**／近一週／近一月／信心等級，所有欄位可點擊排序
- **目標價區間視覺化**：每列有一條小軌道，兩端是最低與最高目標價，
  ● 標出目前收盤價的位置、▍標出共識目標價的位置，一眼看出現價落在分析師預期區間的哪裡
- **篩選**：類股、信心等級、最低上漲空間、**最少機構數**、**最高離散度**、
  代號或公司名稱搜尋、是否隱藏無目標價標的

### 離散度（價格分歧程度）的定義與限制

```
離散度 = (最高目標價 − 最低目標價) / 共識目標價 × 100%
```

數值越大代表各機構對該股的看法越分歧。UI 以顏色分級：≤30% 綠（共識度高）、
30–60% 中性、≥60% 橘（分歧大）。

**這是以區間寬度估算的代理指標，不是統計上的標準差。** 資料來源只公布
最高／最低／平均／中位數，不提供每一家機構的個別目標價，因此無法計算真正的
標準差或四分位距。這個估算會被單一極端值放大，機構數少的時候尤其不穩定，
所以表格把「機構數」放在旁邊，兩個要一起看。

高低價、中位數與離散度一律取自**同一個資料來源**（優先 Yahoo，記錄於 `range_source`
欄位）——分子（最高−最低）與分母（平均）若來自不同的分析師樣本，算出來的數字沒有意義。
但 `consensus_target` 仍是各來源共識價的平均，兩者用途不同。

## 關於「蒐集各財金網站並計算平均」的實作說明

分析師目標價本身就是券商研究報告的產物，各大財金網站（Yahoo Finance、MarketWatch、
TipRanks、Finviz…）呈現的都是**同一批券商報告的彙總共識價**，而非各站獨立的估值。
因此逐站抓取再平均，實際上多半只是把同一份共識重複計算。此外 Finviz、TipRanks 等站的
服務條款明文禁止自動化擷取，也沒有公開 API。

本模組採**可插拔多來源**架構，誠實處理這件事：

| 來源 | 狀態 | 說明 |
|---|---|---|
| Yahoo Finance | 預設啟用，免金鑰 | 共識 mean/median/high/low + 分析師家數 |
| Finnhub | 選用，需 `FINNHUB_API_KEY` | 另一組獨立彙總的共識目標價（免費方案 60 calls/min）|

- 兩個來源都取得時，`consensus_target` 為**兩者的平均**；
- 只有一個來源時就用該來源的值，並在輸出與 UI 上標示 `sources_used`，
  **不會假裝有做多來源平均**。
- 要啟用 Finnhub：到 https://finnhub.io/register 申請免費金鑰，
  在 repo 的 Settings → Secrets and variables → Actions 新增 `FINNHUB_API_KEY`。

## 資料品質處理

- 抓不到目標價或收盤價的標的，`upside_pct` 為 `null`、信心等級標記「暫缺」，
  **不以任何方式填補**，並可在儀表板上選擇顯示或隱藏。
- 信心等級：分析師 ≥5 家且有 2 個來源 → 高；≥5 家但單一來源 → 中；<5 家 → 低。
- 目標價相對現價偏離超過 +300% / −90% 者標記為「疑似資料異常」（保留但註記，不剔除）。
- 維基百科的 `BRK.B` 等代號會轉成 Yahoo Finance 使用的 `BRK-B`，否則抓不到價格。

## 已知限制

1. **目標價不等於價值判斷**：上漲空間為正只代表分析師共識價高於現價，不代表便宜。
   分析師普遍存在偏多傾向，且目標價常在股價變動後才「事後」調整。
2. **股票池為 S&P 500**：不含中小型股與非成分股；成分股清單取自維基百科當前版本，
   屬 look-back bias（歷史回溯時成分股其實會變動）。
3. **Yahoo 未提供目標價的更新日期**，因此無法判斷某檔的共識價是否已數月未更新。
4. 本模組同樣在無對外網路的沙盒中開發，所有邏輯以合成資料測試
   （`tests/test_valuation.py`），真實網站回應格式尚待第一次正式執行後人工核對。

---

## 免責聲明

本專案為上述兩份分析框架的機械化資料爬取與計算實作，**產出結果非投資建議**，
開發者非持牌投資顧問。

模組一無法預警外生衝擊型事件（如疫情、地緣戰爭）；槓桿ETF(TQQQ)具每日重設特性，
長期持有於震盪盤中會產生顯著損耗。使用前請詳閱文件本身第285-292行之誠實原則與
本README之已知限制。

模組二呈現的是分析師共識目標價與市價的差距，**不構成任何個股的買賣建議**；
目標價的準確度沒有保證，且不同分析師的評估基礎與時間範圍可能差異很大。
