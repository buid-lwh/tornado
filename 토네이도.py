# -*- coding: utf-8 -*-
"""
토네이도 통합 검색기 — 세 가지 검색기를 한 번에 실행 (코스피·코스닥 전 종목)

 ① 토네이도 12번선 돌파   (tornado_screener.py) : 매수 후보 · 매도 대상 · 매도 주의
 ①-2 토네이도 순위        (tornado_rank.py)      : 1·2번선 돌파 후 3번선·4번선 단계 (제1·2·3순위, 기준은 그 파일 머리말)
 ② 20일선 재돌파          (scan.py)             : 정배열 눌림 → 20일선 재돌파 → 1·2번선 반 이상 통과
 ③ 20·60 역배열 동시돌파  (reverse_breakout.py) : 양봉 하나로 20·60일선을 한 번에 돌파

 주가 데이터는 종목마다 한 번만 받아서(저장 데이터 + 오늘 시세) 세 검색기에 같이 씁니다.
 검색 조건·계산은 각 검색기 파일 그대로입니다. 조건을 바꾸려면 각 파일의 설정값을 고치세요.

[실행]  ※ 당일 15:30 장 마감 이후
 토네이도.bat  (= python 토네이도.py)
 → '토네이도_통합_YYYYMMDD.xlsx' (시트 5개: 토네이도 매수·매도·매도주의, 20일선 재돌파, 역배열 돌파)

[파일 위치]  아래 둘 중 하나면 됩니다.
 · 세 검색기 파일(tornado_screener.py, scan.py, reverse_breakout.py)이 이 파일과 같은 폴더
 · PC처럼 C:/LEEWH/제테크/주식검색 안에 세 검색기 폴더가 나란히 있음
"""
import os
import sys
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
SEARCH_DIRS = [HERE,
               os.path.join(PARENT, "12번선 돌파 캔들 검색기"),
               os.path.join(PARENT, "20일선 재돌파 검색기"),
               os.path.join(PARENT, "2060 역배열 돌파 검색기")]


def _find(fname):
    for d in SEARCH_DIRS:
        if os.path.exists(os.path.join(d, fname)):
            if d not in sys.path:
                sys.path.insert(0, d)
            return
    sys.exit(f"{fname} 을(를) 찾지 못했습니다. 이 폴더나 옆 검색기 폴더에 있어야 합니다.")


for _f in ("tornado_screener.py", "scan.py", "reverse_breakout.py", "tornado_rank.py"):
    _find(_f)

import tornado_screener as ts   # noqa: E402  ① 토네이도 (데이터 받기·저장도 이 파일 것을 씀)
import scan as sc               # noqa: E402  ② 20일선 재돌파
import reverse_breakout as rb   # noqa: E402  ③ 역배열 돌파
import tornado_rank as tk       # noqa: E402  ①-2 토네이도 순위 (1·2번선 돌파 후 3·4번선 단계)

SHEETS = ["토네이도 매수", "토네이도 매도", "토네이도 매도주의", "토네이도 순위", "20일선 재돌파", "역배열 돌파"]


def check_one(r, raw, trade_day):
    """한 종목의 주가로 세 검색기 조건을 모두 확인 → {시트이름: 행} (해당 없으면 빈 dict)"""
    head = {"시장": r.시장, "종목코드": r.Code, "종목명": r.Name}
    out = {}
    # ① 토네이도
    try:
        df = ts.compute(raw)
        if df is not None:
            for k in range(1, ts.SIGNAL_DAYS + 1):
                if df["signal"].iloc[-k]:
                    row = {**head, "신호일": df.index[-k].date(), **ts.row_of(df, -k)}
                    row.pop("정배열", None)
                    out["토네이도 매수"] = row
                    break
            s = ts.sell_row(df)
            if s:
                out["토네이도 매도"] = {**head, **s}
            w = ts.warn_row(df) if ts.WARN_GAP else None
            if w:
                out["토네이도 매도주의"] = {**head, **w}
            # ①-2 순위 : 1·2번선 돌파 후 3번선·4번선 단계 (제1·2·3순위)
            ra = tk.prepare_rank(df)
            if ra is not None:
                n = len(ra["c"])
                for k in range(1, ts.SIGNAL_DAYS + 1):
                    rrow = tk.rank_row(df, ra, n - k)
                    if rrow:
                        out["토네이도 순위"] = {**head, "신호일": df.index[n - k].date(), **rrow}
                        break
    except Exception:
        pass
    # ② 20일선 재돌파
    try:
        a = sc.prepare(raw)
        if a is not None:
            n = len(a["c"])
            for k in range(1, sc.SIGNAL_DAYS + 1):
                info = sc.detect_at(a, n - k)
                if info:
                    out["20일선 재돌파"] = {**head, **info}
                    break
    except Exception:
        pass
    # ③ 역배열 돌파
    try:
        df = rb.compute(raw)
        if df is not None and trade_day in df.index:
            end = df.index.get_loc(trade_day)
            for k in range(rb.SIGNAL_DAYS):
                j = end - k
                if j > 0 and df["signal"].iloc[j]:
                    out["역배열 돌파"] = {**head, "신호일": df.index[j].date(), **rb.row_of(df, j)}
                    break
    except Exception:
        pass
    return out


