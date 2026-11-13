# -*- coding: utf-8 -*-
"""
황금토네이도 돌파 + 이평선 정배열 종목 검색기 (코스피·코스닥 전 종목)

[매수 타점 조건]  ※ 당일 15:30 장 마감 이후 실행
 1) 정배열 : 종가 > 5일선 > 10일선 > 20일선 > 60일선  (150일선은 위치 무관, 지수이동평균)
 2) 겹침   : 토네이도 1번선(BBandsUP 50~59, 2) 묶음과
             토네이도 2번선(EnvelopeUP 12~21, 10) 묶음이 하나로 모여 있음 (간격 GAP_TOL 이하)
 3) 통과   : 1·2번선 20개를 합친 띠를 캔들이 아래에서 위로 뚫음
             A급 완전통과 - 양봉, 저가 ≤ 띠 맨 위 < 종가, 어제 종가 ≤ 띠 맨 위
             B급 중간이상 - 양봉, 시가 < 띠 중간 ≤ 종가 ≤ 띠 맨 위
 → 결과에 나온 종목 = "내일 매수" 후보

[매도 조건]
 오늘 5일선이 10일선 아래로 내려감 (5·10일선 데드크로스, 종가 기준)
 + 최근 SELL_LOOKBACK 영업일(약 3개월) 안에 매수 신호가 났던 종목
 + 그 매수 신호 뒤 처음 나온 데드크로스 (이미 앞서 데드크로스가 났으면 이미 판 것으로 보고 제외)
 → 결과에 나온 종목 = "매도" 대상 (오늘 종가 또는 내일 시가에 매도)

 BBandsUP(N, 2) = (고+저+종)/3 의 N일 단순평균 + 2 × 표준편차  (영웅문과 소수점까지 일치 확인)

[두 가지 모드]
 ● 전 종목 검색 : 실행.bat
     → '토네이도_매수후보_YYYYMMDD.xlsx'
 ● 과거 신호 점검 : 점검.bat  (python tornado_screener.py 089590 051630)
     → 지난 CHECK_DAYS 영업일 동안 신호가 뜬 날짜를 모두 표시
     → '토네이도_과거신호_YYYYMMDD.xlsx'
"""

import os
import sys
import numpy as np
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

try:
    import FinanceDataReader as fdr
except ImportError:
    sys.exit("먼저 설치하세요:  pip install finance-datareader pandas openpyxl")

# ───────────── 조정 가능한 값 ─────────────
GAP_TOL = 0.02      # 겹침 판정: 1번선 묶음과 2번선 묶음이 서로 겹치거나, 떨어진 간격이 2% 이내
                    #   더 엄격하게 → 0.01, 느슨하게 → 0.03
SIGNAL_DAYS = 1     # 전 종목 검색: 1 = 오늘 신호만, 2 = 어제·오늘 신호까지
WORKERS = 8         # 동시 다운로드 수 (오류 많으면 4로 낮추기)

SELL_LOOKBACK = 60  # 매도 대상: 최근 며칠(영업일) 안에 매수 신호가 났던 종목까지 볼지 (20 = 1개월, 60 = 3개월)

VOL_MIN = 0         # 거래량 조건: 0 = 안 씀, 1.5 = 20일 평균의 1.5배 이상만 (통계 결과 보고 정하기)

CHECK_CODES = None  # 점검.bat 에서 종목코드를 넘겨받음 (파일은 고치지 않아도 됨)
CHECK_DAYS = 500    # 과거 신호 점검 기간 (영업일, 약 2년)
# ─────────────────────────────────────────

BB_P = list(range(50, 60))   # 토네이도 1번선-1·2 : BBandsUP(50~59, 2)   (영웅문 수식 확인)
ENV_P = list(range(12, 22))  # 토네이도 2번선-1·2 : EnvelopeUP(12~21, 10) (영웅문 수식 확인)
ENV_PCT = 10
T1, T2 = BB_P[:5], BB_P[5:]
MAS = [5, 10, 20, 60, 150, 220]   # 계산·표시용
ALIGN = [5, 10, 20, 60]      # 정배열 필수: 종가 > 5 > 10 > 20 > 60 (150일선은 조건에서 제외)
MA_TYPE = "지수"            # 영웅문 '가격 이동평균' 이평종류 = 지수
CHART_BARS = 600            # 영웅문 차트에 불러온 봉 개수 (지수이평 계산 기준, 확인됨)


