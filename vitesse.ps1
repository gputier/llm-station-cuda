param(
  [Parameter(Mandatory=$true)][string]$Label,
  [string]$PromptFile = 'D:\LLM-Setup\bench\bench-prompt.json',
  [int]$Predict = 400,
  [int]$Runs = 3,
  # Cut the prompt to this many characters, 0 for the whole file. A short run
  # (-Runs 1 -Chars 30000 -Predict 150) sorts many candidates before the full
  # bench is spent on the survivors.
  [int]$Chars = 0,
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
if ($Chars -gt 0) { $prompt = $prompt.Substring(0, [Math]::Min($Chars, $prompt.Length)) }

# Spill: what the server process holds in the shared system memory Windows hands
# out once dedicated VRAM is exhausted. nvidia-smi cannot see it. A configuration
# can load, answer and still sit there: on 2026-09-13, on the 16 GB box, a
# 1,450 MiB spill alone cut prefill to 485 tok/s. Read it before trusting a figure.
#
# READ THE END FIGURE, NOT THE START ONE, and publish the end figure. The spill
# is taken twice on purpose because it GROWS during generation: on bonsai2 with
# its drafter, 2026-09-20, 1,648 MiB at load and 4,132 MiB at the end, against
# 1,508 MiB flat with no drafter. A run reported as spilling nothing, on the
# strength of the load figure alone, had to be corrected across five files.
function Get-SpillMb {
  $inst = Get-ChildItem 'D:\LLM-Setup\instances' -Filter '*.json' -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $inst) { return -1 }
  $serverPid = (Get-Content $inst.FullName -Raw | ConvertFrom-Json).Pid
  $sum = 0
  (Get-Counter '\GPU Process Memory(*)\Shared Usage').CounterSamples |
    Where-Object { $_.InstanceName -like "pid_$($serverPid)_*" } |
    ForEach-Object { $sum += $_.CookedValue }
  return [int]($sum / 1MB)
}
$spillAvant = Get-SpillMb

# Wait for the server rather than assume it: a model that is still loading
# answers /props with 503 "Loading model", and the run dies on the first call
# instead of on a measurement.
$pret = $false
for ($w = 0; $w -lt 600; $w += 5) {
  try {
    if ((Invoke-RestMethod -Uri "$Uri/health" -TimeoutSec 4).status -eq 'ok') { $pret = $true; break }
  } catch { }
  Start-Sleep -Seconds 5
}
if (-not $pret) { throw "le serveur n a pas repondu en 600 s" }

$props = Invoke-RestMethod -Uri "$Uri/props" -TimeoutSec 15
$modele = $props.model_path

$decode = @(); $prefill = @(); $accepte = @(); $draftes = @()

for ($i = 1; $i -le $Runs; $i++) {
  # /v1/chat/completions and NOT /completion, since 2026-09-10. The raw endpoint
  # sends the prompt with no chat template, and an instruction-tuned model reads a
  # finished block of code as finished: tiel ingested 44,801 tokens and emitted an
  # end-of-sequence immediately, one token predicted, 0.0 tok/s. Nothing was wrong
  # with tiel, the protocol was asking the wrong question.
  #
  # The reply's CONTENT is ignored on purpose. Reasoning models put everything in
  # reasoning_content and leave content empty; this bench measures throughput, and
  # a token costs the same whether it lands in one field or the other.
  $body = @{
    messages    = @(@{ role = 'user'; content = ($prompt + "`n`nResume ce code en une phrase.") })
    max_tokens  = $Predict
    seed        = 42
    temperature = 0
    # Since 2026-09-15. The protocol line below always claimed the prompt cache was off, and
    # nothing turned it off: runs 2 and 3 re-read the cache, so only run 1 measured ingestion.
    # That run is also the one that pays for cold weights, which on a model whose experts are
    # memory-mapped from disk ('flash') is most of what it measures.
    cache_prompt = $false
  } | ConvertTo-Json -Depth 6 -Compress

  $r = Invoke-RestMethod -Uri "$Uri/v1/chat/completions" -Method Post `
        -ContentType 'application/json; charset=utf-8' `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 900

  $decode  += [double]$r.timings.predicted_per_second
  $prefill += [double]$r.timings.prompt_per_second
  # draft_n and draft_n_accepted, the names llama-server actually returns in its
  # timings. n_drafted and n_accepted were read here until 2026-09-13 and never
  # existed: every speculative run was reported "sans speculation".
  if ($null -ne $r.timings.draft_n_accepted) { $accepte += [int]$r.timings.draft_n_accepted }
  if ($null -ne $r.timings.draft_n)          { $draftes += [int]$r.timings.draft_n }
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
  # Median over every run now that cache_prompt is off, with run 1 alongside: the gap
  # between the two is the cost of cold weights. Figures recorded before 2026-09-15 are
  # run 1 only and compare with the second number, not the median.
  ("ingestion   : {0:N0} tok/s (mediane de {1}), premiere passe {2:N0}" -f (Mediane $prefill), $Runs, $prefill[0]),
  ("speculation : {0}" -f $tauxTxt),
  ("vram        : {0}" -f $vram),
  ("debordement : {0} Mio au depart, {1} Mio a la fin" -f $spillAvant, (Get-SpillMb)),
  ("protocole   : {0} jetons generes, graine 42, temperature 0, cache d invite desactive" -f $Predict)
)
New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
Set-Content -Path $out -Value $lignes -Encoding UTF8
$lignes | ForEach-Object { Write-Output $_ }
