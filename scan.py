# -*- coding: utf-8 -*-
"""
정배열 눌림 → 20일선 재돌파 → 토네이도 1·2번선 반 이상 통과 종목 검색기
(예시: 머큐리·광전자 2026-02-26 ~ 03-12 구간)

[패턴 순서]
 ① 정배열      : 종가 > 5일선 > 20일선 > 60일선 인 날이 있었음
 ② 급락        : 그 뒤 주가가 급락해서 5일선이 20일선 아래로 데드크로스
 ③ 5·60        : 5일선·60일선 데드크로스는 보통 안 남 (나더라도 며칠 안에 회복)
 ④ 저점        : 20일선과 60일선 사이에서 저점 형성 (꼬리가 60일선을 살짝 찍는 건 허용)
                 이 동안 20일선은 60일선 위 유지
 ⑤ 20일선 돌파 : 저점 후 단기간에 종가가 20일선을 다시 돌파
 ⑥ 신호        : 그 뒤 종가가 1·2번선 20개 중 절반(10개) 이상을 처음 넘어선 날
 → 신호일 = 오늘이면 "내일 매수 검토" 후보

 1번선 = BBandsUP(50~59, 2)   기준가격 (고+저+종)/3   (12번선 검색기와 같은 계산)
 2번선 = EnvelopeUP(12~21, 10)
 이동평균 = 지수이평 (영웅문 차트 600봉 기준)

[실행 방법]
 ● 실행.bat     : 전 종목에서 오늘(최근 장 마감일) 신호 검색
 ● 기간검색.bat : 전 종목에서 지정 기간에 신호가 뜬 종목 모두 찾기
                  (python scan.py 기간 2026-02-26 2026-03-12)
 ● 점검.bat     : 특정 종목의 과거 신호 날짜 모두 보기
                  (python scan.py 100590 017900)

주가 데이터는 '12번선 돌파 캔들 검색기/data_cache' 에 저장된 것을 같이 씁니다.
"""
import os
import sys
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

try:
    import FinanceDataReader as fdr
except ImportError:
    sys.exit("먼저 설치하세요:  pip install finance-datareader pandas openpyxl")

# ───────────── 조정 가능한 값 (필요하면 여기만 바꾸세요) ─────────────
P = dict(
    align_lookback=10,     # ① 데드크로스 전 며칠 안에 정배열이 있었는지
    drop_min_pct=10.0,     # ② 정배열 구간 고점 → 저점 최소 하락폭 (%)
    dc_within=30,          # ② 20일선 돌파 전 며칠 안에 5·20 데드크로스가 있었는지
    max_5_60_days=3,       # ③ 5일선이 60일선 아래에 있어도 되는 날 수 (0 = 5·60 데드크로스 절대 불가)
    low_tol_pct=10.0,      # ④ 저점(꼬리)이 60일선 아래로 내려가도 되는 한도 (%)
    max_close_below60=3,   # ④ 종가가 60일선 아래로 마감해도 되는 날 수
    low_to_break=10,       # ⑤ 저점 → 20일선 돌파까지 최대 며칠 ("단기간")
    break_within=10,       # ⑥ 20일선 돌파 → 신호일까지 최대 며칠
    half=10,               # ⑥ 1·2번선 20개 중 몇 개 이상 넘어야 하는지 (10 = 절반)
)
SIGNAL_DAYS = 1      # 전 종목 검색: 1 = 오늘 신호만, 2 = 어제·오늘 신호까지
WORKERS = 8          # 동시 다운로드 수 (오류 많으면 4로 낮추기)
CHECK_DAYS = 500     # 점검.bat: 최근 몇 영업일을 볼지 (약 2년)
MA_TYPE = "지수"     # "지수" 또는 "단순"
CHART_BARS = 600     # 영웅문 차트 봉 개수 (지수이평 계산 기준)
# ─────────────────────────────────────────────────────────────────────

