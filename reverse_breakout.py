# -*- coding: utf-8 -*-
"""
20·60일선 역배열 동시돌파 검색기 (코스피·코스닥 전 종목)   예) 한진칼 2026-11-02

[조건]  ※ 당일 15:30 장 마감 이후 실행
 1) 역배열 : 어제와 오늘 모두 20일선 < 60일선
 2) 동시돌파 : 오늘 양봉이 20일선과 60일선을 한 번에 위로 뚫음
      A 몸통관통 - 시가 < 20일선  그리고  종가 > 60일선   (캔들 몸통이 두 선을 모두 관통)
      B 갭돌파   - 어제 종가 < 20일선 ≤ 오늘 시가,  종가 > 60일선  (갭으로 20일선을 넘고 60일선까지 돌파)
 3) 어제 종가는 60일선 아래 (이미 60일선 위에 있던 종목 제외)

 이평선 종류·계산 방식은 tornado_screener.py 설정(영웅문과 같은 지수이평 600봉)을 그대로 씀
 → '12번선 돌파 캔들 검색기' 폴더의 tornado_screener.py 와 저장된 주가 데이터를 함께 씀
   (두 폴더가 모두 C:/LEEWH/제테크/주식검색 안에 나란히 있어야 함)

[사용법]
 ● 오늘 전 종목 검색 : python reverse_breakout.py
     → '역배열돌파_YYYYMMDD.xlsx'
 ● 지난 날짜로 검색 : python reverse_breakout.py 날짜 2026-11-02
     → '역배열돌파_20261102.xlsx'
 ● 종목별 과거 신호 : python reverse_breakout.py 180640 005930
     → 최근 CHECK_DAYS 영업일 동안 신호가 뜬 날짜와 5·20일 뒤 수익률
     → '역배열돌파_과거신호_YYYYMMDD.xlsx'
"""
import os
import sys
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
# tornado_screener.py 위치: 같은 폴더 → 옆의 '12번선 돌파 캔들 검색기' 폴더 순서로 찾음
TORNADO_DIR = os.path.join(os.path.dirname(HERE), "12번선 돌파 캔들 검색기")
for _p in (TORNADO_DIR, HERE):
    if os.path.exists(os.path.join(_p, "tornado_screener.py")):
        sys.path.insert(0, _p)
try:
    import tornado_screener as ts  # noqa: E402  (데이터 받기·저장·이평 설정 공유)
except ImportError:
    sys.exit(f"tornado_screener.py 를 찾지 못했습니다. 이 폴더나 '{TORNADO_DIR}' 에 있어야 합니다.")

# ───────────── 조정 가능한 값 ─────────────
INCLUDE_GAP = True   # B 갭돌파도 포함 (False = A 몸통관통만)
MA_GAP_MIN = 0.0     # 역배열 폭: 어제 20일선이 60일선보다 최소 몇 % 아래여야 하는지 (0 = 안 씀, 0.02 = 2%)
VOL_MIN = 0          # 거래량 조건: 0 = 안 씀, 1.5 = 20일 평균의 1.5배 이상만
SIGNAL_DAYS = 1      # 1 = 오늘 신호만, 2 = 어제·오늘 신호까지
CHECK_DAYS = 500     # 종목별 과거 신호 기간 (영업일, 약 2년)
# ─────────────────────────────────────────

MAS = [5, 20, 60, 120]


def compute(df):
    df = df[df["Close"] > 0].copy()
    if len(df) < 62:
        return None                       # 상장 기간이 짧아 60일선 계산 불가
    c, o = df["Close"], df["Open"]
    for n in MAS:
        if ts.MA_TYPE == "지수":
            df[f"ma{n}"] = c.iloc[-ts.CHART_BARS:].ewm(span=n, adjust=False).mean().reindex(df.index)
        else:
            df[f"ma{n}"] = c.rolling(n).mean()
    m20, m60 = df["ma20"], df["ma60"]
    pc, p20, p60 = c.shift(1), m20.shift(1), m60.shift(1)

    reverse = (p20 < p60 * (1 - MA_GAP_MIN)) & (m20 < m60)          # 1) 어제·오늘 역배열
    bull = c > o
    below = pc < p60                                                 # 3) 어제 종가 60일선 아래
    a = reverse & bull & below & (o < m20) & (c > m60)
    b = reverse & bull & below & ~a & (pc < p20) & (o >= m20) & (c > m60)

    df["grade"] = None
    if INCLUDE_GAP:
        df.loc[b, "grade"] = "B 갭돌파"
    df.loc[a, "grade"] = "A 몸통관통"
    df["vol_ratio"] = df["Volume"] / df["Volume"].shift(1).rolling(20).mean()
    df["signal"] = df["grade"].notna()
    if VOL_MIN:
        df["signal"] &= df["vol_ratio"] >= VOL_MIN
    df["chg"] = (c / pc - 1) * 100
    buy = o.shift(-1).where(o.shift(-1) > 0)                         # 다음날 시가 매수 가정
    for n in [5, 20]:
        df[f"ret{n}"] = (c.shift(-n) / buy - 1) * 100
    return df


