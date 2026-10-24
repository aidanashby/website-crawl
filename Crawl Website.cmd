@echo off
cd /d "%~dp0"
title Website crawl

echo.
echo   Website crawl
echo   -------------

rem Quick check (well under a second): the venv exists and its packages import.
if not exist "venv\Scripts\python.exe" goto :setup
venv\Scripts\python.exe -c "import requests, bs4, lxml, markdownify, defusedxml" >nul 2>&1
if errorlevel 1 goto :setup
goto :url

:setup
echo.
echo   The crawler's Python environment is missing or incomplete.
set "PY="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1 && set "PY=python"
if not defined PY goto :nopython

echo.
choice /c YN /n /m "  Create it and install the packages now? [Y/N]: "
if errorlevel 2 exit /b 1
echo.
if not exist "venv\Scripts\python.exe" (%PY% -m venv venv || goto :fail)
venv\Scripts\python.exe -m pip install -r requirements.txt || goto :fail
goto :url

:nopython
echo   Python 3.11 or newer is not installed.
echo.
choice /c YN /n /m "  Install Python 3.12 with winget now? [Y/N]: "
if errorlevel 2 exit /b 1
echo.
winget install -e --id Python.Python.3.12 || goto :fail
echo.
echo   Python installed. Close this window and run Crawl Website again.
pause
exit /b 0

:fail
echo.
echo   Setup failed. See the messages above.
pause
exit /b 1

:url
echo.
set /p URL=  Site URL:

if "%URL%"=="" (
  echo   No URL given.
  pause
  exit /b 1
)

echo.
echo   [N]  New and changed pages only   (default)
echo   [F]  Full re-fetch                 - re-reads every page
echo.
echo   Either way, notes for pages no longer on the site are deleted.
echo.
choice /c NF /n /d N /t 15 /m "  Press N or F (defaults to N in 15s): "
if errorlevel 2 (set MODE=--full) else (set MODE=)

echo.
venv\Scripts\python.exe crawl.py "%URL%" %MODE%

if errorlevel 1 (
  echo.
  echo   Something went wrong.
)
echo.
pause
