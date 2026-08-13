@echo off
setlocal
cd /d "%~dp0"

if exist ".\dist\pdf_exam_formatter_gui\pdf_exam_formatter_gui.exe" (
    start "" ".\dist\pdf_exam_formatter_gui\pdf_exam_formatter_gui.exe"
    exit /b 0
)

if exist "%LocalAppData%\Programs\Python\Python312\pythonw.exe" (
    "%LocalAppData%\Programs\Python\Python312\pythonw.exe" -m app.gui
    exit /b %errorlevel%
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
    pythonw -m app.gui
    exit /b %errorlevel%
)

where python >nul 2>nul
if %errorlevel%==0 (
    python -m app.gui
    exit /b %errorlevel%
)

echo No Python interpreter or packaged executable was found.
echo Install Python 3.11+ or run build_exe.bat first.
pause
