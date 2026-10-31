@echo off
rem Збирання MarstekMetrics.exe (потрібно: pip install -e .[dev])
pyinstaller --noconfirm --clean --windowed --onefile --name MarstekMetrics run_gui.py
echo.
echo Готово: dist\MarstekMetrics.exe
