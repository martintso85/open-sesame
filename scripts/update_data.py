# -*- coding: utf-8 -*-
"""AI 台股六條件選股 Dashboard 每日資料更新腳本。

資料來源(皆為 TWSE 公開端點):
  - STOCK_DAY_ALL : 全部個股盤後行情(OpenAPI)
  - T86           : 三大法人買賣超(投信買賣超股數)
  - MI_MARGN      : 融資融券餘額(大盤彙總 + 個股)
  - FMTQIK        : 大盤加權指數每日成交量值與收盤指數
  - STOCK_DAY     : 個股日K(僅對排名前 HISTORY_TOP 檔抓近幾個月,計算均線/KD/均量/相對大盤)

任一來源抓取失敗時不會中斷:對應欄位標示「資料待補」,其餘照常輸出。
"""

import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(BASE_DIR, "data", "dashboard.json")

STOCK_DAY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86?date={date}&selectType=ALLBUT0999&response=json"
MI_MARGN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={date}&selectType={sel}&response=json"
FMTQIK_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={date}&response=json"
STOCK_DAY_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date={date}&stockNo={code}&response=json"

TOP_N = int(os.getenv("TOP_N", "200"))  # 清單保留檔數
HISTORY_TOP = int(os.getenv("HISTORY_TOP", "30"))  # 可由 Actions 環境變數調整
HISTORY_MONTHS = 3   # 個股歷史抓幾個月
MARKET_MONTHS = 4    # 大盤歷史抓幾個月(60日均線需要)
MARGIN_TREND_DAYS = 12   # 大盤融資趨勢往回抓幾個「日曆日」
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.4"))  # 對 rwd 端點的禮貌延遲(秒)

VOL_SURGE_5 = 1.5    # 個股:今日量 >= 1.5 倍 5 日均量 → 量增
VOL_SURGE_20 = 2.0   # 個股:今日量 >= 2.0 倍 20 日均量 → 量增
MKT_VOL_SURGE = 1.3  # 大盤:成交值 >= 1.3 倍 5 日均值 → 量增
MARGIN_FLAG_PCT = 5.0  # 個股融資餘額單日增減 >= 5% → 明顯放大

PENDING = "資料待補"


def fetch_json(url, timeout=30, retries=2):
    """抓取 JSON,失敗回傳 None(不中斷整體流程)。"""
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if i == retries:
                print(f"[warn] fetch failed: {url} ({e})")
                return None
            time.sleep(1.5 * (i + 1))
    return None


def to_float(s):
    try:
        return float(str(s).replace(",", "").replace("+", ""))
    except Exception:
        return None


def roc_date(s):
    """民國日期字串(115/08/29)轉 ISO(2026-08-29);失敗回傳原字串。"""
    try:
        parts = str(s).replace(".", "/").split("/")
        y = int(parts[0]) + 1911
        return f"{y:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    except Exception:
        return str(s)


# ---------- 各資料來源 ----------

def fetch_base_stocks():
    """STOCK_DAY_ALL → 上市普通股盤後行情。

    候選排序使用漲幅百分比，再以成交金額與成交量破同分。原本用漲跌
    金額排序會系統性偏向高價股，讓後續只計算前幾名技術指標的結果失真。
    """
    data = fetch_json(STOCK_DAY_ALL_URL)
    if not data:
        return None
    stocks = []
    for x in data:
        try:
            code = x.get("Code", "")
            name = x.get("Name", "")
            close = to_float(x.get("ClosingPrice", "0")) or 0
            open_ = to_float(x.get("OpeningPrice", "0")) or 0
            volume = (to_float(x.get("TradeVolume", "0")) or 0) / 1000  # 張
            trade_value = (to_float(x.get("TradeValue", "0")) or 0) / 1e8  # 億元
            change = to_float(x.get("Change", "0")) or 0
            # 台股普通股為 4 位純數字；排除 ETF、ETN、權證與特殊證券。
            if (len(code) != 4 or not code.isdigit() or code.startswith("0")
                    or not name or close <= 0):
                continue
            prev = close - change
            stocks.append({
                "code": code,
                "name": name,
                "close": close,
                "open": open_,
                "volume": round(volume, 2),
                "trade_value": round(trade_value, 2),
                "change": change,
                "change_pct": round(change / prev * 100, 2) if prev > 0 else 0,
            })
        except Exception:
            continue
    stocks.sort(
        key=lambda s: (s["change_pct"], s["trade_value"], s["volume"]),
        reverse=True,
    )
    return stocks[:TOP_N]


