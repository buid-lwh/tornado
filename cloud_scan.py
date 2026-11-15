# -*- coding: utf-8 -*-
"""
클라우드(GitHub Actions)용 전 종목 검색 → 스마트폰 앱이 읽을 JSON 저장
 - 토네이도.py 로 세 검색기(토네이도·20일선 재돌파·역배열 돌파)를 한 번에 실행 (PC 버전과 결과 동일)
 - 결과: data/latest.json, data/YYYYMMDD.json, data/index.json
"""
import os
import sys
import json
import datetime as dt

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
    import 토네이도 as tt                     # 세 검색기(토네이도·20일선 재돌파·역배열 돌파)를 한 번에
    u = get_universe()
    res, trade_day, total, fails = tt.run_all(u, log=lambda m: print(m, flush=True))

    def recs(name):
        return [{k: clean(v) for k, v in row.items()} for row in res[name].to_dict("records")]

    now = dt.datetime.now()
    result = {
        "기준일": trade_day.date().isoformat(),
        "생성시각": now.strftime("%Y-%m-%d %H:%M"),
        "검색종목수": int(total),
        "데이터실패": fails,
        "조건": {"GAP_TOL": ts.GAP_TOL, "SIGNAL_DAYS": ts.SIGNAL_DAYS, "VOL_MIN": ts.VOL_MIN,
                 "SELL_LOOKBACK": ts.SELL_LOOKBACK, "WARN_GAP": ts.WARN_GAP},
        "종목": recs("토네이도 매수"),
        "매도": recs("토네이도 매도"),
        "매도주의": recs("토네이도 매도주의"),
        "20일선재돌파": recs("20일선 재돌파"),
        "역배열돌파": recs("역배열 돌파"),
    }
    if fails > total * 0.5:
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
    print(f"\n토네이도 매수 {len(result['종목'])} · 매도 {len(result['매도'])} · 주의 {len(result['매도주의'])}"
          f" · 20일선 재돌파 {len(result['20일선재돌파'])} · 역배열 돌파 {len(result['역배열돌파'])}"
          f" → data/{day}.json 저장 (데이터 실패 {fails}건)")


if __name__ == "__main__":
    main()
