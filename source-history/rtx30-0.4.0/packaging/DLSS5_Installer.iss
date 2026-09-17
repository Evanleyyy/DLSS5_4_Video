#ifndef AppSource
  #error AppSource must point to the freshly built PyInstaller folder
#endif
#ifndef ProjectRoot
  #error ProjectRoot is required
#endif
#define AppVersion "0.4.0"

[Setup]
AppId={{FB3F634C-64AE-4E83-A12F-93008D65CC80}
AppName=DLSS5 30系离线工作台
AppVersion={#AppVersion}
AppPublisher=Evanleyyy
AppPublisherURL=https://github.com/Evanleyyy/DLSS5_4_Video
DefaultDirName={code:DefaultLocation}
DefaultGroupName=DLSS5 30系离线工作台
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
OutputDir={#ProjectRoot}\output
#ifdef UpdateOnly
OutputBaseFilename=DLSS5_Update_{#AppVersion}
#else
OutputBaseFilename=DLSS5_Setup_{#AppVersion}
#endif
Compression=zip/1
SolidCompression=no
#ifdef UpdateOnly
DiskSpanning=no
#else
DiskSpanning=yes
DiskSliceSize=2000000000
SlicesPerDisk=1
#endif
UninstallDisplayIcon={app}\DLSS5_App.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
LicenseFile={#ProjectRoot}\LICENSE

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Types]
#ifdef UpdateOnly
Name: "full"; Description: "更新程序（使用已安装的运行库与模型）"
Name: "custom"; Description: "更新程序"; Flags: iscustom
#else
Name: "full"; Description: "标准安装（DLSS 可离线运行，其他模型按需准备）"
Name: "withmodels"; Description: "安装并准备其他超分和引导模型（可能需要联网）"
Name: "custom"; Description: "自定义安装"; Flags: iscustom
#endif

[Components]
#ifdef UpdateOnly
Name: "app"; Description: "应用、DLSS 与离线超分运行库"; Types: full custom; Flags: fixed
#else
Name: "app"; Description: "应用、DLSS 与离线超分运行库"; Types: full withmodels custom; Flags: fixed
#endif
#ifndef UpdateOnly
Name: "models"; Description: "安装后检查并准备模型（可稍后准备，模型不随安装包分发）"; Types: withmodels
Name: "models\guidance"; Description: "深度与光流模型"; Types: withmodels
Name: "models\pisa"; Description: "PiSA-SR 图片超分"; Types: withmodels
Name: "models\seedvr2"; Description: "SeedVR2 3B FP8 图片／视频超分"; Types: withmodels
Name: "models\vosr"; Description: "VOSR 2.0 图片超分"; Types: withmodels
#endif

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#ProjectRoot}\runtime\dlssnr\manifest.json"; DestDir: "{app}\runtime\dlssnr"; Flags: ignoreversion; Components: app
Source: "{#ProjectRoot}\runtime\dlssnr\310.8.SF-v2\nvngx_dlssnr.dll"; DestDir: "{app}\runtime\dlssnr\310.8.SF-v2"; Flags: ignoreversion; Components: app
Source: "{#ProjectRoot}\runtime\dlssnr\310.8.SF\nvngx_dlssnr.dll"; DestDir: "{app}\runtime\dlssnr\310.8.SF"; Flags: ignoreversion; Components: app
#ifdef UpdateOnly
Source: "{#AppSource}\DLSS5_App.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: app
#else
Source: "{#AppSource}\*"; DestDir: "{app}"; Excludes: "runtime\*"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: app
Source: "{#ProjectRoot}\runtime\python\*"; DestDir: "{app}\runtime\python"; Excludes: "__pycache__\*"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: app
Source: "{#ProjectRoot}\runtime\sr-packages\*"; DestDir: "{app}\runtime\sr-packages"; Excludes: "__pycache__\*,*.pyc,*.lib,*.h,*.hpp,*.cuh,torch\include\*,PyInstaller\*,_pyinstaller_hooks_contrib\*,pip\*"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: app
Source: "{#ProjectRoot}\runtime\pisa-packages\*"; DestDir: "{app}\runtime\pisa-packages"; Excludes: "__pycache__\*,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: app
Source: "{#ProjectRoot}\runtime\sr-engines\*"; DestDir: "{app}\runtime\sr-engines"; Excludes: ".git\*,.idea\*,__pycache__\*,*.pyc,figs\*,assets\*"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: app
#endif
Source: "{#ProjectRoot}\packaging\sr_worker.py"; DestDir: "{app}\runtime\sr-worker"; Flags: ignoreversion; Components: app
Source: "{#ProjectRoot}\source\dlss5standaloneV2\image_denoise.py"; DestDir: "{app}\runtime\sr-worker"; Flags: ignoreversion; Components: app
Source: "{#ProjectRoot}\source\dlss5standaloneV2\task_control.py"; DestDir: "{app}\runtime\sr-worker"; Flags: ignoreversion; Components: app
Source: "{#ProjectRoot}\docs\30系安装与使用.md"; DestDir: "{app}\说明"; Flags: ignoreversion
Source: "{#ProjectRoot}\docs\0.4.0更新说明.md"; DestDir: "{app}\说明"; Flags: ignoreversion
Source: "{#ProjectRoot}\docs\模型按需部署说明.md"; DestDir: "{app}\说明"; Flags: ignoreversion
Source: "{#ProjectRoot}\docs\本地超分操作说明.md"; DestDir: "{app}\说明"; Flags: ignoreversion
Source: "{#ProjectRoot}\docs\暂停生成操作说明.md"; DestDir: "{app}\说明"; Flags: ignoreversion
Source: "{#ProjectRoot}\docs\空格播放操作说明.md"; DestDir: "{app}\说明"; Flags: ignoreversion
Source: "{#ProjectRoot}\docs\导出缓存修复说明.md"; DestDir: "{app}\说明"; Flags: ignoreversion
Source: "{#ProjectRoot}\docs\参数预设操作说明.md"; DestDir: "{app}\说明"; Flags: ignoreversion
Source: "{#ProjectRoot}\THIRD_PARTY_NOTICES.md"; DestDir: "{app}\说明"; Flags: ignoreversion
Source: "{#ProjectRoot}\packaging\sr-assets-manifest.json"; DestDir: "{app}\说明"; Flags: ignoreversion

[Icons]
Name: "{group}\DLSS5 30系离线工作台"; Filename: "{app}\DLSS5_App.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\DLSS5 30系离线工作台"; Filename: "{app}\DLSS5_App.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
#ifndef UpdateOnly
Filename: "{app}\DLSS5_App.exe"; Parameters: "--prepare-models={code:SelectedModels}"; StatusMsg: "检查并准备本地模型"; Flags: waituntilterminated skipifsilent; Check: HasSelectedModels
#endif
Filename: "{app}\DLSS5_App.exe"; Description: "启动 DLSS5 30系离线工作台"; Flags: nowait postinstall skipifsilent

[Code]
#ifndef UpdateOnly
function SelectedModels(Param: String): String;
begin
  Result := '';
  if WizardIsComponentSelected('models\guidance') then Result := Result + 'guidance,';
  if WizardIsComponentSelected('models\pisa') then Result := Result + 'pisa,';
  if WizardIsComponentSelected('models\seedvr2') then Result := Result + 'seedvr2,';
  if WizardIsComponentSelected('models\vosr') then Result := Result + 'vosr,';
  if Length(Result) > 0 then Delete(Result, Length(Result), 1);
end;

function HasSelectedModels: Boolean;
begin
  Result := SelectedModels('') <> '';
end;
#endif
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if FileExists(ExpandConstant('{app}\DLSS5_App.exe')) and
     not FileExists(ExpandConstant('{app}\runtime\dlssnr\manifest.json')) then
  begin
    Result := '该目录包含原版或其他版本程序。请为 30 系版本选择独立目录，保留原版安装。';
    Exit;
  end;
#ifdef UpdateOnly
  if not FileExists(ExpandConstant('{app}\DLSS5_App.exe')) or
     not FileExists(ExpandConstant('{app}\_internal\python312.dll')) or
     not FileExists(ExpandConstant('{app}\_internal\base_library.zip')) or
     not FileExists(ExpandConstant('{app}\runtime\python\python.exe')) or
     not FileExists(ExpandConstant('{app}\runtime\sr-packages\torch\__init__.py')) then
    Result := '这是 {#AppVersion} 更新安装包。请先安装 0.4.0 30 系完整安装包，再选择其独立安装目录。'
  else
    Result := '';
#endif
end;

function DefaultLocation(Param: String): String;
begin
  Result := ExtractFileDrive(ExpandConstant('{src}')) + '\DLSS5OfflineRTX30';
end;
