# -*- coding: utf-8 -*-
"""차트만들기.py 실행 도우미 (bat 에서 한글 파일명 문제를 피하려고 ASCII 이름으로 둠)."""
import os
import runpy
import sys
import traceback

here = os.path.dirname(os.path.abspath(__file__))
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass
args = sys.argv[1:]
if not args:
    q = input("종목명 또는 6자리 코드를 입력하세요: ").strip()
    if not q:
        sys.exit("입력이 없습니다.")
    args = [q]
if "--열기" not in args:
    args.append("--열기")
sys.argv = [os.path.join(here, "차트만들기.py")] + args
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
except SystemExit as e:
    if e.code not in (None, 0):
        print(f"\n[중단] {e.code}")
except Exception:
    traceback.print_exc()
