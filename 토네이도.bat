@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title 토네이도 통합 검색기
set PY=python
where python > nul 2> nul
if errorlevel 1 set PY=py
echo [1/2] 필요한 패키지를 확인하는 중...
%PY% -m pip install -q --disable-pip-version-check --upgrade finance-datareader pandas numpy openpyxl
echo [2/2] 토네이도 · 20일선 재돌파 · 역배열 돌파 검색 중...
%PY% 토네이도.py
echo.
pause
