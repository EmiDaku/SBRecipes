@echo off
REM Builds SkyBlockTracker.exe  (run this on Windows, in the same folder as skyblock_tracker.py)
python -m pip install --upgrade pyinstaller || goto :err
python -m PyInstaller --noconfirm --onefile --windowed --name SkyBlockTracker --icon myicon.ico --add-data "myicon.ico;." skyblock_tracker.py || goto :err
echo.
echo Done! Your exe is at: dist\SkyBlockTracker.exe
pause
exit /b 0
:err
echo Build failed.
pause
exit /b 1
