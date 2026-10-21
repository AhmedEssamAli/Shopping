@echo off
rem Opens "Shopping Chrome": a normal Chrome window with its own profile that shopper can use.
rem Sign in to noon / Amazon / talabat here once. Keep this window open (minimised is fine).
rem Chrome 136+ blocks remote debugging on your default profile, hence the separate profile folder.
set "PROFILE=%LOCALAPPDATA%\shopper-chrome"
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" (echo Chrome not found. Install Google Chrome first. & exit /b 1)
start "" "%CHROME%" --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 --user-data-dir="%PROFILE%" --no-first-run https://www.noon.com/egypt-en/
