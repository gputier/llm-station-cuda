param(
  [string[]]$Profils = @('tiel','kat','ornith','qwen','qwenu','muse'),
  [int]$LoadTimeout = 420
)

# Runs the quality bench across several profiles in one pass: load, wait for the
# server to actually answer, bench, move on. One model at a time, which is the
# only way this card works anyway.
#
# Why re-run models that already have published figures: the script that
# produced them is gone from this box, so its extraction rules are unknown. Two
# numbers obtained by two unknown methods are not comparable, and a ranking
# built on them is decoration. Everything is re-measured with bench/banc.ps1.

$ErrorActionPreference = 'Stop'
$ctl = 'D:\LLM-Setup\llm-ctl.ps1'
$recap = @()

foreach ($p in $Profils) {
  Write-Output ("=== {0} : chargement" -f $p)
  & powershell -NoProfile -ExecutionPolicy Bypass -File $ctl -Action $p | Out-Host

  $ok = $false
  for ($i = 0; $i -lt $LoadTimeout; $i += 5) {
    Start-Sleep -Seconds 5
    try {
      $h = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/health' -TimeoutSec 4
      if ($h.status -eq 'ok') { $ok = $true; break }
    } catch { }
  }
  if (-not $ok) {
    Write-Output ("{0} : ECHEC de chargement en {1} s, passe au suivant" -f $p, $LoadTimeout)
    $recap += ("{0} : echec de chargement" -f $p)
    continue
  }

  # Which model actually answers matters more than whether one does: the port is
  # single-slot and a stale server would silently bench the previous model.
  $props = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/props' -TimeoutSec 10
  Write-Output ("{0} : servi par {1}" -f $p, $props.model_path)
  $vram = (nvidia-smi --query-gpu=memory.used --format=csv,noheader) -join ''

  & powershell -NoProfile -ExecutionPolicy Bypass -File 'D:\LLM-Setup\banc.ps1' -Label $p | Out-Host

  $res = Get-Content ("D:\LLM-Setup\banc-{0}.txt" -f $p)
  $mmlu = ($res | Where-Object { $_ -like 'mmlu*' }) -join ''
  $gsm  = ($res | Where-Object { $_ -like 'gsm8k*' }) -join ''
  $recap += ("{0} : {1} | {2} | vram {3}" -f $p, $mmlu.Trim(), $gsm.Trim(), $vram)
}

Write-Output ''
Write-Output '=== RECAPITULATIF'
$recap | ForEach-Object { Write-Output $_ }
Set-Content -Path 'D:\LLM-Setup\banc-recap.txt' -Value $recap -Encoding UTF8