def get_universe():
    """코스피·코스닥 보통주 목록 (스팩·우선주·리츠 등 제외)"""
    frames = []
    for mkt in ["KOSPI", "KOSDAQ"]:
        df = fdr.StockListing(mkt)
        code_col = "Code" if "Code" in df.columns else "Symbol"
        df = df.rename(columns={code_col: "Code"})
        df["시장"] = "거래소" if mkt == "KOSPI" else "코스닥"
        keep = ["Code", "Name", "시장"] + [k for k in ["Open", "High", "Low", "Close", "Volume"]
                                          if k in df.columns]
        frames.append(df[keep])
    u = pd.concat(frames, ignore_index=True)
    u = u[u["Code"].str.fullmatch(r"\d{5}0")]                # 우선주 제외
    u = u[~u["Name"].str.contains("스팩|리츠|ETN|우$|우B$", regex=True)]
    return u.drop_duplicates("Code").reset_index(drop=True)


def compute(df):
    """일봉 DataFrame → 날짜별 지표·신호가 붙은 DataFrame (데이터 부족 시 None)"""
    df = df[df["Close"] > 0].copy()
    if len(df) < max(MAS) + 2:
        return None  # 상장 기간이 짧아 150일선 계산 불가

    c, o = df["Close"], df["Open"]
    for n in MAS:
        if MA_TYPE == "지수":   # 영웅문처럼 최근 CHART_BARS봉으로 지수평균 계산
            df[f"ma{n}"] = c.iloc[-CHART_BARS:].ewm(span=n, adjust=False).mean().reindex(df.index)
        else:
            df[f"ma{n}"] = c.rolling(n).mean()
    tp = (df["High"] + df["Low"] + c) / 3   # 영웅문 BBands 기준가격 (확인됨)
    for n in BB_P:
        df[f"bb{n}"] = tp.rolling(n).mean() + 2 * tp.rolling(n).std(ddof=0)
    for n in ENV_P:
        df[f"env{n}"] = c.rolling(n).mean() * (1 + ENV_PCT / 100)

    g1 = df[[f"bb{n}" for n in BB_P]]
    g2 = df[[f"env{n}" for n in ENV_P]]
    df["g1_lo"], df["g1_hi"] = g1.min(axis=1), g1.max(axis=1)
    df["g2_lo"], df["g2_hi"] = g2.min(axis=1), g2.max(axis=1)
    df["top"] = df[["g1_hi", "g2_hi"]].max(axis=1)
    df["bot"] = df[["g1_lo", "g2_lo"]].min(axis=1)
    df["mid"] = (df["top"] + df["bot"]) / 2
    df["spread"] = df["top"] / df["bot"] - 1
    # 두 묶음 사이 간격 (겹치면 0)
    gap = (df[["g1_lo", "g2_lo"]].max(axis=1) - df[["g1_hi", "g2_hi"]].min(axis=1)).clip(lower=0)
    df["gap"] = gap / c

    # 조건 1 : 정배열
    seq = [c] + [df[f"ma{n}"] for n in ALIGN]
    aligned = pd.Series(True, index=df.index)
    for a, b in zip(seq, seq[1:]):
        aligned &= a > b
    df["aligned"] = aligned

    # 사전 조건 (설정 파일 '매수 방법 1) 사전 조건')
    #   flat  : 150·220일선이 최근 20일 동안 1% 넘게 떨어지지 않음 (하락 멈춤·횡보·우상향)
    #   cross : 최근 20일 안에 20일선이 60일선을 위로 교차 (2060 역배열 → 정배열 전환)
    df["flat"] = ((df["ma150"] / df["ma150"].shift(20) - 1 >= -0.01)
                  & (df["ma220"] / df["ma220"].shift(20) - 1 >= -0.01))
    up = df["ma20"] > df["ma60"]
    crossed = up & ~up.shift(1, fill_value=True)
    df["cross"] = crossed.rolling(20, min_periods=1).max().astype(bool)

    # 조건 2·3 : 겹친 띠 통과
    pc = c.shift(1)
    merged = df["gap"] <= GAP_TOL
    # 1·2번선 20개를 합친 띠를 캔들이 아래에서 위로 뚫음
    #   A 완전통과 : 양봉, 종가 > 띠 맨 위, 저가(꼬리 포함) ≤ 띠 맨 위, 어제 종가 ≤ 띠 맨 위
    #   B 중간이상 : 양봉, 띠 중간 ≤ 종가 ≤ 띠 맨 위, 시가 < 띠 중간
    bull = c > o
    a_cross = (merged & bull & (c > df["top"]) & (df["Low"] <= df["top"])
               & (pc <= df["top"].shift(1)))
    b_cross = (merged & bull & ~a_cross & (c >= df["mid"]) & (c <= df["top"])
               & (o < df["mid"]))
    df["grade"] = None
    df.loc[b_cross, "grade"] = "B 중간이상"
    df.loc[a_cross, "grade"] = "A 완전통과"
    df["signal"] = df["grade"].notna() & df["aligned"]

    # 신호 다음날 시가 매수 시 수익률 (과거 점검용)
    buy = o.shift(-1).where(o.shift(-1) > 0)   # 거래정지(시가 0)는 제외
    for n in [5, 20]:
        df[f"ret{n}"] = (c.shift(-n) / buy - 1) * 100
    df["vol_ratio"] = df["Volume"] / df["Volume"].shift(1).rolling(20).mean()
    if VOL_MIN:                                  # 거래량 조건 (오늘 거래량 ≥ 20일 평균 × VOL_MIN)
        df["signal"] &= df["vol_ratio"] >= VOL_MIN
    df["chg"] = (c / pc - 1) * 100

    # 매도 : 5일선이 10일선 아래로 내려온 날 (데드크로스)
    df["dead"] = (df["ma5"] < df["ma10"]) & (df["ma5"].shift(1) >= df["ma10"].shift(1))
    return df


