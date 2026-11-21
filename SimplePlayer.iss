; Simple Player installer (Inno Setup 6). Built by build_installer.bat, do not run by hand.
; Installs for the current user only (no admin prompt). Never sets the default player.
#define MyAppName "Simple Player"
#define MyAppVersion "1.0.0"
#define MyExe "SimplePlayer.exe"
#define DistDir "C:\build\SimplePlayer\dist\SimplePlayer"
#define IconFile "C:\build\SimplePlayer\src\icon.ico"
#define ProgId "SimplePlayer.Video"

[Setup]
AppId={{6F3B2A9E-4C1D-4E7A-9B52-8D0A7E1C5F34}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Simple Player
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#MyExe}
UninstallDisplayName={#MyAppName}
OutputDir=C:\build\Output
OutputBaseFilename=SimplePlayer_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ChangesAssociations=yes
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyExe}"; AppUserModelID: "SimplePlayer.App"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyExe}"; Tasks: desktopicon; AppUserModelID: "SimplePlayer.App"

[Registry]
; Program entry (same names the in-app "Set as default player" menu writes, so the two never fight)
Root: HKCU; Subkey: "Software\Classes\{#ProgId}"; ValueType: string; ValueName: ""; ValueData: "Video file (Simple Player)"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#ProgId}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyExe}"",0"
Root: HKCU; Subkey: "Software\Classes\{#ProgId}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyExe}"" ""%1"""
; Name shown in the "Open with" menu
Root: HKCU; Subkey: "Software\Classes\Applications\{#MyExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\{#MyExe}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyExe}"" ""%1"""
; Default apps page entry (lets Windows list Simple Player; the user still picks it himself)
Root: HKCU; Subkey: "Software\SimplePlayer\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\SimplePlayer\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Minimal video player on VLC's engine"
Root: HKCU; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "SimplePlayer"; ValueData: "Software\SimplePlayer\Capabilities"; Flags: uninsdeletevalue
; File types: only added to "Open with", nothing is made the default
Root: HKCU; Subkey: "Software\SimplePlayer\Capabilities\FileAssociations"; ValueType: string; ValueName: ".mp4"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\SimplePlayer\Capabilities\FileAssociations"; ValueType: string; ValueName: ".mkv"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\SimplePlayer\Capabilities\FileAssociations"; ValueType: string; ValueName: ".avi"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\SimplePlayer\Capabilities\FileAssociations"; ValueType: string; ValueName: ".mov"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\SimplePlayer\Capabilities\FileAssociations"; ValueType: string; ValueName: ".wmv"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\SimplePlayer\Capabilities\FileAssociations"; ValueType: string; ValueName: ".webm"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\SimplePlayer\Capabilities\FileAssociations"; ValueType: string; ValueName: ".m4v"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\Classes\.mp4\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.mkv\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.avi\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.mov\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.wmv\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.webm\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.m4v\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyExe}"; Description: "Start {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only our own program folder. %APPDATA%\SimplePlayer (settings, history) and all videos are never touched.
Type: filesandordirs; Name: "{app}\vlc"