def fetch_trust_net():
    """T86 → {code: 投信買賣超(張)}。往回最多找 10 天取得最近一個交易日。"""
    today = datetime.now(timezone(timedelta(hours=8)))
    for back in range(10):
        d = (today - timedelta(days=back)).strftime("%Y%m%d")
        resp = fetch_json(T86_URL.format(date=d))
        time.sleep(REQUEST_DELAY)
        if not resp or resp.get("stat") != "OK" or not resp.get("data"):
            continue
        fields = resp.get("fields", [])
        try:
            idx = fields.index("投信買賣超股數")
        except ValueError:
            continue
        result = {}
        for row in resp["data"]:
            code = str(row[0]).strip()
            v = to_float(row[idx])
            if code and v is not None:
                result[code] = round(v / 1000)  # 股 → 張
        if result:
            print(f"[info] T86 date={d} rows={len(result)}")
            return result, roc_or_ymd(d)
    return None, None


def roc_or_ymd(ymd):
    return f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"


def parse_mi_margn(resp):
    """MI_MARGN 回應 → (大盤彙總 dict, 個股 dict)。任一缺少回傳 None。"""
    if not resp or resp.get("stat") != "OK":
        return None, None
    summary, per_stock = None, None
    for t in resp.get("tables", []):
        fields = t.get("fields", [])
        data = t.get("data", [])
        if not data:
            continue
        if "股票代號" in fields:
            # 融資融券彙總(個股):前日餘額/今日餘額 取「融資」區塊(最先出現者)
            try:
                i_prev = fields.index("前日餘額")
                i_today = fields.index("今日餘額")
            except ValueError:
                continue
            per_stock = {}
            for row in data:
                code = str(row[0]).strip()
                prev = to_float(row[i_prev])
                cur = to_float(row[i_today])
                if code and prev is not None and cur is not None:
                    per_stock[code] = {"prev": prev, "today": cur}
        else:
            # 信用交易統計:列首為 項目(融資金額(仟元) 等)
            for row in data:
                item = str(row[0])
                if "融資金額" in item:
                    prev = to_float(row[-2])
                    cur = to_float(row[-1])
                    if prev is not None and cur is not None:
                        summary = {
                            "prev": round(prev / 100000, 1),   # 仟元 → 億元
                            "today": round(cur / 100000, 1),
                        }
    return summary, per_stock


def fetch_margin():
    """MI_MARGN → (大盤融資趨勢 history, 個股融資 dict)。"""
    today = datetime.now(timezone(timedelta(hours=8)))
    history = []      # [{date, balance(億元)}] 由舊到新
    per_stock = None
    for back in range(MARGIN_TREND_DAYS):
        d = (today - timedelta(days=back)).strftime("%Y%m%d")
        sel = "ALL" if per_stock is None else "MS"
        resp = fetch_json(MI_MARGN_URL.format(date=d, sel=sel))
        time.sleep(REQUEST_DELAY)
        summary, stocks = parse_mi_margn(resp)
        if stocks and per_stock is None:
            per_stock = stocks
        if summary:
            history.append({"date": roc_or_ymd(d), "balance": summary["today"],
                            "prev_balance": summary["prev"]})
    history.reverse()
    return (history or None), per_stock


def fetch_market_history():
    """FMTQIK(近幾個月)→ 大盤每日 [{date, index, change, amount(億元)}]。"""
    today = datetime.now(timezone(timedelta(hours=8)))
    rows = []
    for m in range(MARKET_MONTHS - 1, -1, -1):
        y, mo = today.year, today.month - m
        while mo <= 0:
            y, mo = y - 1, mo + 12
        d = f"{y:04d}{mo:02d}01"
        resp = fetch_json(FMTQIK_URL.format(date=d))
        time.sleep(REQUEST_DELAY)
        if not resp or resp.get("stat") != "OK" or not resp.get("data"):
            continue
        for row in resp["data"]:
            # [日期, 成交股數, 成交金額, 成交筆數, 發行量加權股價指數, 漲跌點數]
            idx = to_float(row[4])
            amt = to_float(row[2])
            chg = to_float(row[5])
            if idx is None or amt is None:
                continue
            rows.append({
                "date": roc_date(row[0]),
                "index": idx,
                "change": chg or 0,
                "amount": round(amt / 1e8, 1),  # 元 → 億元
            })
    return rows or None


