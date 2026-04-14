@echo off
echo ============================================
echo  Ross Cameron Trading System - Install
echo ============================================
echo.
echo Installing required Python packages...
pip install webull yfinance requests pandas numpy beautifulsoup4 pytz APScheduler finvizfinance
echo.
echo Done! Run the system with:
echo   python main.py
echo.
pause