def sell_row(df):
    """오늘이 매도일(매수 신호 뒤 첫 5·10 데드크로스)이면 매도 정보 dict, 아니면 None"""
    n = len(df)
    if n < 3 or not df["dead"].iloc[-1]:
        return None
    dead_prev = np.where(df["dead"].values[:n - 1])[0]
    after = dead_prev[-1] + 1 if len(dead_prev) else 0      # 직전 데드크로스 이후 신호만 (그 전 것은 이미 매도)
    sig = np.where(df["signal"].values[:n - 1])[0]
    sig = sig[sig >= max(after, n - 1 - SELL_LOOKBACK)]
    if not len(sig):
        return None
    i = sig[0]                                               # 이번 보유의 첫 매수 신호
    buy = df["Open"].iloc[i + 1]
    r = df.iloc[-1]
    return {
        "매수 신호일": df.index[i].date(),
        "신호 등급": df["grade"].iloc[i],
        "신호 횟수": int(len(sig)),
        "매수가(신호 다음날 시가)": int(buy) if buy > 0 else None,
        "오늘 종가": int(r["Close"]),
        "등락률(%)": round(r["chg"], 2),
        "수익률(%)": round((r["Close"] / buy - 1) * 100, 2) if buy > 0 else None,
        "보유일(영업일)": int(n - 1 - i),
        "5일선": int(r["ma5"]),
        "10일선": int(r["ma10"]),
    }


def _download(code, start):
    """네이버에서 먼저 받고, 실패하면 기본 경로로 다시 시도"""
    try:
        df = fdr.DataReader(f"NAVER:{code}", start)
        if df is not None and len(df):
            return df
    except Exception:
        pass
    return fdr.DataReader(code, start)


CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_cache")


def fetch(code, years):
    """주가 데이터: 처음엔 전체를 받아 저장, 다음부터는 최근 며칠치만 받아 이어붙임"""
    start = (dt.date.today() - dt.timedelta(days=int(365 * years))).isoformat()
    path = os.path.join(CACHE_DIR, f"{code}.pkl")
    old = None
    if os.path.exists(path):
        try:
            old = pd.read_pickle(path)
        except Exception:
            old = None
    if old is not None:
        old = old[old.index <= last_trade_day()]            # 잘못 붙은 미래 날짜 봉 제거
    if old is not None and len(old) > 10 and str(old.index[0].date()) <= start:
        recent_start = (old.index[-6]).strftime("%Y-%m-%d")
        new = _download(code, recent_start)
        if new is not None and len(new):
            common = old.index.intersection(new.index)
            # 겹치는 날 가격이 다르면 (액면분할·무상증자 등) 전체 다시 받기
            if len(common) and (abs(old.loc[common, "Close"] - new.loc[common, "Close"])
                                <= 1).all():
                df = pd.concat([old[old.index < new.index[0]], new])
                df = df[df.index >= start]
                df.to_pickle(path)
                return df
    df = _download(code, start)
    os.makedirs(CACHE_DIR, exist_ok=True)
    df.to_pickle(path)
    return df


def last_trade_day():
    """가장 최근 '장 마감된' 거래일: 평일 15:30 이전이면 전 거래일, 주말이면 금요일"""
    now = dt.datetime.now()
    d = pd.Timestamp(now.date())
    if now.weekday() < 5 and (now.hour, now.minute) < (15, 30):
        d -= pd.Timedelta(days=1)
    while d.weekday() >= 5:
        d -= pd.Timedelta(days=1)
    return d