def run_all(universe=None, log=print):
    """전 종목 검색 → (결과 {시트이름: DataFrame}, 기준일, 검색종목수, 데이터실패수)"""
    u = universe if universe is not None else ts.get_universe()
    trade_day = ts.last_trade_day()
    cached = sum(os.path.exists(os.path.join(ts.CACHE_DIR, f"{c}.pkl")) for c in u["Code"])
    if cached < len(u) * 0.9:
        log(f"검색 대상 {len(u)}종목 · 기준일 {trade_day.date()} · 처음이라 전체 데이터를 받습니다 (20~40분, 한 번만)…")
    else:
        log(f"검색 대상 {len(u)}종목 · 기준일 {trade_day.date()} · 저장 데이터에 오늘 시세만 붙입니다 (1~2분)…")
    rows = {k: [] for k in SHEETS}
    fails = 0
    with ThreadPoolExecutor(ts.WORKERS) as ex:
        futs = {ex.submit(ts.fast_fetch, r, trade_day): r for r in u.itertuples()}
        for i, f in enumerate(as_completed(futs), 1):
            r = futs[f]
            if i % 200 == 0:
                log(f"  {i}/{len(u)} 완료")
            try:
                raw = f.result()
            except Exception:
                fails += 1
                continue
            for k, v in check_one(r, raw, trade_day).items():
                rows[k].append(v)

    res = {k: pd.DataFrame(v) for k, v in rows.items()}
    sort = {"토네이도 매수": (["등급", "두 묶음 간격(%)", "등락률(%)"], [True, True, False]),
            "토네이도 매도": (["매수 신호일"], [True]),
            "토네이도 매도주의": (["5·10 간격(%)"], [True]),
            "토네이도 순위": (["순위", "등락률(%)"], [True, False]),
            "20일선 재돌파": (["신호일", "통과 선(20개 중)"], [False, False]),
            "역배열 돌파": (["등급", "등락률(%)"], [True, False])}
    for k, (cols, asc) in sort.items():
        if len(res[k]):
            res[k] = res[k].sort_values(cols, ascending=asc).reset_index(drop=True)
    return res, trade_day, len(u), fails


def main():
    print("=" * 64)
    print("  🌪 토네이도 통합 검색기 : 토네이도 돌파 · 20일선 재돌파 · 역배열 돌파")
    print("=" * 64)
    res, day, total, fails = run_all()
    pd.set_option("display.width", 250)
    for k in SHEETS:
        df = res[k]
        print(f"\n■ {k} : {len(df)}종목")
        if len(df):
            cols = [c for c in ["시장", "종목명", "종목코드", "순위", "단계", "등급", "신호일", "종가", "오늘 종가", "등락률(%)",
                                "통과 선(20개 중)", "매수 신호일", "수익률(%)", "5·10 간격(%)"] if c in df]
            print(df[cols].to_string(index=False))
        else:
            print("   없음")
    fname = os.path.join(HERE, f"토네이도_통합_{day:%Y%m%d}.xlsx")
    try:
        with pd.ExcelWriter(fname, engine="openpyxl") as w:
            for k in SHEETS:
                df = res[k]
                (df if len(df) else pd.DataFrame({"결과": ["해당 종목 없음"]})).to_excel(w, sheet_name=k, index=False)
            for ws in w.sheets.values():
                for col in ws.columns:
                    ws.column_dimensions[col[0].column_letter].width = 14
    except PermissionError:
        print("\n엑셀 파일이 열려 있어서 저장하지 못했습니다. 엑셀을 닫고 다시 실행하세요.")
        return
    summary = " · ".join(f"{k} {len(res[k])}" for k in SHEETS)
    print(f"\n→ {fname} 저장\n   ({summary}, 데이터 실패 {fails}건)")
    if sys.platform.startswith("win"):
        try:
            os.startfile(fname)
        except Exception:
            pass


if __name__ == "__main__":
    main()
