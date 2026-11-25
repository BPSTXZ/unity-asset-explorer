@echo off
rem 打包免安装单文件 exe（需先有 .venv，见 run.bat）
cd /d "%~dp0"
if not exist .venv (
  echo Creating venv...
  uv venv --python 3.12 .venv || py -3.12 -m venv .venv
  uv pip install --python .venv\Scripts\python.exe -r requirements.txt || .venv\Scripts\pip install -r requirements.txt
)
uv pip install --python .venv\Scripts\python.exe pyinstaller || .venv\Scripts\pip install pyinstaller
.venv\Scripts\python.exe -m PyInstaller asset_explorer.spec --noconfirm --distpath dist --workpath build
echo.
echo Done: dist\AssetExplorer.exe
pause