def fetch_stock_history(code):
    """STOCK_DAY(近幾個月)→ [{date, close, high, low, volume(張)}] 由舊到新。"""
    today = datetime.now(timezone(timedelta(hours=8)))
    rows = []
    for m in range(HISTORY_MONTHS - 1, -1, -1):
        y, mo = today.year, today.month - m
        while mo <= 0:
            y, mo = y - 1, mo + 12
        d = f"{y:04d}{mo:02d}01"
        resp = fetch_json(STOCK_DAY_URL.format(date=d, code=code))
        time.sleep(REQUEST_DELAY)
        if not resp or resp.get("stat") != "OK" or not resp.get("data"):
            continue
        for row in resp["data"]:
            # [日期, 成交股數, 成交金額, 開盤, 最高, 最低, 收盤, 漲跌價差, 成交筆數]
            close = to_float(row[6])
            high = to_float(row[4])
            low = to_float(row[5])
            vol = to_float(row[1])
            if close is None or high is None or low is None:
                continue  # 停牌日 "--"
            rows.append({
                "date": roc_date(row[0]),
                "close": close, "high": high, "low": low,
                "volume": round((vol or 0) / 1000, 2),
            })
    return rows


# ---------- 指標計算 ----------

def sma(values, n, offset=0):
    """最後第 offset 筆往前 n 筆的簡單平均;資料不足回傳 None。"""
    end = len(values) - offset
    if end - n < 0:
        return None
    seg = values[end - n:end]
    return sum(seg) / n


def calc_kd(rows, n=9):
    """KD(9,3,3)。回傳 (K序列, D序列);資料不足回傳 (None, None)。"""
    if len(rows) < n + 1:
        return None, None
    ks, ds = [50.0], [50.0]
    for i in range(n - 1, len(rows)):
        window = rows[i - n + 1:i + 1]
        hi = max(r["high"] for r in window)
        lo = min(r["low"] for r in window)
        rsv = 50.0 if hi == lo else (rows[i]["close"] - lo) / (hi - lo) * 100
        ks.append(ks[-1] * 2 / 3 + rsv / 3)
        ds.append(ds[-1] * 2 / 3 + ks[-1] / 3)
    return ks, ds


def trend_up(closes, n):
    """收盤在 n 日均線上,且均線向上(高於 3 根前的均線)。資料不足回傳 None。"""
    ma_now = sma(closes, n)
    ma_prev = sma(closes, n, offset=3)
    if ma_now is None or ma_prev is None:
        return None
    return closes[-1] > ma_now and ma_now > ma_prev


