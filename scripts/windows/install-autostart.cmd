@echo off
rem Registers a Windows scheduled task that runs start-shopper.cmd at every logon.
rem Remove it later with:  schtasks /Delete /TN "shopper" /F
schtasks /Create /TN "shopper" /TR "\"%~dp0start-shopper.cmd\"" /SC ONLOGON /RL LIMITED /F
if errorlevel 1 (echo Failed to create the task. & exit /b 1)
echo Done. shopper will start at your next logon. To start it now:  schtasks /Run /TN "shopper"
