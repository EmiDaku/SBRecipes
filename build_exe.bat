@echo off
REM Build the account + coin edition of the SkyBlock Tracker.
REM hypixel_sync.py and hypixel_economy.py are imported by skyblock_tracker.py,
REM so PyInstaller bundles them automatically.
REM Your API key is NEVER bundled: it lives in
REM %APPDATA%\SkyBlockTracker\hypixel_api_key.txt (or an env var) at runtime.
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller || goto :err
if not exist "hypixel_sync.py" (
  echo Missing hypixel_sync.py next to this script. & goto :err
)
if not exist "hypixel_economy.py" (
  echo Missing hypixel_economy.py next to this script. & goto :err
)
if exist "myicon.ico" (
python -m PyInstaller --noconfirm --onefile --windowed --name SkyBlockTracker ^
  --icon "myicon.ico" --add-data "myicon.ico;." "skyblock_tracker.py" || goto :err
) else (
python -m PyInstaller --noconfirm --onefile --windowed --name SkyBlockTracker ^
  "skyblock_tracker.py" || goto :err
)
echo.
echo Done! Your exe is at: dist\SkyBlockTracker.exe
echo Save data, caches and your API key live in: %%APPDATA%%\SkyBlockTracker
pause
exit /b 0
:err
echo Build failed.
pause
exit /b 1