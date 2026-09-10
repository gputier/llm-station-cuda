param(
  [Parameter(Mandatory=$true)][string]$Label,
  [int[]]$Profondeurs = @(10, 50, 90),
  [int]$Jetons = 240000,
  [string]$Corpus = 'D:\LLM-Setup\llama.cpp',
  [string]$Uri = 'http://127.0.0.1:8080'
)

# Needle in a haystack: does the model still FIND what sits deep inside its
# window? A window the server accepts says nothing about what the model retrieves
# from it, and every launcher here announces a window to its client.
#
# The filler is real, varied source code read from a checkout on this box, not
# one text repeated. Repeating a block would let the model answer from a pattern
# instead of from memory, and would flatter the result.
#
# The needle is arbitrary on purpose: no amount of general knowledge can produce
# it, so a correct answer can only come from having read it in context.

$ErrorActionPreference = 'Stop'
$out = "D:\LLM-Setup\bench\resultats\aiguille-$Label.txt"
$aiguille = "Le code de maintenance de la station Kryolys est ARBRE-4719-VIOLET."
$attendu  = "ARBRE-4719-VIOLET"
$question = "Quel est le code de maintenance de la station Kryolys ? Reponds uniquement par le code."

# Roughly 3 characters per token on source code. Measured against /tokenize below
# rather than trusted, since the ratio moves with the tokenizer.
$cibleCar = $Jetons * 3
$sb = New-Object System.Text.StringBuilder
foreach ($f in Get-ChildItem $Corpus -Recurse -Include *.cpp,*.h,*.hpp -EA SilentlyContinue) {
  [void]$sb.AppendLine("// ---- $($f.Name) ----")
  [void]$sb.AppendLine((Get-Content $f.FullName -Raw -EA SilentlyContinue))
  if ($sb.Length -ge $cibleCar) { break }
}
if ($sb.Length -lt ($cibleCar / 2)) { throw "corpus trop court : $($sb.Length) caracteres pour $cibleCar demandes" }
# Keep printable ASCII only. A source checkout carries files that are not valid
# UTF-8, and one stray byte makes the server reject the whole request with a JSON
# parse error 500 that names a column number and not the cause.
$foin = ($sb.ToString() -replace '[^\x20-\x7E\r\n\t]', '')
$foin = $foin.Substring(0, [Math]::Min($cibleCar, $foin.Length))

$n = (Invoke-RestMethod -Uri "$Uri/tokenize" -Method Post -ContentType 'application/json' `
      -Body (@{ content = $foin } | ConvertTo-Json -Compress)).tokens.Count
Write-Output ("foin : {0} caracteres, {1} jetons mesures" -f $foin.Length, $n)

$lignes = @("=== aiguille $Label", ("date        : {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm')),
            ("foin        : {0} jetons" -f $n), ("aiguille    : {0}" -f $attendu))
$ok = 0

foreach ($p in $Profondeurs) {
  $coupe = [int]($foin.Length * $p / 100)
  # Cut on a line boundary: splitting mid-token would put the needle inside a
  # broken identifier and test the tokenizer instead of the model.
  $nl = $foin.IndexOf("`n", $coupe)
  if ($nl -lt 0) { $nl = $coupe }
  $avec = $foin.Substring(0, $nl) + "`n`n" + $aiguille + "`n`n" + $foin.Substring($nl)

  $body = @{
    messages   = @(@{ role = 'user'; content = ($avec + "`n`n" + $question) })
    max_tokens = 200; seed = 42; temperature = 0
  } | ConvertTo-Json -Depth 6 -Compress

  $t0 = Get-Date
  $r = Invoke-RestMethod -Uri "$Uri/v1/chat/completions" -Method Post `
        -ContentType 'application/json; charset=utf-8' `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 1800
  $s = [int]((Get-Date) - $t0).TotalSeconds

  # The answer may land in content or in reasoning_content: a reasoning model
  # often states the code while thinking and never repeats it. Finding it in
  # either field proves the retrieval, which is what this measures.
  $rep = "$($r.choices[0].message.content) $($r.choices[0].message.reasoning_content)"
  $trouve = $rep -match [regex]::Escape($attendu)
  if ($trouve) { $ok++ }
  $lignes += ("profondeur {0,3} % : {1} en {2} s, {3} jetons d entree" -f $p, $(if ($trouve) { 'TROUVE' } else { 'MANQUE' }), $s, $r.usage.prompt_tokens)
  Write-Output $lignes[-1]
}

$lignes += ("resultat    : {0}/{1}" -f $ok, $Profondeurs.Count)
New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
Set-Content -Path $out -Value $lignes -Encoding UTF8
Write-Output ("resultat : {0}/{1}, ecrit dans {2}" -f $ok, $Profondeurs.Count, $out)
