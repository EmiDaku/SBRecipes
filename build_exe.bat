@echo off
REM Build the fixed SkyBlock Tracker from this directory.
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller || goto :err
if exist "myicon.ico" (
    python -m PyInstaller --noconfirm --onefile --windowed --name SkyBlockTracker --icon "myicon.ico" --add-data "myicon.ico;." "skyblock_tracker.py" || goto :err
) else (
    python -m PyInstaller --noconfirm --onefile --windowed --name SkyBlockTracker "skyblock_tracker.py" || goto :err
)
echo.
echo Done! Your exe is at: dist\SkyBlockTracker.exe
pause
exit /b 0
:err
echo Build failed.
pause
exit /b 1
