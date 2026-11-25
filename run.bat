@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Creating venv...
  uv venv --python 3.12 .venv || py -3.12 -m venv .venv
  uv pip install --python .venv\Scripts\python.exe -r requirements.txt || .venv\Scripts\pip install -r requirements.txt
)
start "" http://127.0.0.1:8765
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
pause