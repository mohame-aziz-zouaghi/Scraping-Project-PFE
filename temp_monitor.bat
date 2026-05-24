@echo off
title Monitoring System
color 0A
echo ============================================================
echo     TUNISIAN TECH PRODUCTS MONITORING SYSTEM
echo ============================================================
echo.
"C:\Users\Lenovo\AppData\Local\Programs\Python\Python313\python.exe" "C:\Users\Lenovo\VisualStudioProjects\ScrapingProject\monitoring\run_monitor.py" --mode interactive
echo.
echo ============================================================
echo Monitoring system closed.
echo ============================================================
echo.
pause
del "%~f0"