def analyze_stock_history(rows, market_ret20):
    """由個股日K計算技術指標欄位。rows 需由舊到新且包含今日。"""
    out = {}
    closes = [r["close"] for r in rows]
    vols = [r["volume"] for r in rows]

    # 月線(20MA)/週線(5MA)趨勢
    m_up = trend_up(closes, 20)
    w_up = trend_up(closes, 5)
    out["month_up"], out["week_up"] = m_up, w_up
    if m_up is None or w_up is None:
        out["month_week"] = "資料不足"
    else:
        out["month_week"] = f"月{'↑' if m_up else '↓'}/週{'↑' if w_up else '↓'}"

    # KD 黃金交叉(3 日內 K 上穿 D 且目前 K>D)
    ks, ds = calc_kd(rows)
    if ks is None or len(ks) < 4:
        out["kd"], out["kd_golden"] = "資料不足", None
    else:
        # 3 日內 K 由下(或貼近 D 的交叉帶)上穿:留 0.5 的容忍避免臨界值抖動漏判
        golden = ks[-1] > ds[-1] and any(ks[-i] <= ds[-i] + 0.5 for i in range(2, 5))
        out["k"], out["d"] = round(ks[-1], 1), round(ds[-1], 1)
        out["kd_golden"] = golden
        # 全形「＜」:避免半形 < 在網頁被當成 HTML 標籤
        out["kd"] = (f"K{out['k']}/D{out['d']}" +
                     ("〔黃金交叉〕" if golden else "" if ks[-1] > ds[-1] else "〔K＜D〕"))

    # 量增:今日量 vs 5/20 日均量(不含今日)
    avg5 = sma(vols[:-1], 5)
    avg20 = sma(vols[:-1], 20)
    vol = vols[-1]
    if avg5:
        ratio = vol / avg5
        out["vol_ratio"] = round(ratio, 2)
        out["vol_surge"] = ratio >= VOL_SURGE_5 or (bool(avg20) and vol >= VOL_SURGE_20 * avg20)
    else:
        out["vol_ratio"], out["vol_surge"] = None, None

    # 近 20 日漲幅 vs 大盤
    if len(closes) >= 21 and closes[-21] > 0:
        ret20 = (closes[-1] / closes[-21] - 1) * 100
        out["ret20_pct"] = round(ret20, 1)
        if market_ret20 is not None:
            rel = ret20 - market_ret20
            out["rel_pct"] = round(rel, 1)
            out["outperform"] = rel > 0
            out["relative"] = f"{ret20:+.1f}% vs 大盤{market_ret20:+.1f}%"
        else:
            out["relative"] = f"20日{ret20:+.1f}%(大盤{PENDING})"
    else:
        out["relative"] = "資料不足"
    return out


def analyze_market(mkt_rows):
    """由大盤日資料計算指數趨勢與量能。"""
    if not mkt_rows:
        return None
    closes = [r["index"] for r in mkt_rows]
    amounts = [r["amount"] for r in mkt_rows]
    last = mkt_rows[-1]
    prev_close = closes[-2] if len(closes) > 1 else None
    m = {
        "date": last["date"],
        "index": last["index"],
        "change": last["change"],
        "change_pct": round(last["change"] / (last["index"] - last["change"]) * 100, 2)
        if last["index"] != last["change"] else None,
        "amount": last["amount"],
    }
    m["ma20"] = round(sma(closes, 20), 0) if sma(closes, 20) else None
    m["ma60"] = round(sma(closes, 60), 0) if sma(closes, 60) else None
    m["trend_up"] = trend_up(closes, 20)
    m["above_ma60"] = (closes[-1] > m["ma60"]) if m["ma60"] else None
    avg5 = sma(amounts[:-1], 5)
    avg20 = sma(amounts[:-1], 20)
    m["amount_avg5"] = round(avg5, 1) if avg5 else None
    m["amount_avg20"] = round(avg20, 1) if avg20 else None
    if avg5:
        m["vol_ratio"] = round(last["amount"] / avg5, 2)
        m["vol_surge"] = m["vol_ratio"] >= MKT_VOL_SURGE
    else:
        m["vol_ratio"], m["vol_surge"] = None, None
    if len(closes) >= 21 and closes[-21] > 0:
        m["ret20_pct"] = round((closes[-1] / closes[-21] - 1) * 100, 1)
    else:
        m["ret20_pct"] = None
    return m


def build_reason(s):
    """個股跑贏大盤時,組出「最新理由(積極做多誘因)」的量化摘要。"""
    parts = []
    if s.get("rel_pct") is not None:
        parts.append(f"20日漲幅{s['ret20_pct']:+.1f}%,跑贏大盤 {s['rel_pct']:+.1f} 個百分點")
    if (s.get("trust_net") or 0) > 0:
        parts.append(f"投信買超 {s['trust_net']:,} 張")
    if s.get("vol_surge"):
        parts.append(f"量能放大({s['vol_ratio']} 倍 5 日均量)")
    if s.get("kd_golden"):
        parts.append("KD 黃金交叉")
    if s.get("margin_flag"):
        parts.append(f"融資{s['margin_flag']}")
    return ";".join(parts) if parts else None


# ---------- 主流程 ----------

