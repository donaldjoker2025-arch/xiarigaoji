@echo off
setlocal
rem ===========================================================
rem  Xiarigaoji - License signing GUI launcher (vendor only)
rem  Content kept ASCII-only so cmd parses it on any codepage.
rem  Uses pythonw.exe + start so no console window stays open.
rem ===========================================================

set "ROOT=%~dp0.."
set "PYW=%ROOT%\.venv\Scripts\pythonw.exe"
set "PY=%ROOT%\.venv\Scripts\python.exe"

rem No-console launch via venv pythonw (preferred)
if exist "%PYW%" (
  start "" "%PYW%" "%ROOT%\tools\license_gui.py"
  goto :eof
)

rem Fallback: console python so any error is visible
if not exist "%PY%" set "PY=python"
echo venv pythonw not found, launching with: "%PY%"
"%PY%" "%ROOT%\tools\license_gui.py"
if not "%errorlevel%"=="0" (
  echo.
  echo [Launch failed]
  echo - Missing cryptography:  .venv\Scripts\pip install cryptography
  echo - Missing private key :  .venv\Scripts\python tools\gen_keys.py
  echo.
  pause
)
endlocal
