# =====================================================================
#  Crea el acceso directo "Jarvis" (icono de robot) en el Escritorio y
#  en el menu Inicio. Abre iniciar_jarvis.bat sin ventana de consola.
#  Si ya existe, lo actualiza. No toca nada mas.
#  Uso: powershell -ExecutionPolicy Bypass -File crear_acceso_jarvis.ps1
# =====================================================================
$dir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$bat   = Join-Path $dir "iniciar_jarvis.bat"
$icono = Join-Path $dir "jarvis.ico"
foreach ($f in $bat, $icono) { if (-not (Test-Path $f)) { throw "No encuentro $f" } }

$sh = New-Object -ComObject WScript.Shell
$destinos = @(
  (Join-Path ([Environment]::GetFolderPath("Desktop"))   "Jarvis.lnk"),
  (Join-Path ([Environment]::GetFolderPath("Programs"))  "Jarvis.lnk")
)
foreach ($d in $destinos) {
  $l = $sh.CreateShortcut($d)
  $l.TargetPath       = $bat
  $l.WorkingDirectory = $dir
  $l.IconLocation     = "$icono,0"
  $l.WindowStyle      = 7          # la consola del .bat sale minimizada
  $l.Description      = "Jarvis, tu asistente"
  $l.Save()
  "Creado: $d"
}
"Listo. Si el icono no cambia en el escritorio, pulsa F5 sobre el escritorio."