def main():
    stocks = fetch_base_stocks()
    if stocks is None:
        # 行情來源失敗時退回既有檔案的清單,僅更新可取得的欄位
        print("[warn] STOCK_DAY_ALL unavailable, falling back to existing dashboard.json")
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                old = json.load(f)
            stocks = [{k: s.get(k) for k in
                       ("code", "name", "close", "volume", "trade_value", "change", "change_pct")}
                      for s in old.get("stocks", [])]
        except Exception:
            stocks = []

    trust, trust_date = fetch_trust_net()
    margin_history, margin_stocks = fetch_margin()
    mkt_rows = fetch_market_history()
    market = analyze_market(mkt_rows)
    market_ret20 = market.get("ret20_pct") if market else None
    market_trend_up = market.get("trend_up") if market else None

    # 大盤融資趨勢
    margin_summary = None
    if margin_history:
        latest = margin_history[-1]
        diff = round(latest["balance"] - latest["prev_balance"], 1)
        trend = None
        if len(margin_history) >= 5:
            d5 = latest["balance"] - margin_history[-5]["balance"]
            trend = "增" if d5 > 0 else "減" if d5 < 0 else "平"
        margin_summary = {
            "balance": latest["balance"],       # 億元
            "change": diff,
            "trend": trend,
            "history": margin_history,
        }

    for i, s in enumerate(stocks):
        s["trust_net"] = trust.get(s["code"], 0) if trust else None

        # 個股融資餘額增減
        if margin_stocks and s["code"] in margin_stocks:
            ms = margin_stocks[s["code"]]
            s["margin_balance"] = ms["today"]
            if ms["prev"] > 0:
                pct = (ms["today"] - ms["prev"]) / ms["prev"] * 100
                s["margin_change_pct"] = round(pct, 1)
                s["margin_flag"] = ("大增" if pct >= MARGIN_FLAG_PCT
                                    else "大減" if pct <= -MARGIN_FLAG_PCT else None)
                s["margin"] = f"{ms['today']:,.0f} 張({pct:+.1f}%)"
            else:
                s["margin"] = f"{ms['today']:,.0f} 張"
        elif margin_stocks:
            s["margin"] = "無融資"
        else:
            s["margin"] = PENDING

        # 技術指標:僅前 HISTORY_TOP 名抓歷史計算
        if i < HISTORY_TOP:
            rows = fetch_stock_history(s["code"])
            if rows and len(rows) >= 6:
                s.update(analyze_stock_history(rows, market_ret20))
            else:
                s["month_week"] = s["kd"] = s["relative"] = PENDING
        else:
            s["month_week"] = s["kd"] = s["relative"] = f"僅前{HISTORY_TOP}名計算"

        # 六條件中的優先買入四條件
        conds = {
            "ma": bool(s.get("month_up")) and bool(s.get("week_up")),
            "kd": bool(s.get("kd_golden")),
            "trust": bool(s.get("trust_net") and s["trust_net"] > 0),
            "market": bool(market_trend_up),
        }
        s["conditions"] = conds
        s["cond_count"] = sum(conds.values())
        s["priority"] = all(conds.values())
        s["status"] = "優先買入" if s["priority"] else "觀察"

        # 跑贏大盤 → 理由欄(量化摘要;新聞來源尚未接入,標示待補)
        if s.get("outperform"):
            s["reason"] = build_reason(s)
            s["news"] = PENDING
        else:
            s["reason"] = None

    if trust is None:
        print("[warn] T86 unavailable → 投信欄位標示待補")
    result = {
        "schema_version": 2,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "data_date": market.get("date") if market else trust_date,
        "trust_date": trust_date,
        "methodology": {
            "universe": "TWSE 上市普通股",
            "candidate_rank": "單日漲幅百分比、成交金額、成交量",
            "history_count": HISTORY_TOP,
            "priority_conditions": ["月週線向上", "KD黃金交叉", "投信買超", "大盤轉強"],
        },
        "market": {
            "source": "TWSE",
            "finance_today": margin_summary["balance"] * 100 if margin_summary else None,  # 相容舊欄位(百萬元)
            "margin": margin_summary,
            "quote": market,
        },
        "stocks": stocks,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    n_pri = sum(1 for s in stocks if s.get("priority"))
    print(f"[done] stocks={len(stocks)} priority={n_pri} market={'ok' if market else PENDING}")


if __name__ == "__main__":
    main()
