@echo off
echo Installing dependencies...
call npm install

echo.
echo Building sam-agent.exe...
call npm run build

echo.
if exist sam-agent.exe (
  echo BUILD SUCCESSFUL: sam-agent.exe is ready.
  echo Copy sam-agent.exe + config.json to your Windows machine and run it.
) else (
  echo BUILD FAILED. Check errors above.
)
pause
