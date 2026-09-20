param(
  [Parameter(Mandatory=$true)][string]$Exe,
  [Parameter(Mandatory=$true)][string]$Label,
  [switch]$NoDraft,
  # Which drafter to ask for. The default is the one published for this target;
  # the validation on pull request 210 used the stock z-lab file instead, and the
  # two do not behave the same way, which is the whole point of passing it here.
  [string]$Draft = 'Bonsai-2-27B-DFlash2-Q8_0.gguf'
)
# Reproduces the server settings and the three prompts of the independent
# validation posted on PrismML pull request 210, so this box's figures can be
# put next to the ones published there. Deliberately NOT the bonsai2 profile:
# the point is comparability with that comment, not production settings. For the
# profile's own numbers use vitesse.ps1 against a running llm-ctl instance.
#
# Written 2026-09-20 to answer one question: does that pull request help here.
# It does not, and this script is what showed why. See docs/tuning-log.md.
$ErrorActionPreference = 'Stop'
$ModelsDir = 'D:\models'
$RootDir   = 'D:\LLM-Setup'
$cudaBin   = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.3\bin'
$workDir   = Split-Path $Exe -Parent

$modelArgs = @(
  '-m',"$ModelsDir\ternary-bonsai-2-27b\Ternary-Bonsai-2-27B-PQ2_0.gguf",
  '-c','32768','-ngl','99','-fa','on','-np','1',
  '-ctk','f16','-ctv','f16','--no-mmproj',
  '--chat-template-file',"$ModelsDir\ternary-bonsai-2-27b\chat-template-system-anywhere.jinja",
  '--host','127.0.0.1','--port','8080','--jinja'
)
if (-not $NoDraft) {
  $modelArgs += @(
    '--spec-type','draft-dflash',
    '--spec-draft-model',"$ModelsDir\ternary-bonsai-2-27b\$Draft",
    '-ngld','99','--spec-draft-n-max','4','-ctkd','f16','-ctvd','f16'
  )
}

Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  Where-Object { (Get-Process -Id $_ -ErrorAction SilentlyContinue).ProcessName -eq 'llama-server' } |
  ForEach-Object { Stop-Process -Id $_ -Force }
Start-Sleep -Seconds 5

$errLog = "$RootDir\logs\llm-err-proto-$Label.log"
Remove-Item $errLog -ErrorAction SilentlyContinue
$savedPath = $env:PATH
$env:PATH = "$cudaBin;$env:PATH"
$p = Start-Process -FilePath $Exe -ArgumentList $modelArgs -WorkingDirectory $workDir `
       -RedirectStandardError $errLog -RedirectStandardOutput "$RootDir\logs\llm-out-proto-$Label.log" `
       -PassThru -WindowStyle Hidden
$env:PATH = $savedPath
Write-Output "STARTED pid=$($p.Id) label=$Label"

$pret = $false
for ($w = 0; $w -lt 600; $w += 5) {
  if ($p.HasExited) { Write-Output "DIED code=$($p.ExitCode)"; break }
  try { if ((Invoke-RestMethod -Uri 'http://127.0.0.1:8080/health' -TimeoutSec 4).status -eq 'ok') { $pret = $true; Write-Output "HEALTH_OK apres ${w}s"; break } } catch {}
  Start-Sleep -Seconds 5
}
if (-not $pret) { Get-Content $errLog -Tail 30 | ForEach-Object { $_ }; exit 1 }

$prompts = @(
  @{ nom = 'arithmetique'; texte = 'A bat and a ball cost $1.10 together. The bat costs $1.00 more than the ball. I also buy 7 more balls at that same price. How much change from $5 for everything? Explain briefly.' },
  @{ nom = 'code';         texte = 'Write a Python function merge_intervals(intervals) for closed intervals, sorted output, merging touching endpoints. Include four assert tests for empty, nested, overlapping, and touching intervals. Code only.' },
  @{ nom = 'prose';        texte = 'Explain how a rainbow forms in exactly three sentences for a curious child.' }
)

$resultats = @()
foreach ($pr in $prompts) {
  $corps = @{
    messages     = @(@{ role = 'user'; content = $pr.texte })
    temperature  = 0
    top_k        = 1
    seed         = 42
    max_tokens   = 1024
    cache_prompt = $false
    chat_template_kwargs = @{ enable_thinking = $false }
  } | ConvertTo-Json -Depth 6
  $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/v1/chat/completions' -Method Post `
         -ContentType 'application/json' -Body $corps -TimeoutSec 900
  $t = $r.timings
  $acc = if ($null -ne $t.draft_n_accepted) { $t.draft_n_accepted } else { 0 }
  $drf = if ($null -ne $t.draft_n)          { $t.draft_n }          else { 0 }
  $taux = if ($drf -gt 0) { '{0:N1}' -f (100.0 * $acc / $drf) } else { 'n/a' }
  Write-Output ("{0} : decode {1:N1} tok/s, prefill {2:N1} tok/s, acceptation {3}/{4} ({5} %), fin {6}" -f `
    $pr.nom, $t.predicted_per_second, $t.prompt_per_second, $acc, $drf, $taux, $r.choices[0].finish_reason)
  $resultats += [pscustomobject]@{
    prompt = $pr.nom; decode = $t.predicted_per_second; prefill = $t.prompt_per_second
    accepte = $acc; draftes = $drf; texte = $r.choices[0].message.content
  }
}

$dest = "$RootDir\bench\resultats\pr210-proto-$Label.json"
$resultats | ConvertTo-Json -Depth 5 | Set-Content $dest -Encoding UTF8
Write-Output "SORTIES $dest"
Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
Write-Output "DONE $Label"