def row_of(df, i):
    r = df.iloc[i]
    p = df.iloc[i - 1]
    return {
        "등급": r["grade"],
        "시가": int(r["Open"]), "종가": int(r["Close"]),
        "등락률(%)": round(r["chg"], 1),
        "20일선": int(r["ma20"]), "60일선": int(r["ma60"]),
        "어제 20·60 간격(%)": round((p["ma20"] / p["ma60"] - 1) * 100, 1),
        "종가-60선(%)": round((r["Close"] / r["ma60"] - 1) * 100, 1),
        "120일선 위": "O" if r["Close"] > r["ma120"] else "X",
        "거래량배수": round(r["vol_ratio"], 1) if pd.notna(r["vol_ratio"]) else None,
    }


def load_until(code, day):
    """지난 날짜 검색용: 저장된 데이터가 그 날짜까지 있으면 그대로, 없으면 새로 받기"""
    path = os.path.join(ts.CACHE_DIR, f"{code}.pkl")
    if os.path.exists(path):
        try:
            df = pd.read_pickle(path)
            if df.index[-1] >= day:
                return df
        except Exception:
            pass
    return ts.fetch(code, 6)


def scan(day=None):
    u = ts.get_universe()
    past = day is not None
    day = pd.Timestamp(day) if past else ts.last_trade_day()
    print(f"검색 대상 {len(u)}종목 · 기준일 {day.date()} · 20·60 역배열 동시돌파 검색 중…")
    rows, fails = [], 0
    with ThreadPoolExecutor(ts.WORKERS) as ex:
        if past:
            futs = {ex.submit(load_until, r.Code, day): r for r in u.itertuples()}
        else:
            futs = {ex.submit(ts.fast_fetch, r, day): r for r in u.itertuples()}
        for i, f in enumerate(as_completed(futs), 1):
            r = futs[f]
            if i % 300 == 0:
                print(f"  {i}/{len(u)} 완료")
            try:
                df = compute(f.result())
            except Exception:
                fails += 1
                continue
            if df is None or day not in df.index:
                continue
            end = df.index.get_loc(day)
            for k in range(SIGNAL_DAYS):
                j = end - k
                if j > 0 and df["signal"].iloc[j]:
                    rows.append({"시장": r.시장, "종목코드": r.Code, "종목명": r.Name,
                                 "신호일": df.index[j].date(), **row_of(df, j)})
                    break
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values(["등급", "등락률(%)"], ascending=[True, False])
    print(f"\n■ 20·60 역배열 동시돌파 : {len(out)}종목")
    print(out.to_string(index=False) if len(out) else "   없음")
    fname = f"역배열돌파_{day:%Y%m%d}.xlsx"
    (out if len(out) else pd.DataFrame({"결과": ["해당 종목 없음"]})).to_excel(fname, index=False)
    print(f"\n→ {fname} 저장 (데이터 실패 {fails}건)")


def check(codes):
    try:
        u = ts.get_universe()
        names = dict(zip(u["Code"], u["Name"]))
    except Exception:
        names = {}
    rows = []
    for code in codes:
        name = names.get(code, "")
        print(f"{name}({code}) 데이터 받는 중…", flush=True)
        try:
            df = compute(ts.fetch(code, 6))
        except Exception as e:
            print(f"   데이터 오류 ({e})")
            continue
        if df is None:
            print("   상장 기간이 짧아 60일선 계산 불가")
            continue
        recent = df.iloc[-CHECK_DAYS:]
        hits = [df.index.get_loc(d) for d in recent.index[recent["signal"]]]
        last = df.iloc[-1]
        print(f"\n■ {name}({code})  최근 {len(recent)}영업일 신호 {len(hits)}회"
              f"  · 오늘 종가 {int(last['Close']):,} / 20일선 {int(last['ma20']):,} / 60일선 {int(last['ma60']):,}")
        for i in hits:
            r = {"종목명": name, "종목코드": code, "날짜": df.index[i].date(), **row_of(df, i),
                 "5일후 수익(%)": round(df["ret5"].iloc[i], 1) if pd.notna(df["ret5"].iloc[i]) else None,
                 "20일후 수익(%)": round(df["ret20"].iloc[i], 1) if pd.notna(df["ret20"].iloc[i]) else None}
            rows.append(r)
            print("   " + "  ".join(f"{k}:{v}" for k, v in r.items() if k not in ("종목명", "종목코드")))
    if rows:
        fname = f"역배열돌파_과거신호_{dt.date.today():%Y%m%d}.xlsx"
        pd.DataFrame(rows).to_excel(fname, index=False)
        print(f"\n→ {fname} 저장")
        print("  ※ 수익률 = 신호 다음날 시가에 샀을 때 5일·20일 뒤 종가 기준")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] in ("날짜", "date"):
        scan(args[1])
    elif args:
        check(args)
    else:
        scan()
