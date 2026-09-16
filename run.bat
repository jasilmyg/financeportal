@echo off
title Consumer Finance Review Automation Portal
echo ===================================================
echo   Consumer Finance Review Automation Portal
echo ===================================================
echo.
echo Starting Python Flask Server on http://127.0.0.1:8005 ...
echo.

:: Start python server in a separate background command window
start "Flask Server Backend" cmd /c python app.py

:: Give Flask a couple seconds to boot up
timeout /t 3 /nobreak > nul

:: Open default browser
echo Opening portal in web browser...
start http://127.0.0.1:8005/

echo.
echo Portal started! Press any key in this window to stop.
pause > nul

:: Cleanup - kill python processes running app.py on port 8005
echo Stopping server...
taskkill /fi "windowtitle eq Flask Server Backend*" /t /f > nul 2>&1
echo Done.

