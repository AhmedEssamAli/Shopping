@echo off
rem Starts the Telegram bot + web page. Logs go to %USERPROFILE%\.shopper\serve.log
cd /d "%~dp0..\.."
if not exist "%USERPROFILE%\.shopper" mkdir "%USERPROFILE%\.shopper"
call .venv\Scripts\activate.bat
shopper serve >> "%USERPROFILE%\.shopper\serve.log" 2>&1
