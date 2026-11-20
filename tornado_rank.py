# -*- coding: utf-8 -*-
"""
토네이도 순위 검색기 — 1·2번선 돌파 이후 3번선·4번선 돌파 단계를 1~3순위로 분류

 선행 조건 : 기존 토네이도 신호(tornado_screener.compute 의 signal = 정배열 + 1·2번선 띠 A/B급 돌파)가
             최근 RANK_LOOKBACK 영업일 안에 있었음
 3번선 = BBandsUP(120~129, 2) 10개를 합친 띠,  4번선 = BBandsUP(240~249, 2) 10개를 합친 띠
 '돌파' = 종가가 띠 맨 위를 어제 종가 ≤ 띠 맨 위 → 오늘 종가 > 띠 맨 위로 넘음 (1·2번선 A급과 같은 기준)

 제1순위 : 1·2번선 돌파 → 3번선 돌파 → (마지막으로) 4번선 돌파. 오늘이 4번선 돌파일이고 종가는 3번선 위.
 제2순위 : 1·2번선 돌파 후, 3번선이나 4번선에 처음 닿거나(고가가 띠 안) 근처(고가가 띠 아래 NEAR 이내)까지 도달.
           아직 그 선을 종가로 넘지는 못한 상태. (제1순위에 해당하면 제외)
 제3순위 : 최근 제1·2순위였던 종목이 단기조정(고점 후 PB_MIN 이상 하락, 고점~오늘 PB_MAX 영업일 이내)을 거친 뒤
           3·4번선 중 더 높은 띠의 맨 위를 다시 종가로 돌파. (제1순위에 해당하면 제외)

 기존 20일선 재돌파·역배열 돌파 검색기와는 별개이며 건드리지 않음.
 계산에는 tornado_screener.compute() 결과(df)가 필요하다. 기준일 이후 봉은 없는 상태로 넣을 것.
"""
import numpy as np
import pandas as pd

# ───────────── 조정 가능한 값 ─────────────
RANK_LOOKBACK = 60     # 1·2번선 돌파 신호가 최근 몇 영업일 안에 있어야 하는지 (60 = 약 3개월)
NEAR = 0.03            # 제2순위 '근처': 고가가 띠 하단의 97% 이상 (3% 이내 접근)
PB_MAX = 20            # 제3순위 단기조정 최대 기간(영업일, 20 = 약 1개월): 조정 전 고점 ~ 오늘
PB_MIN = 0.05          # 제3순위 단기조정 최소 깊이: 고점 종가 대비 5% 이상 하락했어야 조정으로 봄
RANK3_LOOKBACK = 60    # 제3순위: 1·2순위였던 날이 최근 몇 영업일 안에 있어야 하는지
# ─────────────────────────────────────────

G3 = list(range(120, 130))     # 토네이도 3번선
G4 = list(range(240, 250))     # 토네이도 4번선


def prepare_rank(df):
    """df(compute 결과) → 순위 계산용 배열 dict. 데이터가 249봉 미만이면 None"""
    if df is None or len(df) < G4[-1] + 2:
        return None
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    a = {"dates": df.index, "o": df["Open"].to_numpy(float), "h": df["High"].to_numpy(float),
         "l": df["Low"].to_numpy(float), "c": df["Close"].to_numpy(float),
         "sig": df["signal"].to_numpy(bool)}
    for key, ns in (("g3", G3), ("g4", G4)):
        arr = np.column_stack([(tp.rolling(n).mean() + 2 * tp.rolling(n).std(ddof=0)).to_numpy() for n in ns])
        a[key + "lo"], a[key + "hi"] = arr.min(axis=1), arr.max(axis=1)
    a["H"] = np.fmax(a["g3hi"], a["g4hi"])
    c = a["c"]
    cp = np.r_[np.nan, c[:-1]]
    with np.errstate(invalid="ignore"):
        a["x3"] = (c > a["g3hi"]) & (cp <= np.r_[np.nan, a["g3hi"][:-1]])      # 3번선 돌파일
        a["x4"] = (c > a["g4hi"]) & (cp <= np.r_[np.nan, a["g4hi"][:-1]])      # 4번선 돌파일
        a["xH"] = (c > a["H"]) & (cp <= np.r_[np.nan, a["H"][:-1]])            # 3·4번선 중 높은 띠 돌파일
        # 접근(터치·근처): 고가가 띠 하단의 (1-NEAR) 이상, 종가는 띠 맨 위 이하
        a["z3"] = (a["h"] >= a["g3lo"] * (1 - NEAR)) & (c <= a["g3hi"])
        a["z4"] = (a["h"] >= a["g4lo"] * (1 - NEAR)) & (c <= a["g4hi"])
    return a


def _first_signal(a, t):
    """t 기준 최근 RANK_LOOKBACK 영업일 안의 가장 이른 1·2번선 돌파 신호일 (없으면 None)"""
    lo = max(0, t - RANK_LOOKBACK)
    idx = np.flatnonzero(a["sig"][lo:t + 1])
    return lo + int(idx[0]) if len(idx) else None


