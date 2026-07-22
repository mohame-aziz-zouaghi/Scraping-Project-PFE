@echo off
title Monitoring System
color 0A
echo.
echo ============================================================
echo     TUNISIAN TECH PRODUCTS MONITORING SYSTEM
echo ============================================================
echo.
echo [INFO] Starting monitoring system...
echo.
"C:\Users\Lenovo\AppData\Local\Programs\Python\Python313\python.exe" "C:\Users\Lenovo\VisualStudioProjects\ScrapingProject\monitoring\run_monitor.py" --mode interactive
echo.
echo ============================================================
echo [SUCCESS] Monitoring system closed.
echo ============================================================
echo.
echo Press any key to close this window...
pause > nul
del "%~f0"
