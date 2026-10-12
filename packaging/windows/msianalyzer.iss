; packaging/windows/msianalyzer.iss — Inno Setup script, run by build.ps1
; (which passes /DAppVersion from pyproject.toml).
;
; Per-user install into %LOCALAPPDATA%\Programs\MSIAnalyzer: no admin rights
; and no UAC prompt. Logs live elsewhere (%LOCALAPPDATA%\MSIAnalyzer\Logs, see
; logging_utils.default_debug_log_dir), so uninstalling never touches them.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; Never change AppId: Windows uses it to recognise upgrades of the same app.
AppId={{5065FCC9-000F-4A08-B7B0-A5BEEF260220}
AppName=MSIAnalyzer
AppVersion={#AppVersion}
AppPublisher=Matteo Miotto
DefaultDirName={autopf}\MSIAnalyzer
DefaultGroupName=MSIAnalyzer
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\..\dist
OutputBaseFilename=MSIAnalyzer-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\msianalyzer-gui.exe
; The app holds a single-instance lock; close it before upgrading files.
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\msianalyzer-gui\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; An upgrade must not leave the previous version's libraries behind.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\MSIAnalyzer"; Filename: "{app}\msianalyzer-gui.exe"
Name: "{autodesktop}\MSIAnalyzer"; Filename: "{app}\msianalyzer-gui.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\msianalyzer-gui.exe"; Description: "{cm:LaunchProgram,MSIAnalyzer}"; Flags: nowait postinstall skipifsilent
