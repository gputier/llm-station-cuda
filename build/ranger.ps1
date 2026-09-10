param(
  [string]$RootDir = 'D:\LLM-Setup',
  [switch]$Apply
)

# Tidies the working directory. Dry run by default: it prints what it would do
# and touches nothing. Pass -Apply to actually move.
#
# Nothing is deleted except files that are provably empty and provably always
# empty. Everything else is MOVED into a dated archive directory, so a wrong
# call costs a move back and not a download.
#
# Run this with no model loaded. It moves the log files the server writes to.

$ErrorActionPreference = 'Stop'
$stamp   = Get-Date -Format 'yyyyMMdd'
$archive = Join-Path $RootDir "_archive-$stamp"
$bench   = Join-Path $RootDir 'bench'
$results = Join-Path $bench 'resultats'
$tools   = Join-Path $RootDir 'outils'
$logs    = Join-Path $RootDir 'logs'

function Plan($action, $item, $dest) {
  "{0,-8} {1,-42} {2}" -f $action, $item, $dest
}

# Kept at the root: the launcher, and this script, which would otherwise sweep
# itself into the archive on its way through.
$garder = @('llm-ctl.ps1','ranger.ps1')

# Moved to bench\: the bench that is still used, plus its question set.
$versBench = @('banc.ps1','banc-tous.ps1','quality.ps1','epreuves.jsonl','bench-prompt.json','vision-test.png')

# Moved to outils\: small one-shot helpers that still earn their place.
$versOutils = @('gguf-head.ps1','syntax-check.ps1','check-exe.ps1','probe.ps1','kat-status.ps1','kat-rate.ps1')

# Deleted outright, and ONLY these: files that are empty and structurally so.
# The llm-out-* ones are empty because llama-server writes everything to stderr,
# which every one of them has proved for months.
$aSupprimer = @(Get-ChildItem $RootDir -File -Filter 'llm-out-*.log' -ErrorAction SilentlyContinue) +
              @(Get-ChildItem $RootDir -File -Filter 'help-b10883*.txt' -ErrorAction SilentlyContinue)
$aSupprimer = @($aSupprimer | Where-Object { $_.Length -eq 0 })

$actions = @()

foreach ($f in $aSupprimer) { $actions += Plan 'SUPPR' $f.Name '(vide)' }

foreach ($n in $versBench) {
  if (Test-Path (Join-Path $RootDir $n)) { $actions += Plan 'BENCH' $n 'bench\' }
}
foreach ($n in $versOutils) {
  if (Test-Path (Join-Path $RootDir $n)) { $actions += Plan 'OUTILS' $n 'outils\' }
}
foreach ($f in Get-ChildItem $RootDir -File -Filter 'banc-*.txt'    -EA SilentlyContinue) { $actions += Plan 'RESULT' $f.Name 'bench\resultats\' }
foreach ($f in Get-ChildItem $RootDir -File -Filter 'quality-*.txt' -EA SilentlyContinue) { $actions += Plan 'RESULT' $f.Name 'bench\resultats\' }
foreach ($f in Get-ChildItem $RootDir -File -Filter 'llm-err-*.log' -EA SilentlyContinue) { $actions += Plan 'LOGS'   $f.Name 'logs\' }

# Everything else at the root goes to the archive: dated backups of the launcher,
# the abandoned download chain, April's container files and relay scripts, the
# superseded bench launchers, and the stray logs of past campaigns.
$connus = $garder + $versBench + $versOutils + @($aSupprimer | ForEach-Object { $_.Name })
$reste = Get-ChildItem $RootDir -File | Where-Object {
  $connus -notcontains $_.Name -and
  $_.Name -notlike 'banc-*.txt' -and $_.Name -notlike 'quality-*.txt' -and $_.Name -notlike 'llm-err-*.log'
}
foreach ($f in $reste) { $actions += Plan 'ARCHIVE' $f.Name "_archive-$stamp\" }

# Directories: the two zip caches duplicate engines already unpacked next door,
# b10740 is referenced by no profile, slot-cache has been empty since April.
$dossiers = @('dl-b10826','dl-b10883','llama-cpp-b10740','slot-cache')
foreach ($d in $dossiers) {
  if (Test-Path (Join-Path $RootDir $d)) { $actions += Plan 'ARCHIVE' "$d\" "_archive-$stamp\" }
}

$actions | ForEach-Object { Write-Output $_ }
Write-Output ''
Write-Output ("{0} actions" -f $actions.Count)

if (-not $Apply) {
  Write-Output 'ESSAI A BLANC. Relancer avec -Apply pour executer.'
  return
}

# A running server holds its own log file open, and moving it would fail halfway
# through the batch. Refuse rather than half-tidy.
$vivant = Get-Process llama-server -ErrorAction SilentlyContinue
if ($vivant) { throw "llama-server tourne (pid $($vivant.Id -join ',')). Arreter le serveur avant de ranger." }

foreach ($d in @($archive, $bench, $results, $tools, $logs)) {
  New-Item -ItemType Directory -Force -Path $d | Out-Null
}

foreach ($f in $aSupprimer) { Remove-Item $f.FullName -Force }
foreach ($n in $versBench)  { $p = Join-Path $RootDir $n; if (Test-Path $p) { Move-Item $p $bench  -Force } }
foreach ($n in $versOutils) { $p = Join-Path $RootDir $n; if (Test-Path $p) { Move-Item $p $tools  -Force } }
foreach ($f in Get-ChildItem $RootDir -File -Filter 'banc-*.txt'    -EA SilentlyContinue) { Move-Item $f.FullName $results -Force }
foreach ($f in Get-ChildItem $RootDir -File -Filter 'quality-*.txt' -EA SilentlyContinue) { Move-Item $f.FullName $results -Force }
foreach ($f in Get-ChildItem $RootDir -File -Filter 'llm-err-*.log' -EA SilentlyContinue) { Move-Item $f.FullName $logs    -Force }
foreach ($f in $reste)      { if (Test-Path $f.FullName) { Move-Item $f.FullName $archive -Force } }
foreach ($d in $dossiers)   { $p = Join-Path $RootDir $d; if (Test-Path $p) { Move-Item $p $archive -Force } }

Write-Output ''
Write-Output 'Racine apres rangement :'
Get-ChildItem $RootDir | ForEach-Object { "  {0}{1}" -f $_.Name, $(if ($_.PSIsContainer) { '\' } else { '' }) }
