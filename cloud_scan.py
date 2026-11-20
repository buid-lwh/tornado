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


# ───────────── 애프터마켓(시간외) 거래 가능 여부 ─────────────
# 2026-09-14부터 KRX 시간외 단일가(16~18시)는 폐지되고 KRX 애프터마켓(16~20시, 실시간 체결)으로 바뀜.
# 관리종목·투자경고·투자위험·거래정지 종목은 KRX 애프터마켓에서 제외.
# NXT(넥스트레이드) 거래 종목은 NXT 애프터마켓(15:30~20시)에서도 거래 가능.
NAVER_LIST = "https://stock.naver.com/api/domestic/market/stock/default"
NAVER_HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://stock.naver.com/"}
ALERT = {"02": "투자경고", "03": "투자위험"}


def _naver_all(trade, order="marketSum"):
    """네이버 증권 종목 목록 → {종목코드: 항목}. startIdx는 페이지 번호(0,1,2…)"""
    import requests
    out = {}
    for page in range(60):
        r = requests.get(f"{NAVER_LIST}?tradeType={trade}&marketType=ALL&orderType={order}"
                         f"&startIdx={page}&pageSize=100", headers=NAVER_HDR, timeout=20)
        r.raise_for_status()
        items = r.json()
        if not isinstance(items, list) or not items:
            break
        for it in items:
            if it.get("itemcode"):
                out[str(it["itemcode"])] = it
        if len(items) < 100:
            break
    return out


def after_market_info():
    """(KRX 전 종목 상태, NXT 종목 상태). 못 받으면 None"""
    krx = nxt = None
    try:
        krx = _naver_all("KRX")
        if len(krx) < 2000:                      # 코스피·코스닥 합쳐 2,500개 이상이어야 정상
            print(f"  [애프터마켓] KRX 목록이 {len(krx)}개뿐이라 믿지 않습니다.")
            krx = None
    except Exception as e:
        print(f"  [애프터마켓] KRX 목록 실패: {e}")
    try:
        nxt = _naver_all("NXT")
        if len(nxt) < 100:                       # NXT 종목은 600개 안팎
            print(f"  [애프터마켓] NXT 목록이 {len(nxt)}개뿐이라 믿지 않습니다.")
            nxt = None
    except Exception as e:
        print(f"  [애프터마켓] NXT 목록 실패: {e}")
    if krx:
        try:                                     # 관리종목 목록 (거래정지 중인 관리종목은 상태값이 달라 목록으로도 확인)
            admin = _naver_all("KRX", "statusTag")
            if 0 < len(admin) < 800:
                for c in admin:
                    if c in krx:
                        krx[c]["_관리"] = True
            else:
                print(f"  [애프터마켓] 관리종목 목록 {len(admin)}개 → 사용 안 함")
        except Exception as e:
            print(f"  [애프터마켓] 관리종목 목록 실패: {e}")
        cnt = {"관리종목": 0, "거래정지": 0, "투자경고": 0, "투자위험": 0}
        for it in krx.values():
            for k in _block_reasons(it):
                cnt[k] = cnt.get(k, 0) + 1
        print("  [애프터마켓] KRX " + f"{len(krx)}종목 중 " + " · ".join(f"{k} {v}" for k, v in cnt.items())
              + f" / NXT {'실패' if nxt is None else len(nxt)}종목", flush=True)
    return krx, nxt


def _block_reasons(it):
    reasons = []
    if it.get("manageStatusGb") == "1" or it.get("_관리"):
        reasons.append("관리종목")
    if it.get("tradeStopYn") == "Y" or it.get("tradableStatus") not in (None, "tradable"):
        reasons.append("거래정지")
    if it.get("marketAlertType") in ALERT:
        reasons.append(ALERT[it["marketAlertType"]])
    return reasons


def after_market_tag(code, krx, nxt):
    n = None
    if nxt is not None:
        ni = nxt.get(code)
        n = bool(ni) and ni.get("tradeStopYn") != "Y" and ni.get("tradableStatusCode") == "ok"
    if krx is None or code not in krx:
        return {"가능": None, "사유": "확인 못함", "NXT": n}
    reasons = _block_reasons(krx[code])
    if reasons:
        return {"가능": False, "사유": "·".join(reasons), "NXT": n}
    return {"가능": True, "사유": "", "NXT": n}


def refresh_after_market_only():
    """장 마감 전: 검색은 하지 않고, 저장된 최신 결과의 애프터마켓 표시만 다시 확인"""
    path = os.path.join(OUT_DIR, "latest.json")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fp:
        result = json.load(fp)
    krx, nxt = after_market_info()
    for k in ("종목", "매도", "매도주의", "순위", "20일선재돌파", "역배열돌파"):
        for row in result.get(k) or []:
            row["애프터"] = after_market_tag(str(row["종목코드"]), krx, nxt)
    day = result["기준일"].replace("-", "")
    for name in ["latest.json", f"{day}.json"]:
        with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as fp:
            json.dump(result, fp, ensure_ascii=False, indent=1)
    print(f"{result['기준일']} 결과의 애프터마켓 표시만 갱신했습니다.")


def main():
    now = dt.datetime.now()
    if now.weekday() < 5 and (now.hour, now.minute) < (15, 35):
        # 장중에는 거래소 시세가 '오늘 장중 가격'이라 전날 봉을 덮어쓰는 문제가 있어 검색하지 않음
        print("장 마감(15:30) 전이라 전 종목 검색은 건너뜁니다.")
        refresh_after_market_only()
        return
    import 토네이도 as tt                     # 세 검색기(토네이도·20일선 재돌파·역배열 돌파)를 한 번에
    u = get_universe()
    res, trade_day, total, fails = tt.run_all(u, log=lambda m: print(m, flush=True))

    try:
        am_lists, am_nxt = after_market_info()
    except Exception as e:
        print(f"  [애프터마켓] 확인 실패: {e}")
        am_lists, am_nxt = None, None

    def recs(name):
        rows = [{k: clean(v) for k, v in row.items()} for row in res[name].to_dict("records")]
        for row in rows:
            row["애프터"] = after_market_tag(str(row["종목코드"]), am_lists, am_nxt)
        return rows

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
        "순위": recs("토네이도 순위"),
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
          f" · 순위 {len(result['순위'])}"
          f" · 20일선 재돌파 {len(result['20일선재돌파'])} · 역배열 돌파 {len(result['역배열돌파'])}"
          f" → data/{day}.json 저장 (데이터 실패 {fails}건)")


if __name__ == "__main__":
    main()
