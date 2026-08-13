@echo off
setlocal
cd /d "%~dp0"

if not exist ".\dist\pdf_exam_formatter_gui\pdf_exam_formatter_gui.exe" (
    echo Packaged executable was not found.
    echo Please run build_exe.bat first.
    pause
    exit /b 1
)

set "ISCC_PATH="
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC_PATH if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%ProgramFiles%\Inno Setup 6\ISCC.exe"

if not defined ISCC_PATH (
    echo Inno Setup was not found. Installing it with winget...
    winget install --exact --id JRSoftware.InnoSetup --source winget --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo Inno Setup installation failed.
        pause
        exit /b 1
    )
    if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
    if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    if not defined ISCC_PATH if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%ProgramFiles%\Inno Setup 6\ISCC.exe"
)

if not defined ISCC_PATH (
    echo ISCC.exe was still not found after installation.
    pause
    exit /b 1
)

if not exist ".\dist_installer" mkdir ".\dist_installer"

echo Building setup package, please wait...
"%ISCC_PATH%" ".\pdf_exam_formatter_setup.iss"
if errorlevel 1 (
    echo Setup build failed. Check the log above.
    pause
    exit /b 1
)

echo.
echo Build completed.
echo Installer: dist_installer\pdf_exam_formatter_setup.exe
pause