def rank12_at(a, t):
    """t번째 날이 제1순위면 (1, info), 제2순위면 (2, info), 아니면 (0, None)"""
    if t < G4[-1] + 1 or np.isnan(a["g4hi"][t]) or np.isnan(a["g4hi"][t - 1]):
        return 0, None
    s = _first_signal(a, t)
    if s is None:
        return 0, None
    # 제1순위 : 오늘 4번선 돌파 + 신호 이후 3번선 돌파가 있었고 종가가 3번선 위
    #           (신호 이후 첫 4번선 돌파만: 같은 신호로 4번선을 다시 넘는 건 제3순위 대상)
    if a["x4"][t] and a["c"][t] > a["g3hi"][t] and not a["x4"][s:t].any():
        j3 = np.flatnonzero(a["x3"][s:t + 1])
        if len(j3):
            return 1, {"s": s, "t3": s + int(j3[0]), "t4": t}
    # 제2순위 : 신호 이후 처음으로 3번선/4번선에 닿음·근접 (그 전에는 두 선 모두 접근권 밖이고 종가 돌파도 없었음)
    near3, near4 = a["z3"][t], a["z4"][t]
    if (near3 or near4) and not (a["z3"][s:t].any() or a["z4"][s:t].any() or a["x3"][s:t].any() or a["x4"][s:t].any()):
        return 2, {"s": s, "line": "3번선" if near3 and not near4 else "4번선" if near4 and not near3 else "3·4번선"}
    return 0, None


def rank3_at(a, t, r12):
    """t번째 날이 제3순위(1·2순위 → 단기조정 → 3·4번선 재돌파)면 info dict, 아니면 None.
       r12 : 날짜별 순위(1·2·0) 배열"""
    if not a["xH"][t]:
        return None
    lo = max(0, t - RANK3_LOOKBACK)
    es = np.flatnonzero(r12[lo:t] > 0)
    if not len(es):
        return None
    for e in lo + es[::-1]:                              # 가장 최근의 1·2순위일부터
        seg = a["c"][e:t]
        pk = e + int(np.argmax(seg))                     # e 이후 어제까지의 고점(종가)
        days = t - pk
        if days < 2 or days > PB_MAX:
            continue
        low = a["c"][pk + 1:t].min()
        dd = low / a["c"][pk] - 1
        if dd <= -PB_MIN:
            return {"e": int(e), "pk": int(pk), "days": int(days), "dd": float(dd) * 100,
                    "from": int(r12[e])}
    return None


def rank_all(a):
    """모든 날의 순위 배열 (0=해당없음, 1·2·3) — 과거 검증용. 계산량이 있으니 최근 구간만 쓰려면 start 사용"""
    n = len(a["c"])
    r12 = np.zeros(n, int)
    for t in range(G4[-1] + 1, n):
        r12[t], _ = rank12_at(a, t)
    r = r12.copy()
    for t in range(G4[-1] + 1, n):
        if r[t] != 1 and rank3_at(a, t, r12) is not None:
            r[t] = 3
    return r12, r


def rank_row(df, a, t, ndays=None):
    """오늘(t) 기준 순위 판정 → 앱·엑셀용 행(dict) 또는 None.
       t 이전 RANK3_LOOKBACK+RANK_LOOKBACK 영업일 구간만 계산해 빠르게 처리"""
    n = len(a["c"])
    if t < G4[-1] + 1:
        return None
    start = max(G4[-1] + 1, t - RANK3_LOOKBACK - 2)
    r12 = np.zeros(n, int)
    for u in range(start, t + 1):
        r12[u], _ = rank12_at(a, u)
    rk, info = int(r12[t]), None
    if rk:
        _, info = rank12_at(a, t)
    else:
        i3 = rank3_at(a, t, r12)
        if i3:
            rk, info = 3, i3
    if not rk:
        return None
    D, c = a["dates"], a["c"]
    f = lambda i: D[i].strftime("%Y-%m-%d")
    row = {"순위": rk,
           "종가": int(c[t]), "시가": int(a["o"][t]),
           "등락률(%)": round((c[t] / c[t - 1] - 1) * 100, 2),
           "3번선": f"{a['g3lo'][t]:,.0f}~{a['g3hi'][t]:,.0f}",
           "4번선": f"{a['g4lo'][t]:,.0f}~{a['g4hi'][t]:,.0f}",
           "3번선까지(%)": round((a["g3hi"][t] / c[t] - 1) * 100, 1),
           "4번선까지(%)": round((a["g4hi"][t] / c[t] - 1) * 100, 1)}
    if rk == 1:
        row.update({"1·2번 돌파일": f(info["s"]), "3번 돌파일": f(info["t3"]), "4번 돌파일": f(info["t4"]),
                    "단계": "1·2번 → 3번 → 4번 돌파"})
    elif rk == 2:
        row.update({"1·2번 돌파일": f(info["s"]), "단계": f"{info['line']} 터치·근접"})
    else:
        e = info["e"]
        s = _first_signal(a, e)
        row.update({"1·2번 돌파일": f(s) if s is not None else "", "이전 순위일": f(e), "이전 순위": f"제{info['from']}순위",
                    "조정 고점일": f(info["pk"]), "조정 기간(영업일)": info["days"], "조정 폭(%)": round(info["dd"], 1),
                    "단계": "조정 후 3·4번선 재돌파"})
    return row
