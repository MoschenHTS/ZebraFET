; ZebraFET.iss — Inno Setup 6 installer script for Windows
;
; Usage (after running PyInstaller):
;   iscc ZebraFET.iss
;
; Requires: Inno Setup 6 (https://jrsoftware.org/isinfo.php)
; Build the PyInstaller bundle first:
;   pyinstaller ZebraFET.spec --noconfirm
;
; Output: dist\ZebraFET-setup.exe

[Setup]
AppName=ZebraFET
AppVersion=2.1.2 ; Keep in sync with src/core/constants.py APP_VERSION
AppPublisher=Henrique Tamanini S. Moschen
AppPublisherURL=https://orcid.org/0000-0002-1920-8915
AppSupportURL=https://github.com/MoschenHTS/ZebraFET
AppUpdatesURL=https://github.com/MoschenHTS/ZebraFET/releases
DefaultDirName={autopf}\ZebraFET
DefaultGroupName=ZebraFET
AllowNoIcons=yes
LicenseFile=resources\docs\Licenses\GPL-3.0.txt
; InfoBeforeFile=resources\welcome.rtf   ; uncomment if you add a welcome RTF
OutputDir=dist
OutputBaseFilename=ZebraFET-setup
; SetupIconFile=icons\fishapp_icon.ico   ; uncomment once a .ico file is added to icons\
Compression=lzma
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter=ZebraFET.exe
WizardSizePercent=120
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; All files from PyInstaller COLLECT output
Source: "dist\ZebraFET\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start menu shortcut
Name: "{group}\ZebraFET"; Filename: "{app}\ZebraFET.exe"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,ZebraFET}"; Filename: "{uninstallexe}"
; Optional desktop shortcut
Name: "{autodesktop}\ZebraFET"; Filename: "{app}\ZebraFET.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\ZebraFET.exe"; Description: "{cm:LaunchProgram,ZebraFET}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove the app folder on uninstall (leaves user data in Documents\ZebraApp intact)
Type: filesandordirs; Name: "{app}"

[Registry]
; Register .zfet file type (ZebraFET Project Archive)
Root: HKCR; Subkey: ".zfet"; ValueType: string; ValueName: ""; ValueData: "ZebraFETProject"; Flags: uninsdeletevalue
Root: HKCR; Subkey: "ZebraFETProject"; ValueType: string; ValueName: ""; ValueData: "ZebraFET Project Archive"; Flags: uninsdeletekey
Root: HKCR; Subkey: "ZebraFETProject\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\ZebraFET.exe,0"
Root: HKCR; Subkey: "ZebraFETProject\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\ZebraFET.exe"" ""%1"""

[Code]
// Optional: show a "Thank you" page or extra validation here
// This block is intentionally minimal — the in-app first-run wizard handles
// user-facing setup (license acceptance, data directory, preferences).
