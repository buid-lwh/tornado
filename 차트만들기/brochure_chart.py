# -*- coding: utf-8 -*-
"""
종목 브로셔 2번 항목(최근 125거래일 차트) 공통 모듈.

구성: 종가선 + 5·20·60·150일선
      + 토네이도 1·2번선(합친 범위, 연한 파란 띠: BBandsUP 50~59 · EnvelopeUP 12~21)
      + 토네이도 3번선(BBandsUP 120~129, 2σ, 진한 파란 띠) + 토네이도 4번선(BBandsUP 240~249, 2σ, 갈색 띠)
      + 번개선 A(실선)·B(점선, 주황) + A/B 신호 삼각형 + 최근 12일 캔들 + 거래량.
정의 출처: 영웅문 사용자 지표(차트 셋팅.pptx 의 지표 설정 화면)
  3번선-1 = BBandsUP(120..124,2), 3번선-2 = BBandsUP(125..129,2), 4번선-1 = BBandsUP(240..244,2), 4번선-2 = BBandsUP(245..249,2)
  (각 지표 수식1만 화면에서 확인, 나머지는 같은 간격으로 연속한다고 보고 계산. '토네이도 4번 검색기'의 BB(240,2)·BB(249,2)와 일치)
번개선: 번개선_분석.py 의 lightning() = 토네이도 차트수식 모음.hwp 값(매집간격 6, d1 0.5)과 동일.
가격 최고가의 1.6배 이내인 선·띠는 축에 포함하고, 더 먼 것은 차트 위쪽 화살표와 종가 대비 거리(%)로만 표시.

사용: import brochure_chart as bc
      svg, info = bc.chart_svg(d, code, name, cut, labels=[...], live=None)
      legend = bc.legend_html(live_text=None) ; note = bc.chart_note(live_text=None)
"""
import os
import sys
import numpy as np
import pandas as pd

def _find_src():
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (here, os.path.dirname(here), os.path.join(here, "12번선 돌파 캔들 검색기"),
              os.path.join(os.path.dirname(here), "12번선 돌파 캔들 검색기")):
        if os.path.exists(os.path.join(d, "tornado_screener.py")):
            return d
    raise SystemExit("tornado_screener.py 를 찾지 못했습니다. 이 폴더나 바로 위 폴더에 있어야 합니다.")
SRC = _find_src()
if SRC not in sys.path:
    sys.path.insert(0, SRC)
import tornado_screener as ts          # noqa: E402
import 번개선_분석 as lb                # noqa: E402

G3 = list(range(120, 130))     # 토네이도 3번선
G4 = list(range(240, 250))     # 토네이도 4번선
NEAR = 1.6


def _step(lo, hi):
    for s in [50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 25000, 50000, 100000, 200000]:
        if (hi - lo) / s <= 8:
            return s
    return 500000


def lightning_lines(code, cut):
    """번개선 A·B 시계열 (2018년부터의 전체 일봉으로 계산)"""
    full = ts._download(code, "2018-01-01")
    full = full[full.index <= cut]
    la, _ = lb.lightning(full, "A")
    lbb, _ = lb.lightning(full, "B")
    return pd.Series(la, index=full.index), pd.Series(lbb, index=full.index)


def bb_group(d, ns):
    """BBandsUP(n, 2) 묶음의 하단·상단. BBandsUP = (고+저+종)/3 의 n일 평균 + 2×표준편차(모집단)"""
    tp = (d["High"] + d["Low"] + d["Close"]) / 3
    arr = pd.concat([tp.rolling(n).mean() + 2 * tp.rolling(n).std(ddof=0) for n in ns], axis=1)
    return arr.min(axis=1), arr.max(axis=1)


