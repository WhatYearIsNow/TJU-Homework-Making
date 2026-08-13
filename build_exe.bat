@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD="
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python312\python.exe"
    goto :python_ready
)

where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON_CMD=py -3"
    goto :python_ready
)

where python >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON_CMD=python"
    goto :python_ready
)

echo Python 3.11+ was not found. Packaging cannot continue.
pause
exit /b 1

:python_ready
call %PYTHON_CMD% -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo Installing PyInstaller...
    call %PYTHON_CMD% -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo PyInstaller installation failed.
        pause
        exit /b 1
    )
)

echo Building executable, please wait...
taskkill /IM pdf_exam_formatter_gui.exe /F >nul 2>nul
call %PYTHON_CMD% -m PyInstaller --noconfirm --clean .\pdf_exam_formatter_gui.spec
if errorlevel 1 (
    echo Build failed. Check the log above.
    pause
    exit /b 1
)

echo.
echo Build completed.
echo Executable: dist\pdf_exam_formatter_gui\pdf_exam_formatter_gui.exe
pause
