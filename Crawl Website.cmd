@echo off
cd /d "%~dp0"
title Website crawl

echo.
echo   Website crawl
echo   -------------
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