BB_P = list(range(50, 60))    # 1번선
ENV_P = list(range(12, 22))   # 2번선
ENV_PCT = 10
MAS = [5, 20, 60]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_shared = os.path.join(os.path.dirname(BASE_DIR), "12번선 돌파 캔들 검색기", "data_cache")
CACHE_DIR = _shared if os.path.isdir(_shared) else os.path.join(BASE_DIR, "data_cache")


# ───────────────────────── 데이터 ─────────────────────────
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
    u = u[u["Code"].str.fullmatch(r"\d{5}0")]
    u = u[~u["Name"].str.contains("스팩|리츠|ETN|우$|우B$", regex=True)]
    return u.drop_duplicates("Code").reset_index(drop=True)


def last_trade_day():
    """가장 최근 '장 마감된' 거래일: 평일 15:30 이전이면 전 거래일, 주말이면 금요일"""
    now = dt.datetime.now()
    d = pd.Timestamp(now.date())
    if now.weekday() < 5 and (now.hour, now.minute) < (15, 30):
        d -= pd.Timedelta(days=1)
    while d.weekday() >= 5:
        d -= pd.Timedelta(days=1)
    return d


def _download(code, start):
    try:
        df = fdr.DataReader(f"NAVER:{code}", start)
        if df is not None and len(df):
            return df
    except Exception:
        pass
    return fdr.DataReader(code, start)


def fetch(code, years=6):
    """저장 데이터가 있으면 최근 며칠치만 받아 이어붙이고, 없으면 전체 받기"""
    start = (dt.date.today() - dt.timedelta(days=int(365 * years))).isoformat()
    path = os.path.join(CACHE_DIR, f"{code}.pkl")
    old = None
    if os.path.exists(path):
        try:
            old = pd.read_pickle(path)
            old = old[old.index <= last_trade_day()]
        except Exception:
            old = None
    if old is not None and len(old) > 10 and str(old.index[0].date()) <= start:
        new = _download(code, old.index[-6].strftime("%Y-%m-%d"))
        if new is not None and len(new):
            common = old.index.intersection(new.index)
            if len(common) and (abs(old.loc[common, "Close"] - new.loc[common, "Close"]) <= 1).all():
                df = pd.concat([old[old.index < new.index[0]], new])
                df = df[df.index >= start]
                df.to_pickle(path)
                return df
    df = _download(code, start)
    os.makedirs(CACHE_DIR, exist_ok=True)
    df.to_pickle(path)
    return df


def fast_fetch(r, trade_day):
    """저장 데이터 + 거래소 일괄 시세(오늘 1봉)로 빠르게 갱신. 안 되면 일반 받기."""
    path = os.path.join(CACHE_DIR, f"{r.Code}.pkl")
    have_quote = all(hasattr(r, k) for k in ["Open", "High", "Low", "Close", "Volume"])
    if have_quote and os.path.exists(path):
        try:
            old = pd.read_pickle(path)
            old = old[old.index <= trade_day]
            last = old.index[-1].normalize()
            if float(r.Close) > 0 and float(r.Open) > 0:
                bar = pd.DataFrame({"Open": [float(r.Open)], "High": [float(r.High)],
                                    "Low": [float(r.Low)], "Close": [float(r.Close)],
                                    "Volume": [float(r.Volume)]}, index=[trade_day])
                if last == trade_day:
                    df = pd.concat([old.iloc[:-1], bar])
                    df.to_pickle(path)
                    return df
                gap = len(pd.bdate_range(last, trade_day)) - 1
                same = (old["Close"].iloc[-1] == bar["Close"].iloc[0]
                        and old["Volume"].iloc[-1] == bar["Volume"].iloc[0])
                if gap == 1 and not same:
                    df = pd.concat([old, bar])
                    df.to_pickle(path)
                    return df
                if same:
                    return old
        except Exception:
            pass
    return fetch(r.Code)


