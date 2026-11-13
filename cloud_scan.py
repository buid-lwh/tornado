# -*- coding: utf-8 -*-
"""
클라우드(GitHub Actions)용 전 종목 검색 → 스마트폰 앱이 읽을 JSON 저장
 - 검색 조건·계산은 tornado_screener.py 그대로 사용 (PC 버전과 결과 동일)
 - 결과: data/latest.json, data/YYYYMMDD.json, data/index.json
"""
import os
import sys
import json
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

os.environ.setdefault("TZ", "Asia/Seoul")
try:
    import time
    time.tzset()
except AttributeError:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tornado_screener as ts  # noqa: E402

OUT_DIR = os.path.join(HERE, "data")
KEEP_DAYS = 120   # 앱에서 볼 수 있는 과거 결과 개수


def universe_naver():
    """거래소 목록을 못 받을 때 대비: 네이버 증권 시가총액 목록으로 대체"""
    import requests
    rows = []
    for mkt, label in [("KOSPI", "거래소"), ("KOSDAQ", "코스닥")]:
        page = 1
        while True:
            url = f"https://m.stock.naver.com/api/stocks/marketValue/{mkt}?page={page}&pageSize=100"
            js = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20).json()
            items = js.get("stocks") or []
            if not items:
                break
            for it in items:
                rows.append({"Code": it.get("itemCode"), "Name": it.get("stockName"), "시장": label})
            if page * 100 >= int(js.get("totalCount", 0)):
                break
            page += 1
    u = pd.DataFrame(rows).dropna()
    u = u[u["Code"].str.fullmatch(r"\d{5}0")]
    u = u[~u["Name"].str.contains("스팩|리츠|ETN|우$|우B$", regex=True)]
    return u.drop_duplicates("Code").reset_index(drop=True)


def get_universe():
    try:
        u = ts.get_universe()
        if len(u) > 1000:
            return u
    except Exception as e:
        print(f"거래소 목록 실패 → 네이버로 대체 ({e})")
    return universe_naver()


def clean(v):
    if isinstance(v, (pd.Timestamp, dt.date)):
        return v.isoformat()[:10]
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and v != v:
        return None
    return v


def main():
    u = get_universe()
    trade_day = ts.last_trade_day()
    print(f"검색 대상 {len(u)}종목 · 기준일 {trade_day.date()}")
    rows, sells, warns, fails = [], [], [], 0
    with ThreadPoolExecutor(ts.WORKERS) as ex:
        futs = {ex.submit(ts.fast_fetch, r, trade_day): r for r in u.itertuples()}
        for i, f in enumerate(as_completed(futs), 1):
            r = futs[f]
            if i % 200 == 0:
                print(f"  {i}/{len(u)} 완료", flush=True)
            try:
                df = ts.compute(f.result())
            except Exception:
                fails += 1
                continue
            if df is None:
                continue
            for k in range(1, ts.SIGNAL_DAYS + 1):
                if df["signal"].iloc[-k]:
                    row = {"시장": r.시장, "종목코드": r.Code, "종목명": r.Name,
                           "신호일": df.index[-k].date(), **ts.row_of(df, -k)}
                    row.pop("정배열", None)
                    rows.append({key: clean(val) for key, val in row.items()})
                    break
            s = ts.sell_row(df)
            if s:
                row = {"시장": r.시장, "종목코드": r.Code, "종목명": r.Name, **s}
                sells.append({key: clean(val) for key, val in row.items()})
            w = ts.warn_row(df) if ts.WARN_GAP else None
            if w:
                row = {"시장": r.시장, "종목코드": r.Code, "종목명": r.Name, **w}
                warns.append({key: clean(val) for key, val in row.items()})

    rows.sort(key=lambda x: (x["등급"], x["두 묶음 간격(%)"], -x["등락률(%)"]))
    sells.sort(key=lambda x: x["매수 신호일"])
    warns.sort(key=lambda x: x["5·10 간격(%)"])
    now = dt.datetime.now()
    result = {
        "기준일": trade_day.date().isoformat(),
        "생성시각": now.strftime("%Y-%m-%d %H:%M"),
        "검색종목수": int(len(u)),
        "데이터실패": fails,
        "조건": {"GAP_TOL": ts.GAP_TOL, "SIGNAL_DAYS": ts.SIGNAL_DAYS, "VOL_MIN": ts.VOL_MIN,
                 "SELL_LOOKBACK": ts.SELL_LOOKBACK, "WARN_GAP": ts.WARN_GAP},
        "종목": rows,
        "매도": sells,
        "매도주의": warns,
    }
    if fails > len(u) * 0.5:
        sys.exit(f"데이터 실패가 너무 많아({fails}건) 결과를 저장하지 않습니다.")

    os.makedirs(OUT_DIR, exist_ok=True)
    day = trade_day.strftime("%Y%m%d")
    for name in ["latest.json", f"{day}.json"]:
        with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as fp:
            json.dump(result, fp, ensure_ascii=False, indent=1)

    days = sorted((f[:8] for f in os.listdir(OUT_DIR) if f[:8].isdigit() and f.endswith(".json")),
                  reverse=True)
    for old in days[KEEP_DAYS:]:
        os.remove(os.path.join(OUT_DIR, f"{old}.json"))
    days = days[:KEEP_DAYS]
    with open(os.path.join(OUT_DIR, "index.json"), "w", encoding="utf-8") as fp:
        json.dump({"dates": days}, fp)
    print(f"\n매수 {len(rows)} · 매도 {len(sells)} · 주의 {len(warns)}종목 → data/{day}.json 저장 (데이터 실패 {fails}건)")


if __name__ == "__main__":
    main()
