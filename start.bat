@echo off
rem Starts Simple Player from this folder. Double-click it, no command window needed first.
cd /d "%~dp0"

set PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe
if not exist "%PY%" set PY=python

"%PY%" player.py %*

rem If the player closed because of an error, keep this window open so you can read it.
if errorlevel 1 (
    echo.
    echo Simple Player stopped with an error. Read the lines above.
    pause
)
