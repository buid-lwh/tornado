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
NAVER_HOSTS = ["https://stock.naver.com", "https://m.stock.naver.com"]
NAVER_HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://stock.naver.com/"}


def _codes_in(js):
    """응답 JSON 안의 종목코드(6자리)를 모두 찾기 (응답 구조가 바뀌어도 동작하도록)"""
    out = set()
    if isinstance(js, dict):
        for k, v in js.items():
            if "code" in str(k).lower() and isinstance(v, str) and len(v) == 6 and v.isdigit():
                out.add(v)
        for v in js.values():
            if isinstance(v, (dict, list)):
                out |= _codes_in(v)
    elif isinstance(js, list):
        for v in js:
            out |= _codes_in(v)
    return out


def _naver_list(order, trade="KRX", extra="", max_pages=15):
    """네이버 증권 종목 목록(관리종목·거래정지·투자경고 등) 전체 코드 → set. 실패하면 None"""
    import requests
    for host in NAVER_HOSTS:
        codes, ok = set(), False
        try:
            for page in range(max_pages):
                url = (f"{host}/api/domestic/market/stock/default?tradeType={trade}&marketType=ALL"
                       f"&orderType={order}{extra}&startIdx={page * 100}&pageSize=100")
                r = requests.get(url, headers=NAVER_HDR, timeout=20)
                r.raise_for_status()
                js = r.json()
                if page == 0:
                    print(f"  [애프터마켓] {order}{extra} {trade} 응답: {json.dumps(js, ensure_ascii=False)[:300]}",
                          flush=True)
                got = _codes_in(js)
                ok = True
                if not got - codes:
                    break
                codes |= got
                if len(got) < 100:
                    break
            if ok:
                return codes
        except Exception as e:
            print(f"  [애프터마켓] {host} {order}{extra} 실패: {e}", flush=True)
    return None


def after_market_info():
    """{'관리종목': set, ...} 와 NXT 종목 set. 목록을 못 받은 항목은 None"""
    lists = {
        "관리종목": _naver_list("statusTag"),
        "거래정지": _naver_list("tradeStopYn"),
        "투자경고": _naver_list("marketAlertType", extra="&alertType=02"),
        "투자위험": _naver_list("marketAlertType", extra="&alertType=03"),
    }
    for k, v in lists.items():                  # 전 종목이 섞여 오면(목록 필터가 안 먹으면) 믿지 않음
        if v is not None and len(v) > 800:
            print(f"  [애프터마켓] {k} 목록이 {len(v)}개로 너무 많아 무시합니다.")
            lists[k] = None
    for k in ("관리종목", "거래정지"):             # 이 둘은 항상 수십 종목 이상 → 0개면 응답을 잘못 읽은 것
        if lists[k] is not None and not lists[k]:
            print(f"  [애프터마켓] {k} 0개 → 응답 형식을 못 읽은 것으로 보고 '확인 못함' 처리")
            lists[k] = None
    nxt = _naver_list("marketSum", trade="NXT")
    if nxt is not None and len(nxt) < 100:      # NXT 종목은 600개 안팎 → 너무 적으면 잘못 받은 것
        nxt = None
    print("  [애프터마켓] " + " · ".join(f"{k} {'실패' if v is None else len(v)}" for k, v in
                                     {**lists, "NXT": nxt}.items()), flush=True)
    return lists, nxt


def after_market_tag(code, lists, nxt):
    reasons = [k for k, v in lists.items() if v is not None and code in v]
    is_nxt = None if nxt is None else code in nxt
    if reasons:
        return {"가능": False, "사유": "·".join(reasons), "NXT": is_nxt}
    if any(v is None for v in lists.values()):
        return {"가능": None, "사유": "확인 못함", "NXT": is_nxt}
    return {"가능": True, "사유": "", "NXT": is_nxt}


def _probe():
    """임시 점검: 네이버 목록 응답 형식과 페이지 넘김 방식 확인"""
    import requests
    base = "https://stock.naver.com/api/domestic/market/stock/default"
    for q in ["tradeType=KRX&marketType=ALL&orderType=statusTag",
              "tradeType=KRX&marketType=ALL&orderType=marketAlertType&alertType=02",
              "tradeType=NXT&marketType=ALL&orderType=marketSum",
              "tradeType=KRX&marketType=ALL&orderType=marketSum"]:
        for st in (0, 1, 2, 100):
            try:
                js = requests.get(f"{base}?{q}&startIdx={st}&pageSize=100", headers=NAVER_HDR, timeout=20).json()
                items = js if isinstance(js, list) else []
                mg = {}
                for it in items:
                    key = (it.get("manageStatusGb"), it.get("tradeStopYn"), it.get("tradableStatusCode"))
                    mg[str(key)] = mg.get(str(key), 0) + 1
                print(f"  [점검] {q} startIdx={st}: {len(items)}개 첫={items[0].get('itemcode') if items else None}"
                      f" 끝={items[-1].get('itemcode') if items else None} 상태분포={mg}", flush=True)
                if st == 0 and items:
                    print(f"  [점검] 항목 예시: {json.dumps(items[0], ensure_ascii=False)}", flush=True)
            except Exception as e:
                print(f"  [점검] {q} startIdx={st} 실패: {e}", flush=True)


def refresh_after_market_only():
    """장 마감 전: 검색은 하지 않고, 저장된 최신 결과의 애프터마켓 표시만 다시 확인"""
    path = os.path.join(OUT_DIR, "latest.json")
    _probe()
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fp:
        result = json.load(fp)
    lists, nxt = after_market_info()
    for k in ("종목", "매도", "매도주의", "20일선재돌파", "역배열돌파"):
        for row in result.get(k) or []:
            row["애프터"] = after_market_tag(str(row["종목코드"]), lists, nxt)
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
        am_lists, am_nxt = {"확인": None}, None

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
