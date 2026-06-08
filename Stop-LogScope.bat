@echo off
REM Stop LogScope Service

setlocal
set "LOGSCOPE_PID_FILE=%USERPROFILE%\.heidisql-lite\logscope.pid"
set "LOGSCOPE_EXE=%~dp0LogScope.exe"

echo Stopping LogScope...

powershell -NoProfile -ExecutionPolicy Bypass -Command "$pidFile='%LOGSCOPE_PID_FILE%'; $exePath='%LOGSCOPE_EXE%'; $stopped=$false; if (Test-Path $pidFile) { $processId=0; if ([int]::TryParse((Get-Content $pidFile -Raw).Trim(), [ref]$processId)) { $process=Get-Process -Id $processId -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -eq 'LogScope' }; if ($process) { Stop-Process -Id $processId -Force; Write-Host 'LogScope stopped successfully.'; $stopped=$true } } else { Write-Host 'Invalid PID file removed.' }; Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }; if (-not $stopped) { $resolvedExe=(Resolve-Path -LiteralPath $exePath -ErrorAction SilentlyContinue).Path; if ($resolvedExe) { $matches=Get-Process -Name 'LogScope' -ErrorAction SilentlyContinue | Where-Object { try { $_.Path -eq $resolvedExe } catch { $false } }; if ($matches) { $matches | Stop-Process -Force; Write-Host 'LogScope stopped successfully.'; $stopped=$true } } }; if (-not $stopped) { Write-Host 'LogScope is not running.' }"

timeout /t 2 /nobreak >nul
