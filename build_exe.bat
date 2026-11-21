@echo off
REM Builds Simple Player as a folder (SimplePlayer.exe + the VLC runtime next to it).
REM The build happens in C:\build\SimplePlayer, OUTSIDE OneDrive, so OneDrive cannot lock files.
REM Needs the 64-bit VLC installed (only to copy its files from).
REM Everything is written to C:\build\build_log.txt. If something fails, send that file.
REM Close SimplePlayer before you run this (a running copy locks its files).
setlocal EnableExtensions
if "%PY%"=="" set PY="%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist %PY% set PY=python
set "VLC_DIR=C:\Program Files\VideoLAN\VLC"
set "B=C:\build\SimplePlayer"
set "OUT=%B%\dist\SimplePlayer"
set "LOG=C:\build\build_log.txt"
if not exist "C:\build" mkdir "C:\build"

echo.
echo Building. The details go to %LOG%
call :main >"%LOG%" 2>&1
set RC=%errorlevel%
echo.
if not "%RC%"=="0" (
  echo Build FAILED. Send this file to Claude: %LOG%
) else (
  echo Done. Run %OUT%\SimplePlayer.exe
)
pause
exit /b %RC%

:say
echo %~1
>con echo %~1
exit /b 0

:main
call :say "[1/6] Checking the 64-bit VLC in %VLC_DIR%"
for %%F in (libvlc.dll libvlccore.dll) do if not exist "%VLC_DIR%\%%F" (call :say "Missing %VLC_DIR%\%%F. Install the 64-bit VLC." & exit /b 1)
if not exist "%VLC_DIR%\plugins" (call :say "Missing the plugins folder in %VLC_DIR%." & exit /b 1)
powershell -NoProfile -Command "$b=[IO.File]::ReadAllBytes('%VLC_DIR%\libvlc.dll'); $o=[BitConverter]::ToInt32($b,60); if([BitConverter]::ToUInt16($b,$o+4) -eq 0x8664){exit 0}else{exit 1}"
if errorlevel 1 (call :say "This VLC is not 64-bit (or its libvlc.dll cannot be read). Install the 64-bit VLC." & exit /b 1)

call :say "[2/6] Removing the old build files (only inside %B%)"
if exist "%B%\dist" rmdir /s /q "%B%\dist"
if exist "%B%\work" rmdir /s /q "%B%\work"
if exist "%B%\dist" (call :say "Could not delete %B%\dist. Close SimplePlayer and try again." & exit /b 1)
if exist "%B%\work" (call :say "Could not delete %B%\work. Close SimplePlayer and try again." & exit /b 1)

call :say "[3/6] Copying the source to %B%\src"
if not exist "%B%\src" mkdir "%B%\src"
copy /Y "%~dp0player.py" "%B%\src\" >nul || exit /b 1
copy /Y "%~dp0icon.ico" "%B%\src\" >nul || exit /b 1
copy /Y "%~dp0requirements.txt" "%B%\src\" >nul || exit /b 1
copy /Y "%~dp0version_info.txt" "%B%\src\" >nul || exit /b 1

call :say "[4/6] Installing PyInstaller and the requirements (a few minutes the first time)"
%PY% -m pip install -r "%B%\src\requirements.txt" pyinstaller || (call :say "pip failed." & exit /b 1)

call :say "[5/6] Building the program (a few minutes)"
cd /d "%B%\src" || exit /b 1
%PY% -m PyInstaller --noconfirm --clean --windowed --name SimplePlayer --icon "%B%\src\icon.ico" --version-file "%B%\src\version_info.txt" --distpath "%B%\dist" --workpath "%B%\work" --specpath "%B%\work" "%B%\src\player.py" || (call :say "PyInstaller failed." & exit /b 1)

call :say "[6/6] Copying the VLC runtime into the program folder"
if not exist "%OUT%\vlc" mkdir "%OUT%\vlc"
copy /Y "%VLC_DIR%\libvlc.dll" "%OUT%\vlc\" >nul || exit /b 1
copy /Y "%VLC_DIR%\libvlccore.dll" "%OUT%\vlc\" >nul || exit /b 1
xcopy /E /I /Y /Q "%VLC_DIR%\plugins" "%OUT%\vlc\plugins" >nul || (call :say "Copying the plugins failed." & exit /b 1)
for %%F in ("%OUT%\SimplePlayer.exe" "%OUT%\vlc\libvlc.dll" "%OUT%\vlc\libvlccore.dll" "%OUT%\vlc\plugins") do if not exist %%F (call :say "Missing %%~F after the build." & exit /b 1)

for /f %%N in ('dir /s /b /a-d "%VLC_DIR%\plugins" ^| find /c /v ""') do set SRC_N=%%N
for /f %%N in ('dir /s /b /a-d "%OUT%\vlc\plugins" ^| find /c /v ""') do set DST_N=%%N
call :say "Plugin files: %SRC_N% in VLC, %DST_N% copied"
if not "%SRC_N%"=="%DST_N%" (call :say "The plugin copy is incomplete." & exit /b 1)
for /f %%S in ('powershell -NoProfile -Command "'{0:N0} MB' -f ((Get-ChildItem -Recurse -File '%OUT%\vlc' | Measure-Object Length -Sum).Sum/1MB)"') do set VSIZE=%%S
call :say "The vlc folder is about %VSIZE% MB"
exit /b 0
