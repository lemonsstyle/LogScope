@echo off
REM LogScope Startup Script - Shows Console

setlocal
set "LOGSCOPE_HOST=127.0.0.1"
set "LOGSCOPE_PORT=8765"

title LogScope
echo.
echo ========================================
echo    LogScope - Database Log Query Tool
echo ========================================
echo.
echo Starting server...
echo.

echo Opening browser...
start "" /B cmd /C "timeout /t 2 /nobreak >nul & start http://%LOGSCOPE_HOST%:%LOGSCOPE_PORT%"

echo.
echo ========================================
echo   Service starting: http://%LOGSCOPE_HOST%:%LOGSCOPE_PORT%
echo ========================================
echo.
echo Instructions:
echo   - Browser opened automatically
echo   - This window owns the service process
echo   - You can also run Stop-LogScope.bat from another window
echo   - Data saved in: %USERPROFILE%\.heidisql-lite
echo.
echo Press Ctrl+C or close window to stop server
echo.

LogScope.exe --host %LOGSCOPE_HOST% --port %LOGSCOPE_PORT%
set "LOGSCOPE_EXIT_CODE=%ERRORLEVEL%"

echo.
echo LogScope exited with code %LOGSCOPE_EXIT_CODE%.
pause
exit /b %LOGSCOPE_EXIT_CODE%
