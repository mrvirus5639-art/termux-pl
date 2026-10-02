@echo off
REM ---------------------------------------------------------------------------
REM  One-time setup: creates the GitHub repo and pushes Termux PL v1.0.0.
REM  Usage:  publish_to_github.bat [repo-name] [public|private]
REM  Defaults: repo-name = termux-pl, visibility = public
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set NAME=%~1
if "%NAME%"=="" set NAME=termux-pl
set VIS=%~2
if "%VIS%"=="" set VIS=public

set PY=python
where python >nul 2>nul || set PY=%USERPROFILE%\anaconda3\python.exe

REM --- tools -----------------------------------------------------------------
where git >nul 2>nul || (
  echo Git is not installed. Run:  winget install --id Git.Git -e
  echo Then open a NEW terminal and run this script again.
  exit /b 1
)
where gh >nul 2>nul || (
  echo GitHub CLI is not installed. Run:  winget install --id GitHub.cli -e
  echo Then open a NEW terminal and run this script again.
  exit /b 1
)

REM --- identity and login --------------------------------------------------
git config user.email >nul 2>nul || (
  echo Git doesn't know who you are yet. Run these once, then rerun this script:
  echo   git config --global user.name  "Your Name"
  echo   git config --global user.email "you@example.com"
  exit /b 1
)
gh auth status >nul 2>nul || (
  echo Logging in to GitHub - follow the prompts in your browser...
  gh auth login --hostname github.com --git-protocol https --web || exit /b 1
)
for /f "delims=" %%i in ('gh api user --jq .login') do set OWNER=%%i
if "%OWNER%"=="" (echo Could not read your GitHub username. & exit /b 1)
echo GitHub user: %OWNER%   repo: %NAME%   visibility: %VIS%

REM --- local repository ----------------------------------------------------
if exist .git (
  echo This folder is already a git repository - skipping init.
) else (
  git init -b main || exit /b 1
  "%PY%" scripts\release.py links %OWNER%/%NAME% || exit /b 1
  git add -A || exit /b 1
  git commit -m "Termux PL v1.0.0" || exit /b 1
  git tag -a v1.0.0 -m "Termux PL v1.0.0" || exit /b 1
)

REM --- create on GitHub and push -------------------------------------------
gh repo create %NAME% --%VIS% --source . --remote origin --push ^
  --description "Keyboard-driven terminal music player: spectrum visualizer, synced lyrics, online streaming" ^
  || exit /b 1
git push origin --tags || exit /b 1

echo.
echo Done:  https://github.com/%OWNER%/%NAME%
echo The v1.0.0 tag starts the Release workflow. In a few minutes, check
echo   https://github.com/%OWNER%/%NAME%/releases
endlocal