# ───────────────────────── 계산 ─────────────────────────
def prepare(df):
    """일봉 → 계산용 배열 (데이터 부족 시 None)"""
    df = df[(df["Close"] > 0) & (df["Open"] > 0)].copy()
    if len(df) < 80:
        return None
    c = df["Close"]
    a = {"dates": df.index}
    for k in ["Open", "High", "Low", "Close", "Volume"]:
        a[k[0].lower() if k != "Volume" else "v"] = df[k].to_numpy(float)
    for n in MAS:
        if MA_TYPE == "지수":
            # 전체 기간으로 계산 (600봉 이후는 영웅문 600봉 계산과 사실상 같은 값, 과거 신호도 찾을 수 있음)
            a[f"ma{n}"] = c.ewm(span=n, adjust=False).mean().to_numpy()
        else:
            a[f"ma{n}"] = c.rolling(n).mean().to_numpy()
    tp = (df["High"] + df["Low"] + c) / 3
    bb = np.column_stack([(tp.rolling(n).mean() + 2 * tp.rolling(n).std(ddof=0)).to_numpy()
                          for n in BB_P])
    env = np.column_stack([(c.rolling(n).mean() * (1 + ENV_PCT / 100)).to_numpy() for n in ENV_P])
    lines = np.hstack([bb, env])
    a["bb_lo"], a["bb_hi"] = bb.min(axis=1), bb.max(axis=1)
    a["env_lo"], a["env_hi"] = env.min(axis=1), env.max(axis=1)
    # 종가가 넘어선 1·2번선 개수 (20개 중)
    with np.errstate(invalid="ignore"):
        a["cnt"] = np.where(np.isnan(lines).any(axis=1), -1,
                            (c.to_numpy()[:, None] > lines).sum(axis=1))
    return a


def detect_at(a, t, p=P):
    """t번째 날이 신호일이면 정보 dict, 아니면 None"""
    if t < 61:
        return None
    o, h, l, c = a["o"], a["h"], a["l"], a["c"]
    m5, m20, m60, cnt, D = a["ma5"], a["ma20"], a["ma60"], a["cnt"], a["dates"]

    # ⑥ 오늘 처음으로 1·2번선 절반 이상 통과
    if not (cnt[t] >= p["half"] and 0 <= cnt[t - 1] < p["half"]):
        return None
    if not (c[t] > m20[t]):
        return None

    # ⑤ 최근 20일선 돌파일 b (어제 종가 ≤ 20일선 < 오늘 종가)
    b = next((j for j in range(t, max(1, t - p["break_within"]) - 1, -1)
              if c[j] > m20[j] and c[j - 1] <= m20[j - 1]), None)
    if b is None:
        return None
    if (cnt[b:t] >= p["half"]).any():                          # 돌파 후 첫 통과만 (중복 신호 제외)
        return None

    # ② 돌파 전 5·20 데드크로스일 d
    d = next((j for j in range(b - 1, max(1, b - p["dc_within"]) - 1, -1)
              if m5[j] < m20[j] and m5[j - 1] >= m20[j - 1]), None)
    if d is None:
        return None

    # ① 데드크로스 전 정배열 (종가 > 5 > 20 > 60)
    al = [j for j in range(max(0, d - p["align_lookback"]), d)
          if c[j] > m5[j] > m20[j] > m60[j]]
    if not al:
        return None
    pa = al[-1]

    # ④ 저점: 데드크로스 ~ 돌파 사이 최저가
    L = d + int(np.argmin(l[d:b + 1]))
    if b - L > p["low_to_break"]:
        return None
    if l[L] < m60[L] * (1 - p["low_tol_pct"] / 100):          # 60일선 아래로 너무 깊음
        return None
    if l[L] > m20[L]:                                          # 20일선 위 저점은 눌림 아님
        return None
    if (c[d:b + 1] < m60[d:b + 1]).sum() > p["max_close_below60"]:
        return None
    if not (m20[d:t + 1] > m60[d:t + 1]).all():                # 20일선은 60일선 위 유지
        return None

    # ② 급락폭
    peak = h[pa:d + 1].max()
    drop = (l[L] / peak - 1) * 100
    if drop > -p["drop_min_pct"]:
        return None

    # ③ 5·60 데드크로스
    d560 = int((m5[d:t + 1] < m60[d:t + 1]).sum())
    if d560 > p["max_5_60_days"]:
        return None

    f = lambda i: D[i].strftime("%Y-%m-%d")
    top = max(a["bb_hi"][t], a["env_hi"][t])
    return {
        "신호일": f(t),
        "시가": int(o[t]), "종가": int(c[t]),
        "등락률(%)": round((c[t] / c[t - 1] - 1) * 100, 2),
        "통과 선(20개 중)": int(cnt[t]),
        "1번선(BB)": f"{a['bb_lo'][t]:,.0f}~{a['bb_hi'][t]:,.0f}",
        "2번선(Env)": f"{a['env_lo'][t]:,.0f}~{a['env_hi'][t]:,.0f}",
        "띠 맨 위까지(%)": round((top / c[t] - 1) * 100, 1),
        "정배열 마지막일": f(pa),
        "5·20 데드크로스일": f(d),
        "저점일": f(L), "저점": int(l[L]),
        "저점-60일선(%)": round((l[L] / m60[L] - 1) * 100, 1),
        "급락폭(%)": round(drop, 1),
        "20일선 돌파일": f(b),
        "5·60 데드크로스": f"있음({d560}일)" if d560 else "없음",
        "거래량/20일평균(배)": round(a["v"][t] / a["v"][t - 20:t].mean(), 1) if a["v"][t - 20:t].mean() > 0 else None,
    }


