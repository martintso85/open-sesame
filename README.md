# 開盤密碼｜台股六條件量化雷達

以 GitHub Pages 顯示、由 GitHub Actions 每日盤後更新的台股量化 Scanner。資料來自 TWSE 公開端點；不是盤中即時報價，也不是投資建議。

## 判定方法

個股六條件：

1. 月線與週線趨勢向上
2. KD 在本交易日嚴格黃金交叉
3. 投信買超
4. 量能相對 5 日或 20 日均量放大
5. 近 20 日漲幅跑贏加權指數
6. 融資健康：單日增幅低於 5%

大盤獨立作為 **Market Gate**：20 日趨勢向上且指數站上 60 日均線才是 PASS。Market Gate PASS 且個股至少通過 5/6，才標示為「⭐ 優先觀察」。資料不足以 `—` 呈現，不直接視為條件失敗。

## 排序與介面

- 後端輸出排序：優先觀察 → 六條件通過數 → Score → 漲幅% → 成交量。
- 可搜尋股票代號或名稱，篩選最低 3/6、4/6、5/6、6/6。
- 可依條件分數、漲幅、成交量或投信買超排序。
- 行情、投信、融資各自顯示資料日期。
- 按鈕「重新載入」只重新讀取已產生的 JSON，不會觸發 GitHub Actions。

## 資料範圍

目前 Universe 是 **TWSE 上市四碼普通股**，不包含：

- TPEx 上櫃股票
- ETF / ETN / 權證
- 新聞與盤中即時行情

資料來源：`STOCK_DAY_ALL`、`T86`、`MI_MARGN`、`FMTQIK`、`STOCK_DAY`。更新程式在 `scripts/update_data.py`，輸出至 `data/dashboard.json`。

## 自動更新

- 台北時間平日 16:30：第一版 40 檔。
- 台北時間平日 21:30：完整版 80 檔。
- 工作流程設有 concurrency、15 分鐘 timeout、JSON 驗證與單元測試。
- 可用 `TOP_N`、`HISTORY_TOP`、`HISTORY_WORKERS`、`REQUEST_DELAY` 調整執行規模。

部署時在 GitHub repository 的 Pages 設定中，選擇從 `main` branch 根目錄發布；首次可手動執行 `Daily Taiwan Stock Update`。