def fast_fetch(r, trade_day):
    """저장된 데이터 + 거래소 일괄 시세(오늘 1봉)로 빠르게 갱신. 안 되면 일반 받기."""
    path = os.path.join(CACHE_DIR, f"{r.Code}.pkl")
    have_quote = all(hasattr(r, k) for k in ["Open", "High", "Low", "Close", "Volume"])
    if have_quote and os.path.exists(path):
        try:
            old = pd.read_pickle(path)
            old = old[old.index <= trade_day]               # 잘못 붙은 미래 날짜 봉 제거
            last = old.index[-1].normalize()
            if float(r.Close) > 0 and float(r.Open) > 0:
                bar = pd.DataFrame({"Open": [float(r.Open)], "High": [float(r.High)],
                                    "Low": [float(r.Low)], "Close": [float(r.Close)],
                                    "Volume": [float(r.Volume)]}, index=[trade_day])
                if last == trade_day:                       # 오늘 봉 이미 있음 → 덮어쓰기
                    df = pd.concat([old.iloc[:-1], bar])
                    df.to_pickle(path)
                    return df
                gap = len(pd.bdate_range(last, trade_day)) - 1
                same = (old["Close"].iloc[-1] == bar["Close"].iloc[0]
                        and old["Volume"].iloc[-1] == bar["Volume"].iloc[0])
                if gap == 1 and not same:                   # 바로 다음 거래일 → 한 봉 이어붙이기
                    df = pd.concat([old, bar])
                    df.to_pickle(path)
                    return df
                if same:                                    # 휴장일 등 → 그대로 사용
                    return old
        except Exception:
            pass
    return fetch(r.Code, 6)                                 # 저장 데이터가 없거나 며칠 비면 새로 받기


def row_of(df, i):
    r = df.iloc[i]
    return {
        "등급": r["grade"],
        "정배열": "O" if r["aligned"] else "X",
        "시가": int(r["Open"]),
        "종가": int(r["Close"]),
        "등락률(%)": round(r["chg"], 2),
        "띠 상단": int(r["top"]),
        "띠 하단": int(r["bot"]),
        "1번선(BB)": f"{r['g1_lo']:,.0f}~{r['g1_hi']:,.0f}",
        "2번선(Env)": f"{r['g2_lo']:,.0f}~{r['g2_hi']:,.0f}",
        "두 묶음 간격(%)": round(r["gap"] * 100, 2),
        "거래량/20일평균(배)": round(r["vol_ratio"], 1) if pd.notna(r["vol_ratio"]) else None,
    }


def diagnose(code):
    """영웅문 수치조회와 비교용: 오늘 기준 검색기 계산값"""
    df = compute(fetch(code, 6))
    if df is None:
        return
    r = df.iloc[-1]
    print(f"   [오늘 계산값] 종가 {r['Close']:,.0f}  지수이평 5/10/20/60/150 : "
          + " / ".join(f"{r[f'ma{n}']:,.1f}" for n in MAS))
    print("     1번선-1 : " + ", ".join(f"{r[f'bb{n}']:,.2f}" for n in BB_P[:5]))
    print("     1번선-2 : " + ", ".join(f"{r[f'bb{n}']:,.2f}" for n in BB_P[5:]))
    print("     2번선-1 : " + ", ".join(f"{r[f'env{n}']:,.2f}" for n in ENV_P[:5]))
    print("     2번선-2 : " + ", ".join(f"{r[f'env{n}']:,.2f}" for n in ENV_P[5:]))


def check_history():
    """종목별 과거 신호 점검"""
    rows = []
    try:
        u = get_universe()
        names = dict(zip(u["Code"], u["Name"]))
    except Exception:
        names = {}
    for code in CHECK_CODES:
        name = names.get(code, "")
        print(f"{name}({code}) 데이터 받는 중…", flush=True)
        diagnose(code)
        try:
            df = compute(fetch(code, 6))
        except Exception as e:
            print(f"{code}: 데이터 오류 ({e})")
            continue
        if df is None:
            print(f"{code}: 상장 기간이 짧아 150일선 계산 불가")
            continue
        recent = df.iloc[-CHECK_DAYS:]
        hits = [df.index.get_loc(d) for d in recent.index[recent["grade"].notna()]]
        print(f"\n■ {name}({code})  (최근 {len(recent)}영업일, 통과 {len(hits)}회)")
        if not hits:
            last = df.iloc[-1]
            print(f"   통과 없음 · 오늘 띠 폭 {last['spread']*100:.1f}%"
                  f" / 띠 {int(last['bot'])}~{int(last['top'])}원 / 종가 {int(last['Close'])}원")
        for i in hits:
            r = {"종목명": name, "종목코드": code, "날짜": df.index[i].date(), **row_of(df, i),
                 "5일후 수익(%)": round(df["ret5"].iloc[i], 1) if pd.notna(df["ret5"].iloc[i]) else None,
                 "20일후 수익(%)": round(df["ret20"].iloc[i], 1) if pd.notna(df["ret20"].iloc[i]) else None}
            rows.append(r)
            print("   " + "  ".join(f"{k}:{v}" for k, v in r.items() if k not in ("종목코드", "종목명")))
    if rows:
        fname = f"토네이도_과거신호_{dt.date.today():%Y%m%d}.xlsx"
        pd.DataFrame(rows).to_excel(fname, index=False)
        print(f"\n→ {fname} 저장")
        print("  ※ 정배열 X 인 날은 띠는 통과했지만 정배열이 아니라 검색에서 빠지는 날입니다.")
        print("  ※ 수익률 = 신호 다음날 시가에 샀을 때 5일·20일 뒤 종가 기준")


