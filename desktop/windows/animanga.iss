; AniManga Studio - instalador Windows (Fase 1). Per-user, sin admin.
; Empaqueta el shell nativo (WebView2 + libmpv) que arranca el backend WSL de
; forma invisible. Se compila con desktop/windows/build-installer.ps1, que pasa
; los defines Staging/AppVersion/Distro/LinuxPath a ISCC.

#ifndef Staging
  #define Staging "staging"
#endif
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef Distro
  #define Distro "archlinux"
#endif
#ifndef LinuxPath
  #define LinuxPath "/Manga_Upscaler_project/workspace/manga-upscaler"
#endif

#define AppName "AniManga Studio"
#define AppExe "animanga.exe"
#define AppPublisher "Roier"

[Setup]
; AppId FIJO: identifica la app para actualizaciones/desinstalacion. No cambiar.
AppId={{7B4E9D21-3C6A-4F58-9A2E-1D8C5B0F7A34}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\AniMangaStudio
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#Staging}\out
OutputBaseFilename=AniMangaStudio-Setup
SetupIconFile={#Staging}\animanga.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el Escritorio"; GroupDescription: "Accesos directos:"
Name: "enginesetup"; Description: "Preparar el motor: WSL2, dependencias, biblioteca (necesario la primera vez; puede tardar y pedir reiniciar)"; GroupDescription: "Motor:"

[Files]
Source: "{#Staging}\{#AppExe}";        DestDir: "{app}"; Flags: ignoreversion
Source: "{#Staging}\libmpv-2.dll";     DestDir: "{app}"; Flags: ignoreversion
Source: "{#Staging}\animanga.ico";     DestDir: "{app}"; Flags: ignoreversion
Source: "{#Staging}\config.json";      DestDir: "{localappdata}\AniMangaStudio"; Flags: ignoreversion
; Aprovisionamiento del motor (Fase 2): orquestador Windows + bootstrap in-distro.
Source: "{#Staging}\provision-engine.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Staging}\bootstrap-root.sh";    DestDir: "{app}"; Flags: ignoreversion
Source: "{#Staging}\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
; Directamente en Programs (no subcarpeta) → reemplaza el acceso viejo de launch.ps1
; en vez de crear una entrada "AniManga Studio" duplicada.
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\animanga.ico"
Name: "{autodesktop}\{#AppName}";  Filename: "{app}\{#AppExe}"; IconFilename: "{app}\animanga.ico"; Tasks: desktopicon

[Run]
; 1) Runtime WebView2 (Evergreen) si falta - silencioso.
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Instalando el runtime WebView2..."; Check: NeedsWebView2; Flags: waituntilterminated
; 2) Aprovisionamiento del motor (idempotente, resumible): habilita WSL2, instala
;    la distro, clona el repo y prepara todo. El .ps1 gestiona elevacion y reinicio.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\provision-engine.ps1"" -BootstrapScript ""{app}\bootstrap-root.sh"" -Distro ""{#Distro}"""; StatusMsg: "Preparando el motor (WSL2, dependencias, biblioteca)... puede tardar la primera vez"; Tasks: enginesetup; Flags: waituntilterminated
; 3) Ofrecer abrir la app al terminar.
Filename: "{app}\{#AppExe}"; Description: "Abrir {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; La config es de la app; la biblioteca/datos viven en el checkout WSL y NO se tocan.
Type: files; Name: "{localappdata}\AniMangaStudio\config.json"

[Code]
{ WebView2 se considera presente si existe la version (pv) del cliente Evergreen
  en HKLM (por-maquina) o HKCU (por-usuario). '0.0.0.0' = no instalado. }
function WebView2Installed(): Boolean;
var v: String;
begin
  Result := False;
  if RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) then
    if (v <> '') and (v <> '0.0.0.0') then Result := True;
  if not Result then
    if RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) then
      if (v <> '') and (v <> '0.0.0.0') then Result := True;
end;

function NeedsWebView2(): Boolean;
begin
  Result := not WebView2Installed();
end;

function WslAvailable(): Boolean;
begin
  Result := FileExists(ExpandConstant('{sys}\wsl.exe'));
end;
