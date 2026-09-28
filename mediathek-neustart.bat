@echo off
cd /d "%~dp0"
title ArcTime - Neustart

echo ============================================
echo   ArcTime wird neu gestartet
echo ============================================
echo.

powershell -NoProfile -Command "$p = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -Expand OwningProcess -Unique; if ($p) { foreach ($id in $p) { Write-Host ('Beende laufenden Server (PID ' + $id + ')...'); Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }; Start-Sleep -Seconds 2 } else { Write-Host 'Kein laufender Server gefunden - starte neu.' }"

echo.
python mediathek.py

if errorlevel 1 (
    echo.
    echo Der Server konnte nicht gestartet werden - siehe Fehlermeldung oben.
    pause
)
