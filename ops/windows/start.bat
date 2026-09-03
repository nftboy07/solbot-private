@echo off
REM Same entrypoint as Linux: python main.py from the repo root.
cd /d "%~dp0..\.."

if exist "venv\Scripts\activate.bat" (
  call "venv\Scripts\activate.bat"
) else if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
)

title solbot
echo Starting solbot from %CD%
python main.py
if errorlevel 1 py -3 main.py
