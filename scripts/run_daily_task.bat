@echo off
setlocal
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "C:\Users\dmlwn\Downloads\us-market-v2-restore\us-market-v2"
if not exist logs mkdir logs
".venv\Scripts\python.exe" scripts\handoff.py --provider composite >> logs\daily.log 2>&1