def chart_svg(d, code, name, cut, labels=None, live=None, N=125, ncandle=12, line_labels=False):
    """d: ts.compute() 결과(cut 이하 봉). labels: [{date|'last'|'live', text, ref('high'|'close'|'low'), dx, dy, anchor, color}]
       live: {open, high, low, close, volume} 기준일 다음 봉(장중·마지막 시세)을 점선으로 이어 그림"""
    labels = labels or []
    boxes = []                     # 이미 놓인 라벨의 (x0, x1, y) — 겹침 회피용

    def tw(text, fs):
        return sum(fs * 0.95 if ord(c) > 127 else fs * 0.55 for c in text)

    def free_y(x1, y, w, step):
        for _ in range(4):
            if any(abs(y - by) < 10 and not (x1 < bx0 or x1 - w > bx1) for bx0, bx1, by in boxes):
                y += step
            else:
                break
        return y
    LA, LB = lightning_lines(code, cut)
    g3lo, g3hi = bb_group(d, G3)
    g4lo, g4hi = bb_group(d, G4)
    x = d.iloc[-N:].copy()
    x["la"] = LA.reindex(x.index).values
    x["lb"] = LB.reindex(x.index).values
    x["g3lo"], x["g3hi"] = g3lo.reindex(x.index).values, g3hi.reindex(x.index).values
    x["g4lo"], x["g4hi"] = g4lo.reindex(x.index).values, g4hi.reindex(x.index).values
    last = x.iloc[-1]
    M = N + (1 if live else 0)
    W, H, VH = 700, 288, 46
    L, R, T, B = 44, 8, 8, 18
    pw, ph = W - L - R, H - T - B
    hi_price = max(x["High"].max(), live["high"] if live else 0)
    lim = hi_price * NEAR
    ymin = min(x["Low"].min(), x["ma60"].min(), x["ma150"].min(), live["low"] if live else 1e18) * 0.95
    ymax = hi_price * 1.04
    # 가까운(최고가의 1.6배 이내) 선·띠는 축에 포함
    items = {"번개선 A": last["la"], "번개선 B": last["lb"], "3번선": last["g3hi"], "4번선": last["g4hi"]}
    for k, v in items.items():
        if pd.notna(v) and v <= lim:
            ymax = max(ymax, v * 1.04)
    la_in = pd.notna(last["la"]) and min(last["la"], last["lb"]) <= lim
    step = _step(ymin, ymax)

    def X(i): return L + pw * i / (M - 1)
    def Y(v): return T + ph * (1 - (v - ymin) / (ymax - ymin))
    def cy(v): return Y(min(max(v, ymin), ymax))
    def poly(col): return " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(x[col]))
    def cpoly(col): return " ".join(f"{X(i):.1f},{cy(v):.1f}" for i, v in enumerate(x[col]) if not np.isnan(v))
    def band(lo, hi):
        pts = [(X(i), cy(v)) for i, v in enumerate(x[hi]) if not np.isnan(v)] + \
              [(X(i), cy(v)) for i, v in reversed(list(enumerate(x[lo]))) if not np.isnan(v)]
        return " ".join(f"{a:.1f},{b:.1f}" for a, b in pts)

    svg = [f'<svg viewBox="0 0 {W} {H + VH}" width="100%" role="img" aria-label="{name} 최근 125거래일 차트">']
    t = int(np.ceil(ymin / step) * step)
    while t < ymax:
        svg.append(f'<line x1="{L}" x2="{W-R}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="#e1e6eb" stroke-width="0.6"/>')
        svg.append(f'<text x="{L-4}" y="{Y(t)+3:.1f}" font-size="8" text-anchor="end" fill="#5d6a76">{t:,}</text>')
        t += step
    prev = None
    for i, dt_ in enumerate(x.index):
        if prev is not None and dt_.month != prev:
            svg.append(f'<line x1="{X(i):.1f}" x2="{X(i):.1f}" y1="{T}" y2="{T+ph}" stroke="#eef1f4" stroke-width="0.6"/>')
            svg.append(f'<text x="{X(i):.1f}" y="{H-5}" font-size="8" text-anchor="middle" fill="#5d6a76">{dt_.month}월</text>')
        prev = dt_.month
    # 토네이도 4번선(갈색)·3번선(진한 파랑) 띠
    svg.append(f'<polygon points="{band("g4lo","g4hi")}" fill="#8b5a2b" fill-opacity="0.28"/>')
    svg.append(f'<polygon points="{band("g3lo","g3hi")}" fill="#0d47a1" fill-opacity="0.26"/>')
    for col, color in [("g4hi", "#7a4a1a"), ("g4lo", "#7a4a1a"), ("g3hi", "#0d47a1"), ("g3lo", "#0d47a1")]:
        svg.append(f'<polyline points="{cpoly(col)}" fill="none" stroke="{color}" stroke-width="0.8" stroke-opacity="0.85"/>')
    # 토네이도 1·2번선(합친 범위)
    b12 = [(X(i), cy(v)) for i, v in enumerate(x["top"])] + [(X(i), cy(v)) for i, v in reversed(list(enumerate(x["bot"])))]
    svg.append(f'<polygon points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in b12)}" fill="#b9d4ee" fill-opacity="0.60"/>')
    # 번개선 A(실선)·B(점선)
    if la_in:
        svg.append(f'<polyline points="{cpoly("lb")}" fill="none" stroke="#e67e22" stroke-width="1.3" stroke-dasharray="5 3"/>')
        svg.append(f'<polyline points="{cpoly("la")}" fill="none" stroke="#e67e22" stroke-width="1.7"/>')
    for col, color, wd in [("ma150", "#8e44ad", 1.5), ("ma60", "#8d96a0", 1.1), ("ma20", "#0b6b5c", 1.2), ("ma5", "#c58a1c", 1.2)]:
        svg.append(f'<polyline points="{poly(col)}" fill="none" stroke="{color}" stroke-width="{wd}"/>')
    for i, (dt_, row) in enumerate(x.iterrows()):
        if i >= N - ncandle:
            col = "#d9534f" if row["Close"] >= row["Open"] else "#4a78b5"
            svg.append(f'<line x1="{X(i):.1f}" x2="{X(i):.1f}" y1="{Y(row["High"]):.1f}" y2="{Y(row["Low"]):.1f}" stroke="{col}" stroke-width="1"/>')
            tp, bt = Y(max(row["Open"], row["Close"])), Y(min(row["Open"], row["Close"]))
            svg.append(f'<rect x="{X(i)-2.4:.1f}" y="{tp:.1f}" width="4.8" height="{max(bt-tp,1):.1f}" fill="{col}"/>')
    svg.append(f'<polyline points="{poly("Close")}" fill="none" stroke="#14304a" stroke-width="1.4" stroke-opacity="0.9"/>')
    for i, (dt_, row) in enumerate(x.iterrows()):
        if row["signal"]:
            col = "#0b6b5c" if str(row["grade"]).startswith("A") else "#c58a1c"
            xx, yy = X(i), Y(row["Low"]) + 6
            svg.append(f'<polygon points="{xx:.1f},{yy-5:.1f} {xx-3.5:.1f},{yy+1:.1f} {xx+3.5:.1f},{yy+1:.1f}" fill="{col}"/>')
    # 기준일 다음 봉(마지막 시세) : 점선 + 빈 원
    if live:
        lx, ly = X(N), Y(live["close"])
        svg.append(f'<line x1="{X(N-1):.1f}" y1="{Y(last["Close"]):.1f}" x2="{lx:.1f}" y2="{ly:.1f}" stroke="#a8402a" stroke-width="1.6" stroke-dasharray="3 2"/>')
        svg.append(f'<line x1="{lx:.1f}" x2="{lx:.1f}" y1="{Y(live["high"]):.1f}" y2="{Y(live["low"]):.1f}" stroke="#a8402a" stroke-width="1"/>')
        svg.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.2" fill="#fff" stroke="#a8402a" stroke-width="1.5"/>')
    # 150일선 라벨
    j = N - 62
    if line_labels:          # 선 이름·값 라벨은 기본적으로 그리지 않음 (2026-11-20 사용자 요청)
        t150 = f'150일선 {int(last["ma150"]):,}'
        svg.append(f'<text x="{X(j):.1f}" y="{Y(x["ma150"].iloc[j])-7:.1f}" font-size="8.5" font-weight="700" text-anchor="middle" fill="#8e44ad" paint-order="stroke" stroke="#fff" stroke-width="3">{t150}</text>')
        boxes.append((X(j) - tw(t150, 8.5) / 2, X(j) + tw(t150, 8.5) / 2, Y(x["ma150"].iloc[j]) - 7))
    # 사용자 라벨
    for lab in labels:
        dt_ = lab["date"]
        if dt_ == "last":
            i, row = N - 1, last
        elif dt_ == "live":
            i, row = N, pd.Series({"High": live["high"], "Close": live["close"], "Low": live["low"]})
        else:
            i = list(x.index).index(pd.Timestamp(dt_)); row = x.iloc[i]
        ref = {"high": row["High"], "close": row["Close"], "low": row["Low"]}[lab.get("ref", "high")]
        _lx, _ly = X(i) + lab.get("dx", 0), Y(ref) + lab.get("dy", 0)
        boxes.append((_lx - tw(lab["text"], 9), _lx, _ly))
        svg.append(f'<text x="{X(i)+lab.get("dx", 0):.1f}" y="{Y(ref)+lab.get("dy", 0):.1f}" font-size="9" font-weight="700" '
                   f'text-anchor="{lab.get("anchor", "end")}" fill="{lab.get("color", "#14304a")}" paint-order="stroke" stroke="#fff" stroke-width="3">{lab["text"]}</text>')
    # 선 값·종가 대비 거리 (축 안: 우측 라벨, 축 밖: 위쪽 화살표 줄)
    c_last = last["Close"]
    def dist(v): return (v / c_last - 1) * 100
    inrange, outrange, tor = [], [], []
    if pd.notna(last["la"]):
        hi_is_b = last["lb"] >= last["la"]
        hv, lv = (last["lb"], last["la"]) if hi_is_b else (last["la"], last["lb"])
        if la_in:
            inrange.append((hv, f'번개선 {"B" if hi_is_b else "A"} {int(hv):,} ({dist(hv):+.0f}%)', "#c2610c", -5))
            inrange.append((lv, f'번개선 {"A" if hi_is_b else "B"} {int(lv):,} ({dist(lv):+.0f}%)', "#c2610c", 12))
        else:
            outrange.append(f'번개선 A {int(last["la"]):,}({dist(last["la"]):+.0f}%)·B {int(last["lb"]):,}({dist(last["lb"]):+.0f}%)')
    for key, lo_k, hi_k, nm, color in [("3번선", "g3lo", "g3hi", "토네이도 3번선", "#0d3b99"), ("4번선", "g4lo", "g4hi", "토네이도 4번선", "#7a4a1a")]:
        lo, hi = last[lo_k], last[hi_k]
        if pd.isna(hi):
            continue
        if hi <= lim:
            tor.append((lo, hi, f'{nm} {int(lo):,}~{int(hi):,} ({dist(lo):+.0f}%~{dist(hi):+.0f}%)', color))
        elif lo <= lim:
            outrange.append(f'{nm} {int(lo):,}~{int(hi):,}({dist(lo):+.0f}%~, 상단 범위 밖)')
        else:
            outrange.append(f'{nm} {int(lo):,}~{int(hi):,}({dist(lo):+.0f}%~{dist(hi):+.0f}%)')
    if not line_labels:      # 번개선·3·4번선 이름/값 라벨 생략 (범위 밖 선은 위쪽 화살표만 표시)
        inrange, tor = [], []
    # 우측 라벨이 겹치지 않게 y 순으로 정렬해 최소 간격 확보
    inrange.sort(key=lambda z: Y(z[0]))
    prev_y = -99
    for v, txt, color, off in inrange:
        yy = Y(v) + off
        if yy - prev_y < 10:
            yy = prev_y + 10
        prev_y = yy
        svg.append(f'<text x="{W-R-2}" y="{yy:.1f}" font-size="8.5" font-weight="700" text-anchor="end" fill="{color}" paint-order="stroke" stroke="#fff" stroke-width="3">{txt}</text>')
    # 토네이도 3·4번선 라벨: 캔들 끝(마지막 종가 라벨) 왼쪽에 두고, 높은 띠는 상단 위, 낮은 띠는 하단 아래에 표시
    tor.sort(key=lambda z: -z[1])
    for k, (lo, hi, txt, color) in enumerate(tor):
        xe, wd_ = X(N - 16), tw(txt, 8.5)
        yy = free_y(xe, Y(hi) - 5, wd_, -11) if k == 0 else free_y(xe, Y(lo) + 11, wd_, 11)
        boxes.append((xe - wd_, xe, yy))
        svg.append(f'<text x="{X(N-16):.1f}" y="{yy:.1f}" font-size="8.5" font-weight="700" text-anchor="end" fill="{color}" paint-order="stroke" stroke="#fff" stroke-width="3">{txt}</text>')
    if outrange:
        ax = L + 8
        svg.append(f'<polygon points="{ax:.1f},{T+2} {ax-5:.1f},{T+11} {ax+5:.1f},{T+11}" fill="#e67e22"/>')
        for k, txt in enumerate(outrange if line_labels else []):
            svg.append(f'<text x="{ax+9:.1f}" y="{T+10+k*11}" font-size="8.5" font-weight="700" text-anchor="start" fill="#c2610c" paint-order="stroke" stroke="#fff" stroke-width="3">위쪽 범위 밖: {txt} (종가 대비)</text>')
    # 거래량
    vmax = max(x["Volume"].max(), live["volume"] if live else 0)
    base = H + VH - 2
    for i, (dt_, row) in enumerate(x.iterrows()):
        hgt = (VH - 8) * row["Volume"] / vmax
        col = "#d9534f" if row["Close"] >= row["Open"] else "#4a78b5"
        svg.append(f'<rect x="{X(i)-1.1:.1f}" y="{base-hgt:.1f}" width="2.2" height="{hgt:.1f}" fill="{col}" fill-opacity="0.75"/>')
    if live:
        hgt = (VH - 8) * live["volume"] / vmax
        svg.append(f'<rect x="{X(N)-1.3:.1f}" y="{base-hgt:.1f}" width="2.6" height="{hgt:.1f}" fill="#fff" stroke="#a8402a" stroke-width="0.9"/>')
    svg.append(f'<text x="{L-4}" y="{H+14}" font-size="8" text-anchor="end" fill="#5d6a76">거래량</text>')
    svg.append('</svg>')
    info = dict(la=float(last["la"]), lb=float(last["lb"]), g3=(float(last["g3lo"]), float(last["g3hi"])), g4=(float(last["g4lo"]), float(last["g4hi"])),
                near_lightning=bool(la_in), ma150=float(last["ma150"]))
    return "".join(svg), info


