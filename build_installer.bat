@echo off
REM Builds C:\build\Output\SimplePlayer_Setup.exe with Inno Setup 6.
REM Run build_exe.bat FIRST (it makes C:\build\SimplePlayer\dist\SimplePlayer).
REM Everything is written to C:\build\installer_log.txt. If something fails, send that file.
setlocal EnableExtensions
set "OUT=C:\build\SimplePlayer\dist\SimplePlayer"
set "LOG=C:\build\installer_log.txt"
set "ISCC="
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"

echo.
if "%ISCC%"=="" (
  echo Could not find ISCC.exe. Is Inno Setup 6 installed? Send Claude the folder where it is installed.
  goto :end
)
if not exist "%OUT%\SimplePlayer.exe" (
  echo Missing %OUT%\SimplePlayer.exe. Run build_exe.bat first.
  goto :end
)
if not exist "%OUT%\vlc\libvlc.dll" (
  echo Missing the vlc folder in %OUT%. Run build_exe.bat first.
  goto :end
)
echo Using %ISCC%
echo Building the installer. This takes a few minutes. Details go to %LOG%
"%ISCC%" "%~dp0SimplePlayer.iss" >"%LOG%" 2>&1
if errorlevel 1 (
  echo Installer build FAILED. Send this file to Claude: %LOG%
) else (
  echo Done. Your installer is C:\build\Output\SimplePlayer_Setup.exe
)
:end
echo.
pause