def add_returns(row, a, t):
    """신호 다음날 시가 매수 → 5·20일 뒤 종가 수익률 (과거 점검용)"""
    n = len(a["c"])
    if t + 1 < n:
        buy = a["o"][t + 1]
        row["다음날 시가"] = int(buy)
        for k in (5, 20):
            row[f"{k}일후 수익(%)"] = round((a["c"][t + k] / buy - 1) * 100, 1) if t + k < n else None
        end = min(n, t + 21)
        row["20일 최고(%)"] = round((a["h"][t + 1:end].max() / buy - 1) * 100, 1)
    return row


def save(df, fname, extra=None):
    """엑셀 저장. extra = {시트이름: DataFrame} 추가 시트"""
    path = os.path.join(BASE_DIR, fname)
    sheets = {"결과": df, **(extra or {})}
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as w:
            for name, d in sheets.items():
                if d is not None and len(d):
                    d.to_excel(w, index=False, sheet_name=name)
            for ws in w.sheets.values():
                for col in ws.columns:
                    ws.column_dimensions[col[0].column_letter].width = 14
    except PermissionError:
        print("\n  엑셀 파일이 열려 있어서 저장하지 못했습니다. 엑셀을 닫고 다시 실행하세요.")
        return
    print(f"\n  엑셀 저장: {path}")
    if sys.platform.startswith("win"):
        os.startfile(path)


SHOW = ["종목명", "신호일", "종가", "통과 선(20개 중)", "5·20 데드크로스일", "저점일",
        "저점-60일선(%)", "급락폭(%)", "20일선 돌파일", "5·60 데드크로스"]