def chart_note(live_text=None):
    return "일봉, 지수이평(영웅문 기준) · 최근 12일은 캔들로 표시" + (f" · 점선과 빈 막대는 {live_text}" if live_text else "")


def legend_html(live_text=None):
    live = f'<span><i class="sw" style="background:#a8402a"></i>{live_text}</span>' if live_text else ""
    return ('<div class="legend">'
            '<span><i class="sw" style="background:#14304a"></i>종가</span>'
            '<span><i class="sw" style="background:#c58a1c"></i>5일선</span>'
            '<span><i class="sw" style="background:#0b6b5c"></i>20일선</span>'
            '<span><i class="sw" style="background:#8d96a0"></i>60일선</span>'
            '<span><i class="sw" style="background:#8e44ad;height:4px"></i><b>150일선</b></span>'
            '<span><i class="sw" style="background:#b9d4ee;height:7px"></i>토네이도 1·2번선(합친 띠)</span>'
            '<span><i class="sw" style="background:#0d47a1;height:7px;opacity:.6"></i><b>토네이도 3번선</b>(BBandsUP 120~129)</span>'
            '<span><i class="sw" style="background:#8b5a2b;height:7px;opacity:.65"></i><b>토네이도 4번선</b>(BBandsUP 240~249)</span>'
            '<span><i class="sw" style="background:#e67e22;height:4px"></i><b>번개선 A</b>(번개선-1)</span>'
            '<span><i class="sw" style="background:repeating-linear-gradient(90deg,#e67e22 0 5px,transparent 5px 8px);height:4px"></i><b>번개선 B</b>(번개선-1-2)</span>'
            '<span><b style="color:#0b6b5c">▲</b> A급 신호</span><span><b style="color:#c58a1c">▲</b> B급 신호</span>'
            + live + '</div>'
            '<div class="note">3번선 120~129일·4번선 240~249일 BBandsUP(2σ), 번개선 A는 가이드 값(6, 0.5). 최고가 1.6배 이내만 선으로, 먼 것은 화살표로 표시.</div>')
