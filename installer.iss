; Inno Setup script -> installer\BG-Remove-Setup-1.0.0.exe
; Build the app first (PyInstaller), then:  ISCC.exe installer.iss

#define AppName "BG Remove"
#define AppVersion "1.0.0"
#define AppExe "BG Remove.exe"

[Setup]
AppId={{6B1E4E7C-5F0A-4C2B-9F7D-3A8E2C1D9B40}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=BG Remove
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; installs per-user without admin rights; admins can choose "all users"
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=installer
OutputBaseFilename=BG-Remove-Setup-{#AppVersion}
SetupIconFile=bgremove.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
DiskSpanning=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "dist\BG Remove\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "redist\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsWebView2

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Installing Microsoft Edge WebView2 runtime..."; Check: NeedsWebView2; Flags: waituntilterminated
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\{#AppName}"

[Code]
const
  WV2 = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function HasVersion(Root: Integer; Key: String): Boolean;
var v: String;
begin
  Result := RegQueryStringValue(Root, Key, 'pv', v) and (v <> '') and (v <> '0.0.0.0');
end;

{ the UI needs the Edge WebView2 runtime (built into Windows 11; most Windows 10 PCs have it too) }
function NeedsWebView2: Boolean;
begin
  Result := not (HasVersion(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WV2) or
                 HasVersion(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WV2) or
                 HasVersion(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\' + WV2));
end;
