# -*- coding: utf-8 -*-
"""
번개선(주황색선) 계산 (차트만들기 도구용 축소판 — 전체 분석 스크립트는 PC의 번개선_분석.py 에 있음)

 번개선 A (번개선-1)   : 기간 = 매집간격*80, 양봉이면 보정 = 1 - |몸통| / |최대 몸통|, 계수 d1
 번개선 B (번개선-1-2) : 기간 480, 음봉이면 보정 = 1 - 몸통/최대양봉몸통(>1), 계수 0.5
 공통 : 선 = EMA( (최고종가(기간/2)+최고종가(기간))/2 , 기간 ) + 보정 * 계수 * stdev((고+저+종)/3, 기간)
"""
import numpy as np
import pandas as pd

GAP_A, D1_A = 6, 0.5                 # 번개선 A (매집간격, d1): 토네이도 차트수식 모음.hwp 의 값과 동일 (영웅문 화면 값과의 직접 대조는 안 함)


def lightning(d, kind):
    c, o = d["Close"], d["Open"]
    tp = (d["High"] + d["Low"] + c) / 3
    if kind == "A":
        n, k = GAP_A * 80, D1_A
    else:
        n, k = 480, 0.5
    base = ((c.rolling(n // 2).max() + c.rolling(n).max()) / 2).ewm(span=n, adjust=False).mean()
    body = c - o
    mb = body.rolling(n).max().replace(0, np.nan)
    if kind == "A":
        fac = np.where(c > o, 1 - body.abs() / mb.abs(), 1.0)
    else:
        fac = np.where(c < o, 1 - body / mb, 1.0)
    fac = pd.Series(fac, index=d.index).fillna(1.0)
    sd = tp.rolling(n).std(ddof=0)
    return (base + fac * k * sd).values, sd.values
