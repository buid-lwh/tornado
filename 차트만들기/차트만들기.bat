@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
chcp 65001 >nul
python make_chart.py %*
if errorlevel 9009 py make_chart.py %*
echo.
pause
