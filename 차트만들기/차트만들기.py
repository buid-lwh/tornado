# -*- coding: utf-8 -*-
"""
종목명(또는 6자리 코드)을 넣으면 브로셔 '2. 최근 125거래일 차트'와 같은 모양의 차트를 만들어 주는 도구.

사용:
  python 차트만들기.py 아시아나IDT
  python 차트만들기.py 267850 --기준일 2026-11-19      (그 날짜까지의 봉으로 그림)
  python 차트만들기.py 파수 --열기                      (이름 일부만 써도 됨, 결과 파일을 바로 열기)

결과: 이 폴더의 차트_출력 폴더에 <종목명>_차트.png (이미지) + .html (원본)
기준 규칙: 오늘 봉이 장중(16시 전)이면 직전 확정 봉을 기준으로 그리고, 오늘 시세는 점선·빈 막대로 따로 표시.
차트 구성은 공통 모듈 brochure_chart.py 를 그대로 사용하므로 브로셔 2번 차트와 동일함.
"""
import argparse
import datetime as dt
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

def _find_src():
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (here, os.path.dirname(here), os.path.join(here, "12번선 돌파 캔들 검색기"),
              os.path.join(os.path.dirname(here), "12번선 돌파 캔들 검색기")):
        if os.path.exists(os.path.join(d, "tornado_screener.py")):
            return d
    raise SystemExit("tornado_screener.py 를 찾지 못했습니다. 이 폴더나 바로 위 폴더에 있어야 합니다.")
SRC = _find_src()
sys.path.insert(0, SRC)
sys.path.insert(0, HERE)
os.chdir(SRC)                      # tornado_screener 의 data_cache 경로 기준
sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd                # noqa: E402
import tornado_screener as ts      # noqa: E402
import brochure_chart as bc        # noqa: E402

OUT_DIR = os.path.join(HERE, "차트_출력")
LIST_CACHE = os.path.join(HERE, "종목목록_캐시.csv")
EDGE = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]


def load_universe():
    """코스피·코스닥 종목 목록 (7일 캐시). 우선주·스팩 등은 제외된 목록"""
    if os.path.exists(LIST_CACHE) and (dt.datetime.now().timestamp() - os.path.getmtime(LIST_CACHE)) < 7 * 86400:
        return pd.read_csv(LIST_CACHE, dtype={"Code": str})
    try:
        u = ts.get_universe()[["Code", "Name", "시장"]]
        u.to_csv(LIST_CACHE, index=False, encoding="utf-8-sig")
        return u
    except Exception as e:
        if os.path.exists(LIST_CACHE):
            print(f"(종목 목록 갱신 실패, 저장된 목록 사용: {e})")
            return pd.read_csv(LIST_CACHE, dtype={"Code": str})
        raise


def find_stock(q):
    q = q.strip()
    u = load_universe()
    if q.isdigit() and len(q) == 6:
        hit = u[u["Code"] == q]
        return q, (hit.iloc[0]["Name"] if len(hit) else q), (hit.iloc[0]["시장"] if len(hit) else "")
    ex = u[u["Name"].str.replace(" ", "") == q.replace(" ", "")]
    if len(ex) == 0:
        ex = u[u["Name"].str.contains(q, case=False, regex=False)]
    if len(ex) == 0:
        sys.exit(f"'{q}' 종목을 찾지 못했습니다. 이름을 다시 확인하거나 6자리 종목코드를 쓰세요.")
    if len(ex) > 1:
        print("여러 종목이 겹칩니다. 첫 번째로 진행합니다. 다른 종목이면 이름 전체나 코드를 쓰세요:")
        for _, r in ex.head(8).iterrows():
            print(f"   {r['Name']} ({r['Code']}, {r['시장']})")
    r = ex.iloc[0]
    return r["Code"], r["Name"], r["시장"]


def edge_path():
    for p in EDGE:
        if os.path.exists(p):
            return p
    sys.exit("Microsoft Edge 를 찾지 못했습니다.")


CSS = """
:root{--ink:#14304a;--mute:#5d6a76;--line:#d7dee5}
html,body{margin:0;background:#fff;color:var(--ink);font-family:"Noto Sans KR","Malgun Gothic",sans-serif;
 -webkit-print-color-adjust:exact;print-color-adjust:exact}
.wrap{width:1040px;padding:18px 22px 14px;box-sizing:border-box}
.head{display:flex;justify-content:space-between;align-items:baseline;border-bottom:2px solid var(--ink);padding-bottom:6px;margin-bottom:6px}
.head b{font-size:20px}.head span{font-size:12px;color:var(--mute)}
h2{font-size:14px;margin:6px 0 2px}h2 small{font-weight:400;color:var(--mute);font-size:11px}
.note{font-size:11px;color:var(--mute);margin-top:2px}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:11px;color:var(--mute);margin-top:4px}
.sw{display:inline-block;width:18px;height:3px;vertical-align:middle;margin-right:4px}
.foot{font-size:10px;color:var(--mute);margin-top:6px}
"""


