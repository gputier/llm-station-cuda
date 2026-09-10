param(
  [Parameter(Mandatory=$true)][string]$Label,
  [string]$PromptFile = 'D:\LLM-Setup\bench\bench-prompt.json',
  [int]$Predict = 400,
  [int]$Runs = 3,
  [string]$Uri = 'http://127.0.0.1:8080'
)

# Speed bench: one fixed long prompt, one fixed seed, N runs, median reported.
# Written on 2026-09-10 to compare speculation settings on the same model, which
# the quality bench cannot do: MMLU says nothing about tokens per second.
#
# The median and not the mean, and three runs and not one, because the first run
# after a load pays for a cold cache and would flatter or damn a setting at
# random.
#
# WHAT DECIDES: predicted_per_second, the decode rate. NOT the acceptance rate.
# Measured on tiel on 2026-09-03, raising acceptance from 27% to 66% HALVED
# throughput: a drafter that is right more often can still be too slow to be
# worth asking. Acceptance is reported here as context, never as the verdict.

$ErrorActionPreference = 'Stop'
$out = "D:\LLM-Setup\bench\resultats\vitesse-$Label.txt"

if (-not (Test-Path $PromptFile)) { throw "invite introuvable : $PromptFile" }
$prompt = (Get-Content $PromptFile -Raw | ConvertFrom-Json).prompt
if (-not $prompt) { throw "le fichier d invite ne porte pas de champ 'prompt'" }

$props = Invoke-RestMethod -Uri "$Uri/props" -TimeoutSec 15
$modele = $props.model_path

$decode = @(); $prefill = @(); $accepte = @(); $draftes = @()

for ($i = 1; $i -le $Runs; $i++) {
  $body = @{
    prompt      = $prompt
    n_predict   = $Predict
    seed        = 42
    temperature = 0
    cache_prompt = $false   # each run must pay its own prefill, or runs 2 and 3 measure nothing
  } | ConvertTo-Json -Depth 4 -Compress

  $r = Invoke-RestMethod -Uri "$Uri/completion" -Method Post `
        -ContentType 'application/json; charset=utf-8' `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 900

  $decode  += [double]$r.timings.predicted_per_second
  $prefill += [double]$r.timings.prompt_per_second
  if ($null -ne $r.timings.n_accepted)  { $accepte += [int]$r.timings.n_accepted }
  if ($null -ne $r.timings.n_drafted)   { $draftes += [int]$r.timings.n_drafted }
  Write-Output ("passe {0}/{1} : {2:N1} tok/s en generation, {3:N0} en ingestion" -f $i, $Runs, $decode[-1], $prefill[-1])
}

function Mediane($a) { $s = @($a | Sort-Object); return $s[[int]([math]::Floor($s.Count / 2))] }

$vram = (nvidia-smi --query-gpu=memory.used --format=csv,noheader) -join ''
$tauxTxt = 'sans speculation'
if ($draftes.Count -gt 0 -and ($draftes | Measure-Object -Sum).Sum -gt 0) {
  $a = ($accepte | Measure-Object -Sum).Sum
  $d = ($draftes | Measure-Object -Sum).Sum
  $tauxTxt = "{0}/{1} soit {2:N1} % d acceptation" -f $a, $d, (100.0 * $a / $d)
}

$lignes = @(
  "=== $Label",
  ("date        : {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm')),
  ("modele      : {0}" -f $modele),
  ("generation  : {0:N1} tok/s (mediane de {1})" -f (Mediane $decode), $Runs),
  ("ingestion   : {0:N0} tok/s (mediane de {1})" -f (Mediane $prefill), $Runs),
  ("speculation : {0}" -f $tauxTxt),
  ("vram        : {0}" -f $vram),
  ("protocole   : {0} jetons generes, graine 42, temperature 0, cache d invite desactive" -f $Predict)
)
New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
Set-Content -Path $out -Value $lignes -Encoding UTF8
$lignes | ForEach-Object { Write-Output $_ }