# ───────────────────────── 모드 ─────────────────────────
def scan_all():
    u = get_universe()
    trade_day = last_trade_day()
    print(f"  검색 대상 {len(u)}종목 · 기준일 {trade_day:%Y-%m-%d} (데이터 갱신 중…)")
    rows, fails = [], 0
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(fast_fetch, r, trade_day): r for r in u.itertuples()}
        for i, fu in enumerate(as_completed(futs), 1):
            r = futs[fu]
            if i % 300 == 0:
                print(f"    {i}/{len(u)} 완료")
            try:
                a = prepare(fu.result())
            except Exception:
                fails += 1
                continue
            if a is None:
                continue
            n = len(a["c"])
            for k in range(1, SIGNAL_DAYS + 1):
                info = detect_at(a, n - k)
                if info:
                    rows.append({"시장": r.시장, "종목코드": r.Code, "종목명": r.Name, **info})
                    break
    if not rows:
        print(f"\n  ▶ 조건에 맞는 종목이 없습니다. (데이터 실패 {fails}건)")
        return
    out = pd.DataFrame(rows).sort_values(["신호일", "통과 선(20개 중)"], ascending=[False, False])
    print(f"\n  ▶ {len(out)}종목 (데이터 실패 {fails}건)\n")
    print(out[SHOW].to_string(index=False))
    save(out, f"20일선재돌파_매수후보_{dt.date.today():%Y%m%d}.xlsx")


def summarize(out):
    """과거 신호 성적 요약 · 종목별 · 연도별·월별 횟수"""
    def stat(label, x):
        x = pd.to_numeric(x, errors="coerce").dropna()
        x = x[x.abs() < 1000]
        if not len(x):
            return None
        return {"구분": label, "신호 수": len(x), "중앙값(%)": round(x.median(), 1),
                "평균(%)": round(x.mean(), 1), "수익 낸 비율(%)": round((x > 0).mean() * 100),
                "+10% 이상 비율(%)": round((x >= 10).mean() * 100)}
    perf = [stat("5일후 종가", out.get("5일후 수익(%)")),
            stat("20일후 종가", out.get("20일후 수익(%)")),
            stat("20일 안 최고가", out.get("20일 최고(%)"))]
    no560 = out["5·60 데드크로스"] == "없음"
    perf += [stat("20일후 · 5·60 데드크로스 없음", out.loc[no560, "20일후 수익(%)"]),
             stat("20일후 · 5·60 데드크로스 있음", out.loc[~no560, "20일후 수익(%)"]),
             stat("20일후 · 20개 모두 통과", out.loc[out["통과 선(20개 중)"] == 20, "20일후 수익(%)"]),
             stat("20일후 · 일부만 통과", out.loc[out["통과 선(20개 중)"] < 20, "20일후 수익(%)"])]
    perf = pd.DataFrame([p for p in perf if p])

    by_stock = (out.groupby(["종목코드", "종목명"])
                .agg(신호횟수=("신호일", "size"), 첫신호=("신호일", "min"), 마지막신호=("신호일", "max"),
                     **({"20일후 평균(%)": ("20일후 수익(%)", "mean")} if "20일후 수익(%)" in out else {}))
                .reset_index().sort_values("신호횟수", ascending=False))
    if "20일후 평균(%)" in by_stock:
        by_stock["20일후 평균(%)"] = by_stock["20일후 평균(%)"].round(1)
    by_month = (out.assign(연월=out["신호일"].str[:7]).groupby("연월")
                .agg(신호수=("신호일", "size"), 종목수=("종목코드", "nunique")).reset_index())
    return perf, by_stock, by_month