def scan_all():
    u = get_universe()
    trade_day = last_trade_day()
    cached = sum(os.path.exists(os.path.join(CACHE_DIR, f"{c}.pkl")) for c in u["Code"])
    if cached < len(u) * 0.9:
        print(f"검색 대상 {len(u)}종목 · 처음이라 전체 데이터를 받아 저장합니다 (20~40분, 한 번만)…")
    else:
        print(f"검색 대상 {len(u)}종목 · 저장된 데이터에 오늘 시세만 붙입니다 (1~2분)…")
    rows, sells, fails = [], [], 0
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(fast_fetch, r, trade_day): r for r in u.itertuples()}
        for i, f in enumerate(as_completed(futs), 1):
            r = futs[f]
            if i % 200 == 0:
                print(f"  {i}/{len(u)} 완료")
            try:
                df = compute(f.result())
            except Exception:
                fails += 1
                continue
            if df is None:
                continue
            for k in range(1, SIGNAL_DAYS + 1):
                if df["signal"].iloc[-k]:
                    rows.append({"시장": r.시장, "종목코드": r.Code, "종목명": r.Name,
                                 "신호일": df.index[-k].date(), **row_of(df, -k)})
                    break
            s = sell_row(df)
            if s:
                sells.append({"시장": r.시장, "종목코드": r.Code, "종목명": r.Name, **s})

    out = pd.DataFrame(rows)
    if len(out):
        out = out.drop(columns="정배열")
        out = out.sort_values(["등급", "두 묶음 간격(%)", "등락률(%)"], ascending=[True, True, False])
    sell = pd.DataFrame(sells)
    if len(sell):
        sell = sell.sort_values("매수 신호일")
    print(f"\n■ 매수 후보 (내일 매수) : {len(out)}종목")
    print(out.to_string(index=False) if len(out) else "   없음")
    print(f"\n■ 매도 대상 (5·10일선 데드크로스, 최근 {SELL_LOOKBACK}영업일 안 매수신호 종목) : {len(sell)}종목")
    print(sell.to_string(index=False) if len(sell) else "   없음")
    fname = f"토네이도_매수매도_{dt.date.today():%Y%m%d}.xlsx"
    with pd.ExcelWriter(fname) as w:
        (out if len(out) else pd.DataFrame({"결과": ["매수 후보 없음"]})).to_excel(w, sheet_name="매수후보", index=False)
        (sell if len(sell) else pd.DataFrame({"결과": ["매도 대상 없음"]})).to_excel(w, sheet_name="매도대상", index=False)
    print(f"\n→ {fname} 저장 (매수 {len(out)} · 매도 {len(sell)}종목, 데이터 실패 {fails}건)")


STOP_LOSS = 0.05    # 매도 규칙 시험: 손절 -5%
MAX_HOLD = 60       # 매도 규칙 시험: 최대 보유일


def sim_exit(C, O, L, M5, j, n):
    """j일 시가 매수 → (-STOP_LOSS 손절 / 종가 5일선 이탈 매도 / MAX_HOLD일) 수익률(%)과 보유일"""
    buy = O[j]
    if not buy > 0:
        return None
    stop = buy * (1 - STOP_LOSS)
    for k in range(j, min(j + MAX_HOLD, n)):
        if k > j and O[k] <= stop:                 # 갭하락 → 시가 매도
            return (O[k] / buy - 1) * 100, k - j + 1
        if L[k] <= stop:                           # 장중 손절
            return -STOP_LOSS * 100, k - j + 1
        if C[k] < M5[k]:                           # 종가가 5일선 아래 → 종가 매도
            return (C[k] / buy - 1) * 100, k - j + 1
    if j + MAX_HOLD <= n:                          # 최대 보유일 도달
        return (C[j + MAX_HOLD - 1] / buy - 1) * 100, MAX_HOLD
    return None                                    # 아직 진행 중인 거래는 제외


