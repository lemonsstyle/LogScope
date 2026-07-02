@echo off
REM LogScope single-window runner

setlocal
set "LOGSCOPE_HOST=127.0.0.1"
set "LOGSCOPE_PORT=8765"
set "LOGSCOPE_URL=http://%LOGSCOPE_HOST%:%LOGSCOPE_PORT%"

title LogScope - close this window to stop

echo.
echo ========================================
echo    LogScope - Database Log Query Tool
echo ========================================
echo.
echo Starting service...
echo URL: %LOGSCOPE_URL%
echo.
echo Opening browser...
start "" /B cmd /C "timeout /t 2 /nobreak >nul & start %LOGSCOPE_URL%"
echo.
echo ========================================
echo   Close this window to stop LogScope
echo ========================================
echo.
echo Data saved in: %USERPROFILE%\.heidisql-lite
echo.

LogScope.exe --host %LOGSCOPE_HOST% --port %LOGSCOPE_PORT%
set "LOGSCOPE_EXIT_CODE=%ERRORLEVEL%"

echo.
echo LogScope exited with code %LOGSCOPE_EXIT_CODE%.
pause
exit /b %LOGSCOPE_EXIT_CODE%
