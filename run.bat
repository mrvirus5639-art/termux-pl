@echo off
setlocal
set PY=python
where python >nul 2>nul || set PY=%USERPROFILE%\anaconda3\python.exe
chcp 65001 >nul
"%PY%" -m termuxpl %*
