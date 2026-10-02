@echo off
REM Termux PL installer for Windows
setlocal
set PY=python
where python >nul 2>nul || set PY=%USERPROFILE%\anaconda3\python.exe
echo Using %PY%
"%PY%" -m pip uninstall -y tuneterm >nul 2>nul
"%PY%" -m pip install -e "%~dp0."
if errorlevel 1 (echo Install failed & pause & exit /b 1)
echo.
echo Done. Start the player with run.bat
pause
