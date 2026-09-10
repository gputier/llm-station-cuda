param(
  [Parameter(Mandatory=$true)][string]$Label,
  [int]$MmluCount = 500,
  [int]$Gsm8kCount = 60,
  [string]$Epreuves = 'D:\LLM-Setup\epreuves.jsonl',
  [string]$Uri = 'http://127.0.0.1:8080/v1/chat/completions'
)

# Quality bench: MMLU multiple choice plus GSM8K word problems, temperature 0,
# fixed seed, the same set for every model. Rewritten on 2026-09-10 because the
# original script was gone from the box while epreuves.jsonl survived.
#
# Temperature 0 HERE and nowhere else. The bench wants a reproducible number, so
# it takes greedy decoding and the repetition risk that comes with it. A serving
# profile never does: Qwen documents that greedy decoding on these weights
# degrades quality and produces endless repetitions.
#
# Thinking is disabled through chat_template_kwargs. Without it a reasoning
# model spends its whole output budget in reasoning_content and returns an empty
# answer, which scores as a wrong answer and measures nothing.

$ErrorActionPreference = 'Stop'
$out = "D:\LLM-Setup\banc-$Label.txt"

$all = Get-Content $Epreuves | ForEach-Object { ConvertFrom-Json $_ }
$mmlu  = @($all | Where-Object { $_.kind -eq 'mmlu'  } | Select-Object -First $MmluCount)
$gsm   = @($all | Where-Object { $_.kind -eq 'gsm8k' } | Select-Object -First $Gsm8kCount)

function Ask($prompt, $maxTokens) {
  $body = @{
    messages    = @(@{ role = 'user'; content = $prompt })
    max_tokens  = $maxTokens
    seed        = 42
    temperature = 0
    chat_template_kwargs = @{ enable_thinking = $false }
  } | ConvertTo-Json -Depth 6 -Compress
  $r = Invoke-RestMethod -Uri $Uri -Method Post `
        -ContentType 'application/json; charset=utf-8' `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 600
  return $r.choices[0].message.content
}

$letters = @('A','B','C','D')
$t0 = Get-Date
$mmluOk = 0; $mmluDone = 0; $mmluEmpty = 0

foreach ($q in $mmlu) {
  $opts = ($q.choices | ForEach-Object -Begin { $i = 0 } -Process {
    "{0}. {1}" -f $letters[$i], $_; $i++
  }) -join "`n"
  $prompt = "{0}`n{1}`n`nAnswer with the single letter A, B, C or D. Nothing else." -f $q.q, $opts

  # 8 tokens: the answer is one letter. A model that needs more is not answering
  # the question that was asked, and truncating it is the correct verdict.
  $a = (Ask $prompt 8)
  $mmluDone++
  if (-not $a) { $mmluEmpty++; continue }
  # First standalone letter anywhere in the reply: models prefix with "Answer:"
  # or wrap in markdown often enough that a strict equality throws away correct
  # answers and measures formatting instead of knowledge.
  $m = [regex]::Match($a.Trim().ToUpper(), '\b([ABCD])\b')
  if ($m.Success -and $letters[[int]$q.answer] -eq $m.Groups[1].Value) { $mmluOk++ }
  if ($mmluDone % 50 -eq 0) {
    Write-Output ("mmlu {0}/{1} : {2} justes" -f $mmluDone, $mmlu.Count, $mmluOk)
  }
}

$gsmOk = 0; $gsmDone = 0; $gsmEmpty = 0
foreach ($q in $gsm) {
  $prompt = "{0}`n`nReason briefly, then end your reply with the final number alone on its own last line." -f $q.q
  $a = (Ask $prompt 512)
  $gsmDone++
  if (-not $a) { $gsmEmpty++; continue }
  # Last number in the reply, commas and currency stripped. GSM8K answers are
  # integers, and the last number is the answer by construction of the prompt.
  $nums = [regex]::Matches(($a -replace '[,$]',''), '-?\d+(?:\.\d+)?')
  if ($nums.Count -gt 0) {
    $got = $nums[$nums.Count - 1].Value
    if ([double]$got -eq [double]$q.answer) { $gsmOk++ }
  }
  if ($gsmDone % 20 -eq 0) {
    Write-Output ("gsm8k {0}/{1} : {2} justes" -f $gsmDone, $gsm.Count, $gsmOk)
  }
}

$mins = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)
$pct  = if ($mmlu.Count) { [math]::Round(100.0 * $mmluOk / $mmlu.Count, 1) } else { 0 }

$lignes = @(
  "=== $Label",
  ("date        : {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm')),
  ("mmlu        : {0}/{1} soit {2} %" -f $mmluOk, $mmlu.Count, $pct),
  ("gsm8k       : {0}/{1}" -f $gsmOk, $gsm.Count),
  ("vides       : {0} mmlu, {1} gsm8k" -f $mmluEmpty, $gsmEmpty),
  ("duree       : {0} min" -f $mins),
  "temperature 0, seed 42, thinking desactive"
)
Set-Content -Path $out -Value $lignes -Encoding UTF8
$lignes | ForEach-Object { Write-Output $_ }
Write-Output "ecrit dans $out"
