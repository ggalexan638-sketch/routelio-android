[Setup]
AppName=LiveUT TV
AppVersion=1.3.0
DefaultDirName={autopf}\LiveUT TV
DefaultGroupName=LiveUT TV
OutputDir=Ausgabe
OutputBaseFilename=LiveUT-TV-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
[Files]
Source: "dist\LiveUT TV 1.3 Multi UT\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autodesktop}\LiveUT TV"; Filename: "{app}\LiveUT TV 1.3 Multi UT.exe"
Name: "{group}\LiveUT TV"; Filename: "{app}\LiveUT TV 1.3 Multi UT.exe"
[Run]
Filename: "{app}\LiveUT TV 1.3 Multi UT.exe"; Description: "LiveUT TV starten"; Flags: nowait postinstall skipifsilent
