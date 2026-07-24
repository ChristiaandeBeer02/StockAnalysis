@echo off
setlocal
cd /d "%~dp0"

python -m pip install -e ".[dev]" || exit /b 1
python -m PyInstaller stock_analysis.spec --noconfirm || exit /b 1

echo.
echo Build complete: dist\StockAnalysis.exe
endlocal