def main():
    ap = argparse.ArgumentParser(description="종목명 → 브로셔 2번 차트")
    ap.add_argument("종목", help="종목명(일부 가능) 또는 6자리 코드")
    ap.add_argument("--기준일", help="YYYY-MM-DD, 이 날짜까지의 봉으로 그림")
    ap.add_argument("--급등", type=float, default=15.0, help="급등일 라벨 기준(일 상승률 %%, 기본 15)")
    ap.add_argument("--열기", action="store_true", help="만든 이미지를 바로 열기")
    a = ap.parse_args()

    code, name, mkt = find_stock(a.종목)
    print(f"{name} ({code}) 데이터 받는 중…", flush=True)
    raw = ts.fetch(code, 6)
    if raw is None or len(raw) < 300:
        sys.exit("일봉 데이터가 부족합니다(상장 1년 미만이거나 받기 실패).")

    live = None
    if a.기준일:
        cut = pd.Timestamp(a.기준일)
        df = raw[raw.index <= cut]
        if len(df) == 0:
            sys.exit("기준일 이전 데이터가 없습니다.")
        cut = df.index[-1]
    else:
        now = dt.datetime.now()
        today = pd.Timestamp(now.date())
        if raw.index[-1] == today and now.hour < 16 and len(raw) > 1:
            live = raw.iloc[-1]
            df = raw.iloc[:-1]
        else:
            df = raw
        cut = df.index[-1]
    if len(df) < 260:
        sys.exit("기준일까지의 데이터가 260봉 미만이라 4번선(240일 이상)을 그릴 수 없습니다.")

    d = ts.compute(df)
    c = d["Close"]
    md = lambda t: f"{t.month}/{t.day}"

    # 라벨: 최근 125일 중 급등일(최대 2개) + 기준일 종가 (+ 장중)
    labels = []
    chg = c.pct_change() * 100
    surge = chg.iloc[-125:]
    surge = surge[surge >= a.급등]
    if len(surge):
        last_s = surge.index[-1]
        prev_s = surge.index[:-1]
        same = len(prev_s) and (c.index.get_loc(last_s) - c.index.get_loc(prev_s[-1]) <= 2)
        if same:
            p = prev_s[-1]
            labels.append(dict(date=str(last_s.date()), text=f"{md(p)} {surge[p]:+.0f}%·{md(last_s)} {surge[last_s]:+.0f}% 급등",
                               ref="high", dx=-6, dy=-4, color="#14304a"))
        else:
            labels.append(dict(date=str(last_s.date()), text=f"{md(last_s)} {surge[last_s]:+.0f}% 급등",
                               ref="high", dx=-6, dy=-4, color="#14304a"))
    labels.append(dict(date="last", text=f"{md(cut)} 종가 {int(c.iloc[-1]):,}", ref="close", dx=-6, dy=16, color="#14304a"))
    live_d, lt = None, None
    if live is not None:
        live_d = dict(open=float(live.Open), high=float(live.High), low=float(live.Low),
                      close=float(live.Close), volume=float(live.Volume))
        labels.append(dict(date="live", text=f"{md(live.name)} 장중 {int(live.Close):,}", ref="high", dx=-6, dy=-4, color="#a8402a"))
        lt = f"{md(live.name)} 장중 시세"

    svg, info = bc.chart_svg(d, code, name, cut, labels=labels, live=live_d)

    r = d.iloc[-1]
    ret60 = (c.iloc[-1] / c.iloc[-61] - 1) * 100
    basis = f"기준 {cut:%Y-%m-%d} 종가 {int(c.iloc[-1]):,}원 · 60일 {ret60:+.1f}% · 정배열 {'O' if bool(r['aligned']) else 'X'}"
    html = (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>{CSS}</style></head><body><div class="wrap">'
            f'<div class="head"><b>{name} <small style="font-size:12px;color:#5d6a76">{code} · {mkt}</small></b><span>{basis}</span></div>'
            f'<h2>최근 125거래일 차트 <small>{bc.chart_note(live_text=lt)}</small></h2>'
            f'{svg}{bc.legend_html(live_text=lt)}'
            f'<div class="foot">조건 분류용 참고 자료이며 투자 권유가 아닙니다. 시세는 FinanceDataReader(네이버) 기준.</div>'
            f'</div></body></html>')

    os.makedirs(OUT_DIR, exist_ok=True)
    safe = "".join(ch for ch in name if ch not in '\\/:*?"<>|')
    hp = os.path.join(OUT_DIR, f"{safe}_차트.html")
    pp = os.path.join(OUT_DIR, f"{safe}_차트.png")
    with open(hp, "w", encoding="utf-8") as f:
        f.write(html)

    subprocess.run([edge_path(), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
                    "--window-size=1040,720", f"--screenshot={pp}", "file:///" + hp.replace("\\", "/")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    if not os.path.exists(pp):
        sys.exit(f"이미지 변환 실패. HTML 은 만들어졌습니다: {hp}")
    print(f"완료: {pp}\n      {hp}\n{basis}")
    if live is not None:
        print(f"(오늘 {md(live.name)} 장중 시세는 점선으로 별도 표시)")
    if a.열기:
        os.startfile(pp)


if __name__ == "__main__":
    main()
