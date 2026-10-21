#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef BundleDir
  #error BundleDir must point to the assembled Echo Reader bundle
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

[Setup]
AppId={{F22E6C98-37C1-4CE1-90C6-35E9D349F86D}
AppName=Echo Reader
AppVersion={#AppVersion}
AppPublisher=PakhomLeo and contributors
AppPublisherURL=https://github.com/PakhomLeo/echo-reader
AppSupportURL=https://github.com/PakhomLeo/echo-reader/issues
AppUpdatesURL=https://github.com/PakhomLeo/echo-reader/releases
AppCopyright=Copyright (c) 2026 PakhomLeo and contributors. Released under the MIT License.
LicenseFile={#BundleDir}\LICENSE.txt
DefaultDirName={localappdata}\Programs\Echo Reader
DefaultGroupName=Echo Reader
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=EchoReader-{#AppVersion}-win-x64-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern dynamic
UninstallDisplayIcon={app}\EchoReader.exe
CloseApplications=yes
RestartApplications=no
AppMutex=Global\EchoReader.LocalApp,Local\EchoReader.LocalApp

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\web"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\browser-extension"
Type: filesandordirs; Name: "{app}\config-templates"
Type: filesandordirs; Name: "{app}\licenses"
Type: files; Name: "{app}\EchoReader.exe"
Type: files; Name: "{app}\echo-backend.exe"
Type: files; Name: "{app}\README.md"
Type: files; Name: "{app}\DEVELOPMENT.md"
Type: files; Name: "{app}\THIRD_PARTY_NOTICES.md"
Type: files; Name: "{app}\LICENSE.txt"
Type: files; Name: "{app}\bundle-manifest.json"

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Echo Reader"; Filename: "{app}\EchoReader.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Echo Reader"; Filename: "{app}\EchoReader.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: checkedonce

[Run]
Filename: "{app}\EchoReader.exe"; Description: "启动 Echo Reader"; Flags: nowait postinstall skipifsilent
