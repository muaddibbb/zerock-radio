@echo off
REM ZeRock Poller - Install Windows Task Scheduler entry
REM Run this once as Administrator

set SCRIPT=%~dp0zerock-poller.ps1

echo Installing ZeRock Poller as a scheduled task...

schtasks /create ^
  /tn "ZeRockPoller" ^
  /tr "powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File \"%SCRIPT%\"" ^
  /sc MINUTE ^
  /mo 1 ^
  /ru SYSTEM ^
  /f

if %ERRORLEVEL%==0 (
  echo.
  echo SUCCESS: Task "ZeRockPoller" installed.
  echo It will run every 60 seconds automatically.
  echo.
  echo To verify: Task Scheduler ^> Task Scheduler Library ^> ZeRockPoller
  echo Log file:  C:\zerock_poller.log
) else (
  echo.
  echo FAILED. Make sure you are running as Administrator.
)

pause
