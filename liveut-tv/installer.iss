[Setup]
AppName=Panther LIVEUT
AppVersion=2.0.0
DefaultDirName={autopf}\Panther LIVEUT
DefaultGroupName=Panther LIVEUT
OutputDir=Ausgabe
OutputBaseFilename=Panther-LIVEUT-2.0-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
[Files]
Source: "dist\Panther LIVEUT 2.0\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autodesktop}\Panther LIVEUT 2.0"; Filename: "{app}\Panther LIVEUT 2.0.exe"
Name: "{group}\Panther LIVEUT 2.0"; Filename: "{app}\Panther LIVEUT 2.0.exe"
[Run]
Filename: "{app}\Panther LIVEUT 2.0.exe"; Description: "Panther LIVEUT starten"; Flags: nowait postinstall skipifsilent
