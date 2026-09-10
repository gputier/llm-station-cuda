param(
  [string]$Profils = 'tiel,kat,nex,spark',
  [string]$Temperatures = '0.3,0.6,1.0',
  [string]$TopKs = '0,40,64',
  [double]$TopKAt = 0.6,
  [int]$LoadTimeout = 420
)

# Sweeps sampling settings on models that are already installed, to replace an
# argument with a measurement. The question that started it, on 2026-09-10: does
# temperature 0.3 on tiel actually buy precision, or does it only look like it?
#
# ONE FACTOR AT A TIME, and that is the whole design. Temperatures are swept at
# the top-k every profile already runs, 20; then top-k is swept at -TopKAt. A
# full grid would multiply the runs and tell you less, because a pair that scores
# well never says which half earned it.
#
# Temperature 0 is NOT swept here: the campaign of 2026-09-10 already measured
# every one of these models at 0, and re-running it would burn an hour to
# reproduce numbers that are already on disk.
#
# Temperature and top-k are REQUEST parameters, not load parameters. Each model
# is therefore loaded ONCE and answers every combination, which is what makes
# this affordable at all.

$ErrorActionPreference = 'Stop'
$ctl   = 'D:\LLM-Setup\llm-ctl.ps1'
$banc  = 'D:\LLM-Setup\bench\banc.ps1'
$liste = $Profils -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
$temps = $Temperatures -split ',' | ForEach-Object { [double]$_.Trim() }
# $ks and NOT $topks: PowerShell is case-insensitive, so $topks IS the $TopKs
# parameter, declared [string]. Assigning an array back into it converts it
# straight to "0 40 64", and the whole sweep is passed as one argument. A local
# name that only differs in case from a typed parameter is a silent bug.
$ks = $TopKs -split ',' | ForEach-Object { [int]$_.Trim() }
$recap = @()

# Numbers crossing a command line MUST be formatted in the invariant culture. On a
# French Windows, 0.6 renders as "0,6", and PowerShell reads a comma as an array
# separator: -Temperature 0,6 arrives as two values, and every argument after it
# shifts. That is what killed the top-k sweep on its first run.
function Inv($n) { return ([double]$n).ToString([System.Globalization.CultureInfo]::InvariantCulture) }
# Labels strip BOTH separators, since which one appears depends on the culture.
function Lab($n) { return (Inv $n) -replace '[.,]','' }

foreach ($p in $liste) {
  Write-Output ("=== {0} : chargement" -f $p)
  & powershell -NoProfile -ExecutionPolicy Bypass -File $ctl -Action $p | Out-Host

  $ok = $false
  for ($i = 0; $i -lt $LoadTimeout; $i += 5) {
    Start-Sleep -Seconds 5
    try {
      if ((Invoke-RestMethod -Uri 'http://127.0.0.1:8080/health' -TimeoutSec 4).status -eq 'ok') { $ok = $true; break }
    } catch { }
  }
  if (-not $ok) { $recap += ("{0} : echec de chargement" -f $p); continue }

  foreach ($t in $temps) {
    $lab = "{0}-t{1}-k20" -f $p, (Lab $t)
    Write-Output ("--- {0}" -f $lab)
    # Skip what is already on disk: a sweep that dies halfway must be restartable
    # without paying again for the hours it already spent.
    if (-not (Test-Path ("D:\LLM-Setup\bench\resultats\banc-{0}.txt" -f $lab))) {
      & powershell -NoProfile -ExecutionPolicy Bypass -File $banc -Label $lab -Temperature (Inv $t) -TopK 20 | Out-Host
    } else { Write-Output 'deja mesure, passe' }
    $r = Get-Content ("D:\LLM-Setup\bench\resultats\banc-{0}.txt" -f $lab)
    $recap += ("{0} : {1} | {2}" -f $lab, (($r | Where-Object { $_ -like 'mmlu*' }) -join '').Trim(), (($r | Where-Object { $_ -like 'gsm8k*' }) -join '').Trim())
  }

  foreach ($k in $ks) {
    $lab = "{0}-t{1}-k{2}" -f $p, (Lab $TopKAt), $k
    Write-Output ("--- {0}" -f $lab)
    if (-not (Test-Path ("D:\LLM-Setup\bench\resultats\banc-{0}.txt" -f $lab))) {
      & powershell -NoProfile -ExecutionPolicy Bypass -File $banc -Label $lab -Temperature (Inv $TopKAt) -TopK $k | Out-Host
    } else { Write-Output 'deja mesure, passe' }
    $r = Get-Content ("D:\LLM-Setup\bench\resultats\banc-{0}.txt" -f $lab)
    $recap += ("{0} : {1} | {2}" -f $lab, (($r | Where-Object { $_ -like 'mmlu*' }) -join '').Trim(), (($r | Where-Object { $_ -like 'gsm8k*' }) -join '').Trim())
  }
}

Write-Output ''
Write-Output '=== RECAPITULATIF SAMPLING'
$recap | ForEach-Object { Write-Output $_ }
Set-Content -Path 'D:\LLM-Setup\bench\resultats\banc-sampling-recap.txt' -Value $recap -Encoding UTF8
