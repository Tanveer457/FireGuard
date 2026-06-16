; --- FireGuard Professional Installer Script ---
; Created for Inno Setup

[Setup]
AppId={{D1A2B3C4-E5F6-4A7B-8C9D-0E1F2A3B4C5D}}
AppName=FireGuard
AppVersion=1.0.0
AppPublisher=Dr Haris _Tanveer_Affan_Raja
AppPublisherURL=https://www.fireguard-systems.com
DefaultDirName={autopf}\FireGuard
DefaultGroupName=FireGuard
; Ensure the user is asked for the installation folder and can browse
DisableDirPage=no
DisableProgramGroupPage=no
AlwaysShowDirOnReadyPage=yes
AllowNoIcons=yes
; This forces the Jetson Guide to appear INSIDE the installer before it finishes
InfoAfterFile=JETSON_SETUP_GUIDE.txt
; Output file name
OutputBaseFilename=FireGuard_Installer_v1.0
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; Icon for the installer itself
SetupIconFile=fireguard.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The main executable
Source: "dist\FireGuard\FireGuard.exe"; DestDir: "{app}"; Flags: ignoreversion
; All other files in the dist folder, EXCLUDING local data/logs
Source: "dist\FireGuard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "storage\*, logs\*, __pycache__\*, *.pyc"; Permissions: users-modify
; The icon file for shortcuts
Source: "fireguard.ico"; DestDir: "{app}"; Flags: ignoreversion
; The Jetson Setup Guide
Source: "JETSON_SETUP_GUIDE.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\FireGuard"; Filename: "{app}\FireGuard.exe"; IconFilename: "{app}\fireguard.ico"
Name: "{group}\Jetson Nano Setup Guide"; Filename: "{app}\JETSON_SETUP_GUIDE.txt"
Name: "{group}\{cm:UninstallProgram,FireGuard}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\FireGuard"; Filename: "{app}\FireGuard.exe"; Tasks: desktopicon; IconFilename: "{app}\fireguard.ico"

[Run]
Filename: "{app}\FireGuard.exe"; Description: "{cm:LaunchProgram,FireGuard}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\JETSON_SETUP_GUIDE.txt"; Description: "View Jetson Nano Setup Guide"; Flags: postinstall shellexec skipifsilent