def scan_period(start, end):
    """저장된 전 종목 데이터에서 start~end 사이 신호 모두 찾기 (빈칸이면 저장된 기간 전체)"""
    files = sorted(f for f in os.listdir(CACHE_DIR) if f.endswith(".pkl")) if os.path.isdir(CACHE_DIR) else []
    if not files:
        print("  저장된 데이터가 없습니다. 먼저 실행.bat (또는 12번선 검색기)를 한 번 돌려주세요.")
        return
    try:
        u = get_universe()
        names = dict(zip(u["Code"], u["Name"]))
        mkts = dict(zip(u["Code"], u["시장"]))
    except Exception:
        names, mkts = {}, {}
    s = pd.Timestamp(start) if start else pd.Timestamp("1990-01-01")
    e = pd.Timestamp(end) if end else pd.Timestamp(dt.date.today())
    start, end = (start or "처음"), (end or "오늘")
    print(f"  저장된 {len(files)}종목에서 {start} ~ {end} 신호를 찾습니다 (몇 분 걸립니다)…")
    rows = []
    for k, fn in enumerate(files, 1):
        code = fn[:-4]
        try:
            a = prepare(pd.read_pickle(os.path.join(CACHE_DIR, fn)))
        except Exception:
            continue
        if a is None:
            continue
        idx = np.where((a["dates"] >= s) & (a["dates"] <= e))[0]
        for t in idx:
            info = detect_at(a, t)
            if info:
                rows.append(add_returns({"시장": mkts.get(code, ""), "종목코드": code,
                                         "종목명": names.get(code, ""), **info}, a, t))
        if k % 500 == 0:
            print(f"    {k}/{len(files)} 완료")
    if not rows:
        print(f"\n  ▶ {start} ~ {end} 동안 신호가 뜬 종목이 없습니다.")
        return
    out = pd.DataFrame(rows).sort_values(["신호일", "종목명"])
    perf, by_stock, by_month = summarize(out)
    print(f"\n  ▶ {out['종목코드'].nunique()}종목 · 신호 {len(out)}회\n")
    print(out[SHOW + [c for c in ["20일후 수익(%)"] if c in out]].tail(60).to_string(index=False))
    if len(out) > 60:
        print(f"  … 화면에는 최근 60건만 표시, 전체 {len(out)}건은 엑셀에 있습니다.")
    print("\n  [신호 다음날 시가 매수 시 성적]")
    print(perf.to_string(index=False))
    print("\n  [신호가 많이 나온 종목 TOP 15]")
    print(by_stock.head(15).to_string(index=False))
    save(out, f"20일선재돌파_과거패턴_{start}_{end}.xlsx".replace("-", ""),
         {"성적 요약": perf, "종목별 횟수": by_stock, "월별 횟수": by_month})


def check_history(codes):
    try:
        u = get_universe()
        names = dict(zip(u["Code"], u["Name"]))
    except Exception:
        names = {}
    rows = []
    for code in codes:
        name = names.get(code, "")
        try:
            a = prepare(fetch(code))
        except Exception as ex:
            print(f"  {code}: 데이터 오류 ({ex})")
            continue
        if a is None:
            print(f"  {code}: 데이터 부족")
            continue
        n = len(a["c"])
        hits = []
        for t in range(max(61, n - CHECK_DAYS), n):
            info = detect_at(a, t)
            if info:
                hits.append(add_returns({"종목명": name, "종목코드": code, **info}, a, t))
        print(f"\n  ■ {name}({code})  최근 {min(CHECK_DAYS, n)}영업일 신호 {len(hits)}회")
        for r in hits:
            print(f"     {r['신호일']}  종가 {r['종가']:,}  통과 {r['통과 선(20개 중)']}개  "
                  f"데드크로스 {r['5·20 데드크로스일']} → 저점 {r['저점일']} → 20일선 돌파 {r['20일선 돌파일']}"
                  f"  5·60:{r['5·60 데드크로스']}  20일후 {r.get('20일후 수익(%)')}%")
        rows += hits
    if rows:
        save(pd.DataFrame(rows), f"20일선재돌파_과거신호_{dt.date.today():%Y%m%d}.xlsx")


if __name__ == "__main__":
    print("=" * 64)
    print("  정배열 눌림 → 20일선 재돌파 → 1·2번선 반 이상 통과 검색기")
    print("=" * 64)
    args = sys.argv[1:]
    if args and args[0] in ("기간", "period", "과거"):
        scan_period(args[1] if len(args) > 1 and args[1] not in ("", "-") else None,
                    args[2] if len(args) > 2 and args[2] not in ("", "-") else None)
    elif args:
        check_history(args)
    else:
        scan_all()