def b_to_a_stats(windows=(1, 3, 5, 10)):
    """저장된 전 종목 데이터로 'B 신호 → A 신호 전환' 통계 계산"""
    files = [f for f in os.listdir(CACHE_DIR) if f.endswith(".pkl")] if os.path.isdir(CACHE_DIR) else []
    if not files:
        print("저장된 데이터가 없습니다. 먼저 실행.bat 으로 전 종목 검색을 한 번 돌려주세요.")
        return
    try:
        u = get_universe()
        names = dict(zip(u["Code"], u["Name"]))
    except Exception:
        names = {}
    print(f"저장된 {len(files)}종목으로 최근 {CHECK_DAYS}영업일의 B→A 전환을 계산합니다…")
    maxw = max(windows)
    rows, sig_rows, base20, pb_rows = [], [], [], []
    ex_rows = []   # (구분, 수익률, 보유일)
    for k, f in enumerate(files, 1):
        code = f[:-4]
        try:
            df = compute(pd.read_pickle(os.path.join(CACHE_DIR, f)))
        except Exception:
            continue
        if df is None:
            continue
        n = len(df)
        win = df.iloc[max(0, n - CHECK_DAYS):]
        base20.append(win["ret20"].dropna().clip(-100, 1000).values)     # 비교 기준: 아무 날이나 샀을 때
        sg = win[win["grade"].notna() & win["aligned"]]
        for d, r in sg.iterrows():
            sig_rows.append((r["grade"][0], r["vol_ratio"], r["ret5"], r["ret20"],
                             bool(r["flat"]), bool(r["cross"])))
        is_a = (df["grade"] == "A 완전통과") & df["aligned"]
        # ── 풀백 매수 시험: A 신호 뒤 10일 안에 띠/5일선까지 눌렸다가 다시 오르는 날 ──
        C, O, L = df["Close"].values, df["Open"].values, df["Low"].values
        TOP, BOT, M5 = df["top"].values, df["bot"].values, df["ma5"].values
        # ── 매도 규칙 시험 ──
        FL, CR = df["flat"].values, df["cross"].values
        start = max(0, n - CHECK_DAYS)
        for i in range(start, n - 1, 10):                         # 비교 기준: 10일 간격 표본
            r = sim_exit(C, O, L, M5, i + 1, n)
            if r:
                ex_rows.append(("기준", r[0], r[1]))
        for i in np.where(is_a.values)[0]:
            if i < start or i + 1 >= n:
                continue
            r = sim_exit(C, O, L, M5, i + 1, n)
            if r:
                ex_rows.append(("A", r[0], r[1]))
                if FL[i] and CR[i]:
                    ex_rows.append(("A둘다", r[0], r[1]))
        R5, R20 = df["ret5"].values, df["ret20"].values
        for i in np.where(is_a.values)[0]:
            if i < n - CHECK_DAYS:
                continue
            touched, entry, broke = False, None, False
            for j in range(i + 1, min(i + 11, n)):
                if C[j] < BOT[j]:
                    broke = True
                    break
                if L[j] <= max(TOP[j], M5[j]) * 1.01:
                    touched = True
                if (touched and C[j] > O[j] and C[j] > C[j - 1]
                        and C[j] >= M5[j] and C[j] >= TOP[j]):
                    entry = j
                    break
            pb_rows.append({"A일": df.index[i].date(), "종목코드": code,
                            "풀백매수일": df.index[entry].date() if entry else None,
                            "띠 이탈": broke,
                            "A다음날매수 20일후": R20[i], "풀백매수 20일후": R20[entry] if entry else None,
                            "A다음날매수 5일후": R5[i], "풀백매수 5일후": R5[entry] if entry else None,
                            "장기이평 횡보": bool(df["flat"].iloc[i]), "2060 전환": bool(df["cross"].iloc[i])})
        for i in range(max(0, n - CHECK_DAYS), n):
            if not (df["grade"].iloc[i] == "B 중간이상" and df["aligned"].iloc[i]):
                continue
            ahead = n - 1 - i
            first_a = None
            for j in range(1, min(maxw, ahead) + 1):
                if is_a.iloc[i + j]:
                    first_a = j
                    break
            def rr(col, idx):
                v = df[col].iloc[idx] if idx is not None and idx < n else None
                return round(v, 1) if v is not None and pd.notna(v) and abs(v) < 1000 else None
            ai = i + first_a if first_a else None
            rows.append({"종목명": names.get(code, ""), "종목코드": code,
                         "B 신호일": df.index[i].date(), "종가": int(df["Close"].iloc[i]),
                         "A 전환까지(일)": first_a, "지켜본 기간(일)": min(maxw, ahead),
                         "B매수 5일후(%)": rr("ret5", i), "B매수 20일후(%)": rr("ret20", i),
                         "A매수 5일후(%)": rr("ret5", ai), "A매수 20일후(%)": rr("ret20", ai)})
        if k % 500 == 0:
            print(f"  {k}/{len(files)} 완료")
    if not rows:
        print("B 신호가 한 번도 없었습니다.")
        return
    t = pd.DataFrame(rows)
    print(f"\n■ B 신호(정배열 O) 총 {len(t)}회")
    summary = []
    for w in windows:
        base = t[t["지켜본 기간(일)"] >= w]          # w일 뒤까지 데이터가 있는 신호만
        hit = base["A 전환까지(일)"].notna() & (base["A 전환까지(일)"] <= w)
        rate = hit.mean() * 100 if len(base) else float("nan")
        summary.append({"기간": f"{w}일 이내", "대상 B 신호": len(base),
                        "A로 전환": int(hit.sum()), "전환 확률(%)": round(rate, 1)})
        print(f"   {w:>2}일 이내 A로 전환 : {int(hit.sum()):>5} / {len(base):>5}회  →  {rate:.1f}%")
    def line(label, r5, r20):
        r5, r20 = r5.dropna(), r20.dropna()
        if r20.empty:
            print(f"   {label}: 데이터 부족"); return
        print(f"   {label}: {len(r20)}회 · 20일후 중앙값 {r20.median():+.1f}% (평균 {r20.mean():+.1f}%)"
              f" · 20일후 수익 낸 비율 {(r20 > 0).mean()*100:.0f}% · 5일후 중앙값 {r5.median():+.1f}%")
    print("\n■ 언제 사는 게 나았나 (다음날 시가 매수 기준)")
    line("① B 뜬 다음날 바로 매수     ", t["B매수 5일후(%)"], t["B매수 20일후(%)"])
    conv = t["A 전환까지(일)"].notna() & (t["A 전환까지(일)"] <= 10)
    line("② B 후 A 전환 기다려 매수   ", t.loc[conv, "A매수 5일후(%)"], t.loc[conv, "A매수 20일후(%)"])
    # ── 거래량 조건별 성적 ──
    base = pd.Series(np.concatenate(base20)) if base20 else pd.Series(dtype=float)
    print("\n■ 비교 기준: 같은 기간 아무 종목·아무 날이나 샀을 때")
    print(f"   20일후 중앙값 {base.median():+.1f}% (평균 {base.mean():+.1f}%) · 수익 낸 비율 {(base > 0).mean()*100:.0f}%")
    sig = pd.DataFrame(sig_rows, columns=["등급", "거래량배수", "r5", "r20", "flat", "cross"])
    sig.loc[sig["r20"].abs() > 1000, "r20"] = None
    bins = [0, 1, 1.5, 2, 3, 5, float("inf")]
    labels = ["1배 미만", "1~1.5배", "1.5~2배", "2~3배", "3~5배", "5배 이상"]
    sig["거래량"] = pd.cut(sig["거래량배수"], bins=bins, labels=labels, right=False)
    vol_table = []
    print("\n■ 거래량 조건별 신호 성적 (신호 다음날 시가 매수 → 20일 뒤)")
    for g in ["A", "B"]:
        print(f"   [{g}급]   거래량(20일평균 대비)   횟수   20일후 중앙값   평균    수익 낸 비율")
        for lab in labels:
            x = sig[(sig["등급"] == g) & (sig["거래량"] == lab)]["r20"].dropna()
            if len(x) == 0:
                continue
            vol_table.append({"등급": g, "거래량": lab, "횟수": len(x), "20일후 중앙값(%)": round(x.median(), 1),
                              "20일후 평균(%)": round(x.mean(), 1), "수익 낸 비율(%)": round((x > 0).mean() * 100)})
            print(f"           {lab:<14} {len(x):>6}   {x.median():>+8.1f}%   {x.mean():>+6.1f}%   {(x > 0).mean()*100:>6.0f}%")
        for th in [1.5, 2, 3]:
            x = sig[(sig["등급"] == g) & (sig["거래량배수"] >= th)]["r20"].dropna()
            if len(x):
                print(f"           ▶ {th}배 이상 전체  {len(x):>6}   {x.median():>+8.1f}%   {x.mean():>+6.1f}%   {(x > 0).mean()*100:>6.0f}%")

    # ── 풀백 매수 결과 ──
    pb = pd.DataFrame(pb_rows)
    if len(pb):
        for col in ["A다음날매수 20일후", "풀백매수 20일후", "A다음날매수 5일후", "풀백매수 5일후"]:
            pb[col] = pd.to_numeric(pb[col], errors="coerce")
            pb.loc[pb[col].abs() > 1000, col] = None
        has = pb["풀백매수일"].notna()
        print(f"\n■ 풀백 매수 시험 (A 신호 {len(pb)}회 기준, 10일 안에 띠·5일선 지지 후 재상승)")
        print(f"   풀백 매수 신호 나옴 : {has.sum()}회 ({has.mean()*100:.0f}%)"
              f" · 띠 깨지고 실패 : {pb['띠 이탈'].sum()}회 ({pb['띠 이탈'].mean()*100:.0f}%)")
        def line2(label, x):
            x = x.dropna()
            if len(x):
                print(f"   {label}: {len(x):>5}회 · 20일후 중앙값 {x.median():+.1f}% (평균 {x.mean():+.1f}%)"
                      f" · 수익 낸 비율 {(x > 0).mean()*100:.0f}%")
        line2("비교 기준 (아무 날 매수)       ", base)
        line2("A 다음날 바로 매수 (전체)      ", pb["A다음날매수 20일후"])
        line2("A 다음날 매수 (풀백 나온 종목) ", pb.loc[has, "A다음날매수 20일후"])
        line2("풀백 매수                     ", pb.loc[has, "풀백매수 20일후"])

    # ── 사전 조건별 성적 ──
    print("\n■ 사전 조건을 더했을 때 (A급, 신호 다음날 시가 매수 → 20일 뒤)")
    print("   조건                                  횟수   20일후 중앙값   평균   수익 낸 비율")
    pre_table = []
    def line3(label, x):
        x = x.dropna()
        if len(x) == 0:
            print(f"   {label:<34} 신호 없음"); return
        pre_table.append({"조건": label.strip(), "횟수": len(x), "20일후 중앙값(%)": round(x.median(), 1),
                          "20일후 평균(%)": round(x.mean(), 1), "수익 낸 비율(%)": round((x > 0).mean() * 100)})
        print(f"   {label:<34} {len(x):>6}   {x.median():>+8.1f}%   {x.mean():>+6.1f}%   {(x > 0).mean()*100:>6.0f}%")
    A = sig[sig["등급"] == "A"]
    line3("비교 기준 (아무 날 매수)", base)
    line3("A 전체", A["r20"])
    line3("A + 장기이평 횡보", A.loc[A["flat"], "r20"])
    line3("A + 2060 전환(20일 내)", A.loc[A["cross"], "r20"])
    line3("A + 둘 다", A.loc[A["flat"] & A["cross"], "r20"])
    if len(pb):
        both = pb["장기이평 횡보"] & pb["2060 전환"]
        line3("A + 둘 다 → 풀백 매수", pb.loc[both & has, "풀백매수 20일후"])
    Bs = sig[sig["등급"] == "B"]
    line3("B + 둘 다", Bs.loc[Bs["flat"] & Bs["cross"], "r20"])

    # ── 매도 규칙 결과 ──
    ex = pd.DataFrame(ex_rows, columns=["구분", "수익률", "보유일"])
    ex = ex[ex["수익률"].abs() < 1000]
    print(f"\n■ 매도 규칙 시험 (다음날 시가 매수 → -{STOP_LOSS*100:.0f}% 손절 / 종가 5일선 이탈 매도 / 최대 {MAX_HOLD}일)")
    print("   구분                      거래수   승률   평균수익   중앙값   평균 이익   평균 손실   평균 보유일")
    ex_table = []
    for key, label in [("기준", "비교 기준 (아무 날 매수)"), ("A", "A 전체"), ("A둘다", "A + 사전조건 둘 다")]:
        x = ex[ex["구분"] == key]
        if x.empty:
            continue
        win = x[x["수익률"] > 0]["수익률"]; loss = x[x["수익률"] <= 0]["수익률"]
        ex_table.append({"구분": label, "거래수": len(x), "승률(%)": round(len(win) / len(x) * 100),
                         "평균수익(%)": round(x["수익률"].mean(), 2), "중앙값(%)": round(x["수익률"].median(), 2),
                         "평균 이익(%)": round(win.mean(), 2) if len(win) else None,
                         "평균 손실(%)": round(loss.mean(), 2) if len(loss) else None,
                         "평균 보유일": round(x["보유일"].mean(), 1)})
        print(f"   {label:<22} {len(x):>7}  {len(win)/len(x)*100:>4.0f}%  {x['수익률'].mean():>+7.2f}%  "
              f"{x['수익률'].median():>+6.2f}%  {win.mean() if len(win) else 0:>+8.2f}%  "
              f"{loss.mean() if len(loss) else 0:>+8.2f}%  {x['보유일'].mean():>8.1f}")
    print("   ※ 평균수익이 거래 1번당 기대 수익입니다. 비교 기준보다 높아야 신호가 쓸모 있습니다.")
    print("   ※ 수수료·세금(약 0.2~0.3%)은 빠져 있어 실제로는 그만큼 더 낮습니다.")

    fname = f"토네이도_B전환통계_{dt.date.today():%Y%m%d}.xlsx"
    with pd.ExcelWriter(fname) as w:
        pd.DataFrame(ex_table).to_excel(w, sheet_name="매도규칙 시험", index=False)
        if len(pb):
            pb.to_excel(w, sheet_name="풀백매수 시험", index=False)
        pd.DataFrame(summary).to_excel(w, sheet_name="요약", index=False)
        pd.DataFrame(vol_table).to_excel(w, sheet_name="거래량별 성적", index=False)
        pd.DataFrame(pre_table).to_excel(w, sheet_name="사전조건별 성적", index=False)
        t.to_excel(w, sheet_name="B신호 목록", index=False)
    print(f"\n→ {fname} 저장")
    print("  ※ ①은 B 신호 전체, ②는 10일 안에 A로 넘어간 경우만 (A 다음날 시가 매수)")
    print("  ※ 중앙값 = 가운데 값. 소수 급등주에 평균이 끌려가는 것을 막기 위해 함께 표시")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("stats", "통계"):
        b_to_a_stats()
        sys.exit()
    if len(sys.argv) > 1:              # 예: python tornado_screener.py 089590 051630
        CHECK_CODES = sys.argv[1:]
    if CHECK_CODES:
        check_history()
    else:
        scan_all()
