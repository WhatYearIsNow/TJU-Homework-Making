#define MyAppName "题目图片自动排版成 PDF"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "TJU Homework Making contributors"
#define MyAppExeName "pdf_exam_formatter_gui.exe"
#define MyAppDir "dist\\pdf_exam_formatter_gui"
#define MyOutputDir "dist_installer"

[Setup]
AppId={{B1E81504-73EE-45FE-A3C6-A03F4AB11B5B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\pdf_exam_formatter_gui
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
Compression=lzma
SolidCompression=yes
WizardStyle=modern
OutputDir={#MyOutputDir}
OutputBaseFilename=pdf_exam_formatter_setup
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
Source: "{#MyAppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: ".cache\*,debug\*,outputs\*"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动题目图片自动排版成 PDF"; Flags: nowait postinstall skipifsilent
