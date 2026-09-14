param(
  [ValidateSet('tiel','qwen36','stop','status','logs')]
  [string]$Action,
  [string]$Name,   # optional: for 'stop' and 'logs', targets a named instance
  [int]$Tail = 40, # for 'logs': history lines to show before following live
  # Extra llama-server flags appended to the profile, for a trial that should not
  # become a commit. Space separated, quoted as one string so it survives ssh.
  # They are appended LAST, so they win over the profile on any repeated flag.
  [string]$Extra = '',
  # Strips the profile's own speculation flags before -Extra is appended, which
  # -Extra alone cannot do: --spec-type accumulates rather than replaces, so
  # asking for another type on a profile that already has one runs BOTH. That
  # cost 36% of tiel's decode on the 5090 box on 2026-09-10.
  [switch]$NoSpec
)

# ---------------------------------------------------------------------------
# PC-GUILLAUME-3, RTX 4080 SUPER, 16376 MiB of which 15061 are free to a model.
# Adapted from the 5090 box's llm-ctl.ps1; the control functions are copied
# unchanged, the profiles are this machine's own.
#
# ONE build here: BeeLlama v0.4.6, a llama.cpp fork, from its official Windows
# CUDA 13.3 release zip plus cudart, unpacked flat, no compilation. It replaced
# the upstream b10908 binary on 2026-09-13 for one reason, the KVarN cache types:
# upstream release binaries build flash attention for q4_0 and q8_0 only
# (GGML_CUDA_FA_ALL_QUANTS is OFF upstream), and at q4_0 the full 262,144 window
# of Qwen3.8-27B overflowed this card. Measured figures in the card recipe below.
#
# Verified by execution on 2026-09-13, driver 616.92:
#   llama-server.exe --list-devices
#   CUDA0: NVIDIA GeForce RTX 4080 SUPER (16375 MiB, 14839 MiB free)
# ---------------------------------------------------------------------------
$RootDir   = 'D:\LLM-Setup'
$ModelsDir = 'D:\models'

$instDir    = "$RootDir\instances"
$logDir     = "$RootDir\logs"
$serverPort = 8080

New-Item -ItemType Directory -Force -Path $instDir, $logDir | Out-Null

# Which build serves which profile, and nothing else: a model's own settings
# live in its switch branch.
$builds = @{
  tiel   = @{ Exe = "$RootDir\beellama-v0.4.6\llama-server.exe"; WorkDir = "$RootDir\beellama-v0.4.6" }
  qwen36 = @{ Exe = "$RootDir\beellama-v0.4.6\llama-server.exe"; WorkDir = "$RootDir\beellama-v0.4.6" }
}

function Quote($s) {
  if ($s -match '[\s"]') { return '"' + ($s -replace '"','\"') + '"' }
  return $s
}

function Read-Instances {
  Get-ChildItem $instDir -Filter '*.json' -ErrorAction SilentlyContinue | ForEach-Object {
    $o = Get-Content $_.FullName -Raw | ConvertFrom-Json
    [pscustomobject]@{ Name = $_.BaseName; Pid = $o.Pid; Port = $o.Port }
  }
}

function Kill-Pid($procId) {
  $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
  if ($p) { $p | Stop-Process -Force }
}

# Wait for the card to actually hand its memory back. Stop-Process returns as
# soon as the process is marked dead, but Windows frees device memory
# ASYNCHRONOUSLY, and an instance relaunched before that completes sees a card
# that is still occupied. A fixed delay is either too short or wasted time.
function Get-VramUsedMb {
  try {
    $raw = & nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>$null
  } catch {
    return -1
  }
  if (-not $raw) { return -1 }
  $parsed = 0
  if (-not [int]::TryParse((@($raw)[0]).ToString().Trim(), [ref]$parsed)) { return -1 }
  return $parsed
}

function Wait-VramReleased($timeoutSec = 30) {
  $deadline = (Get-Date).AddSeconds($timeoutSec)

  while ((Get-Date) -lt $deadline -and (Get-Process -Name llama-server -ErrorAction SilentlyContinue)) {
    Start-Sleep -Milliseconds 250
  }

  Start-Sleep -Milliseconds 500
  $previous = -1
  $stable   = 0
  while ((Get-Date) -lt $deadline) {
    $used = Get-VramUsedMb
    # nvidia-smi unavailable: fall back to a fixed delay rather than burning the
    # whole timeout. Control must not depend on that one tool.
    if ($used -lt 0) { Start-Sleep -Seconds 2; return }
    if ($used -eq $previous) { $stable++ } else { $stable = 0 }
    if ($stable -ge 2) { return }
    $previous = $used
    Start-Sleep -Milliseconds 500
  }
}

function Stop-One($name) {
  $f = Join-Path $instDir "$name.json"
  if (Test-Path $f) {
    $o = Get-Content $f -Raw | ConvertFrom-Json
    Kill-Pid $o.Pid
    Remove-Item $f -Force
    Write-Output "STOPPED $name"
  } else {
    Write-Output "NOT_RUNNING $name"
  }
}

function Stop-All {
  $procs = Get-Process -Name llama-server -ErrorAction SilentlyContinue
  if ($procs) { $procs | Stop-Process -Force; Wait-VramReleased; Write-Output "STOPPED all" }
  else { Write-Output "NOT_RUNNING" }
  Get-ChildItem $instDir -Filter '*.json' -ErrorAction SilentlyContinue | Remove-Item -Force
}

# llama-server writes ALL of its output to stderr, progress lines and served
# requests included, so llm-err-<name>.log carries everything and standard
# output goes to NUL. This action picks the log of the running instance and
# follows it. Ctrl+C to exit; the server is unaffected.
function Show-Logs($name, $tail) {
  if (-not $name) {
    $running = @(Read-Instances | Where-Object { Get-Process -Id $_.Pid -ErrorAction SilentlyContinue })
    if ($running.Count -eq 0) {
      $noms = ($builds.Keys | Sort-Object) -join '/'
      Write-Output "NO_INSTANCE no tracked instance is running. Pass -Name ($noms)."
      return
    }
    $name = $running[0].Name
  }
  $errLog = "$logDir\llm-err-$name.log"
  if (-not (Test-Path $errLog)) { Write-Output "NO_LOG $errLog not found"; return }
  Write-Output "TAILING name=$name file=$errLog (Ctrl+C to exit)"
  Get-Content $errLog -Tail $tail -Wait
}

function Start-LLM($name, $modelArgs, $exePath = $null, $workDirPath = $null, $envVars = @{}) {
  # -NoSpec first, so a trial can REPLACE a profile's speculation instead of
  # stacking on top of it.
  if ($NoSpec) {
    $garde = @(); $saut = $false
    foreach ($a in @($modelArgs)) {
      if ($saut) { $saut = $false; continue }
      if ($a -in @('--spec-type','--spec-draft-n-max','--spec-draft-p-min','-md','--spec-draft-model')) { $saut = $true; continue }
      $garde += $a
    }
    $modelArgs = $garde
    Write-Output 'NOSPEC profile speculation flags removed'
  }
  if ($Extra) {
    $sup = @($Extra -split '\s+' | Where-Object { $_ })
    $modelArgs = @($modelArgs) + $sup
    Write-Output ("EXTRA " + ($sup -join ' '))
  }
  # This box has 32 GB of host RAM, not 128 like the 5090 box its profiles are
  # copied from, and mlock is refused here as a constraint of the machine, not
  # of a profile. With the flag on, the locked weights and the prompt cache did
  # not both fit: 29 hours of service read on 2026-09-14 gave 29,955 MiB
  # private for 11,792 resident, a page file peak of 9,965 MiB and 350
  # prompt-cache evictions (docs/tuning-log.md, entry of that day). Without it
  # the loader unmaps every fragment once it sits on the card, and the RAM goes
  # to the cache: the same model restarted without the flag held 18,787 MiB
  # private. A profile copied from llm-ctl.ps1 would bring the flag back
  # silently, which is why it is checked here and not left to each branch.
  for ($i = 0; $i -lt @($modelArgs).Count - 1; $i++) {
    if ($modelArgs[$i] -eq '--load-mode' -and $modelArgs[$i + 1] -eq 'mlock') {
      Write-Output 'REFUSED --load-mode mlock: 32 GB of host RAM on this box, see docs/tuning-log.md 2026-09-14'
      return
    }
  }
  $b = $builds[$name]
  if (-not $exePath)     { $exePath     = $b.Exe }
  if (-not $workDirPath) { $workDirPath = $b.WorkDir }
  $port = $serverPort

  # Free the port: kill any tracked instance on it.
  $killed = @()
  foreach ($i in Read-Instances) {
    if ($i.Port -eq $port) { Kill-Pid $i.Pid; $killed += $i.Pid; Remove-Item (Join-Path $instDir "$($i.Name).json") -Force -ErrorAction SilentlyContinue }
  }
  # Then any ORPHAN still listening on that port. One started by hand, or one
  # that survived a loss of tracking, would block the bind silently while this
  # script reported STARTED and the health check went green against the old
  # process.
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    Where-Object { $killed -notcontains $_ -and (Get-Process -Id $_ -ErrorAction SilentlyContinue).ProcessName -eq 'llama-server' } |
    ForEach-Object { Write-Output "KILLED_ORPHAN pid=$_ port=$port"; Kill-Pid $_ }
  Wait-VramReleased

  $errLog = "$logDir\llm-err-$name.log"
  Clear-Content $errLog -ErrorAction SilentlyContinue

  $quoted = ($modelArgs | ForEach-Object { Quote $_ }) -join ' '
  # No `set` inside the cmd line. cmd /c strips the outer quotes of the whole
  # line, after which `set PATH=<value> && <rest>` swallows ` && <rest>` INTO
  # the value: nothing after it runs, no log file is created, and
  # Win32_Process.Create still returns 0. `cd /d` is still required.
  #
  # The profile's environment goes through Win32_ProcessStartup. Setting it on
  # THIS process before the call does NOT reach the child: WMI starts it from its
  # own environment. Measured on this box on 2026-09-13 with `cmd /c set`: the
  # caller-side variable came out "not defined", the startup-block one came out
  # set. That block REPLACES the child's environment rather than extending it, so
  # the current one is copied and the profile's variables are laid over it.
  $inner = "cd /d `"$workDirPath`" && `"$exePath`" $quoted > NUL 2> `"$errLog`""
  $createArgs = @{ CommandLine = "cmd.exe /c $inner"; CurrentDirectory = $workDirPath }
  if ($envVars.Count -gt 0) {
    $vars = @(Get-ChildItem Env: | Where-Object { -not $envVars.ContainsKey($_.Name) } | ForEach-Object { "$($_.Name)=$($_.Value)" })
    foreach ($k in $envVars.Keys) { $vars += "$k=$($envVars[$k])"; Write-Output "ENV $k=$($envVars[$k])" }
    $createArgs.ProcessStartupInformation = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ EnvironmentVariables = [string[]]$vars }
  }
  $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments $createArgs
  if ($r.ReturnValue -ne 0) { Write-Output "ERROR Win32_Process.Create rc=$($r.ReturnValue)"; return }

  # Resolve the real llama-server PID (child of the cmd.exe we launched).
  $llamaPid = $null
  for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 300
    $child = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($r.ProcessId) AND Name='llama-server.exe'" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($child) { $llamaPid = $child.ProcessId; break }
  }
  if ($llamaPid) {
    @{ Pid = $llamaPid; Port = $port } | ConvertTo-Json -Compress | Set-Content -Path (Join-Path $instDir "$name.json")
    Write-Output "STARTED name=$name pid=$llamaPid port=$port"
  } else {
    # No STARTED here: without a PID the instance is untracked and the process
    # almost certainly failed at startup. Reporting success hides the failure.
    Write-Output "FAILED name=$name port=$port (no llama-server started, see llm-err-$name.log)"
    Get-Content $errLog -Tail 5 -ErrorAction SilentlyContinue
  }
}

function Get-Status {
  $any = $false
  foreach ($i in Read-Instances) {
    $any = $true
    $alive = [bool](Get-Process -Id $i.Pid -ErrorAction SilentlyContinue)
    # /health answers ok whatever model is loaded, and answers ok before the
    # model is servable. model_path from /props is what says WHO holds the port.
    $model = 'no answer'
    try {
      $p = Invoke-RestMethod -Uri "http://localhost:$($i.Port)/props" -TimeoutSec 3
      $model = Split-Path $p.model_path -Leaf
    } catch { $model = 'no answer' }
    Write-Output "RUNNING name=$($i.Name) pid=$($i.Pid) port=$($i.Port) alive=$alive model=$model"
  }
  if (-not $any) { Write-Output "NOT_RUNNING" }
}

# ---------------------------------------------------------------------------
# Profiles.
#
# Sampling values are the ones each publisher asks for, read from its model card
# or generation_config.json, NOT copied from a neighbouring profile. Copying
# Qwen's shape onto nex and spark produced wrong and credible numbers on the
# 5090 box on 2026-09-10.
# ---------------------------------------------------------------------------

# The card recipe, shared by every profile: what 16 GB and BeeLlama impose
# whatever the model. Each profile adds its weights, its template if it needs
# one, and its sampling. Everything here was measured on Qwen3.8-27B, the model
# this box served from 2026-09-13 to 2026-09-14, at the full 262,144 window
# (bench/vitesse.ps1, 6,018-token prompt, then 45k). Both A3B profiles run on it
# unchanged, and their first reading held, figures below.
#
#   UD-IQ4_XS, cache in host RAM (--no-kv-offload) ... decode 14.5 tok/s short,
#                                                       4.85 at 45k: dead end
#   UD-IQ4_XS, KVarN 2 on the card ................... prefill 61, decode 15
#   UD-IQ3_XXS, q4_0, official b10908 ................ prefill 485, decode 48
#   UD-IQ3_XXS, KVarN 4, BeeLlama .................... prefill 1,581, decode 46
#   UD-IQ3_XXS, KVarN 3, BeeLlama .................... prefill 1,572, decode 46,
#                                                       14,378 MiB
#   UD-IQ3_XXS, KVarN 3 + MTP n-max 2 ................ prefill 1,410, decode 63.2
#   UD-IQ3_XXS, KVarN 3 + MTP n-max 3 ................ prefill 1,459, decode 61.9
#   UD-IQ3_XXS, KVarN 4 + MTP n-max 2 ................ prefill 133, decode 26.7
#
# q4_0 loses to KVarN on prefill by a factor of three at equal decode: the
# official build overflows into the shared system memory Windows hands out once
# dedicated VRAM runs dry, which nvidia-smi does not show. KVarN 4 with MTP
# overflows the same way. KVarN 3 with the MTP head is the one setting that
# holds the full window, speculates, and stays on the card.
#
# No --load-mode here, on purpose: mlock is refused by Start-LLM, see there.
# ---------------------------------------------------------------------------
$cardRecipe = @(
  '--n-gpu-layers','99','--flash-attn','on','--jinja',
  # MTP head inside the GGUF, drafting two tokens. Its own cache follows the
  # target window, so it is kept in KVarN 3 too: left at the f16 default it
  # spilled 3,820 MiB into shared memory and prefill fell to 37 tok/s.
  '--spec-type','draft-mtp','--spec-draft-n-max','2',
  '--spec-draft-type-k','kvarn3','--spec-draft-type-v','kvarn3',
  '--host','0.0.0.0','--port','8080','--parallel','1','--ctx-size','262144',
  # -ub 512, not 2048: the larger physical batch pushed KVarN 4 into shared
  # memory (2,222 MiB spilled, prefill 83 tok/s) and 1024 already cost 278 MiB
  # more spill on KVarN 3 for no gain in prefill.
  '-b','512','-ub','512',
  '--cache-type-k','kvarn3','--cache-type-v','kvarn3',
  # 12288 and not the 5090 box's 24576: 32 GB of host RAM. Removing mlock handed
  # close to ten gigabytes back, but a larger cache was not measured before the
  # campaign of 2026-09-14 closed, so the value stays where it was proven.
  '-cram','12288'
)
# GGML_KVARN_WINDOW_CHUNK: BeeLlama's KVarN prefill materialises transient F16
# K/V windows of this many tokens, 65,536 by default. At 16 x 4 heads x 256 x 2
# x 2 bytes, one such window is about 800 MiB, and on 2026-09-13 a 240k-token
# prompt spilled 1,244 MiB into shared memory and read at 288 tok/s once past
# 76k tokens. At 16384, 243,053 tokens read in 278 to 371 s, needle 3/3.
$cardEnv = @{ GGML_KVARN_WINDOW_CHUNK = '16384' }

switch ($Action) {

  # ---------------------------------------------------------------------------
  # Two 35B-A3B profiles since 2026-09-14. They replaced the dense Qwen3.8-27B,
  # whose profile is in the git history, because the 5090 box had made the same
  # move on 2026-09-01 for a structural reason that transposes: about 3B of the
  # 35B parameters work per token, so far fewer bytes are re-read per token, and
  # the attention cache is 3.2x smaller per token (10 full-attention layers x 2
  # KV heads x 256 x 2, against 16 x 4 x 256 x 2, GGUF headers read 2026-09-14).
  #
  # Measured that day on the card recipe: bench/vitesse.ps1, 6,018-token prompt,
  # decode median of 3; then bench/banc.ps1, 500 MMLU and 60 GSM8K, temperature 0.
  #
  #   profile        decode       prefill      VRAM         spill     MMLU    GSM8K
  #   27B, retired   72.1 tok/s   1,394 tok/s  15,851 MiB   650 MiB
  #   tiel           139.3        2,893        15,413       620       85.8%   55/60
  #   qwen36         137.1        2,684        15,861       620       90.2%   56/60
  #
  # Both fit the full window with no --n-cpu-moe. Neither wins on everything:
  # level on speed, a 4.4-point MMLU gap under the 4.5 needed to separate two
  # models on 500 questions, and qwen36 needed 27.2 minutes of bench against 10.9.
  # Both stay, and the launchers ask which one. No vision projector is loaded:
  # this box does text.
  # ---------------------------------------------------------------------------

  'tiel' {
    # Tiel-Coder-35B-A3B MTP, unsloth-style UD-IQ3_XXS (13.6 GB), the 16 GB tier
    # the publisher names. Same weights family as the 5090 box's tiel profile,
    # which is the coding model there: 85.8% MMLU here against 86.6% for the
    # UD-Q4_K_XL build on that box on 2026-09-10, so the 3-bit tier costs no
    # measurable quality. The MTP head is inside the GGUF (41 blocks,
    # blk.40.nextn, read in the header on 2026-09-14); the publisher verified it
    # trained (kurtosis 25.1 against 3.0 for a random init) after an earlier
    # release shipped it untrained.
    #
    # Sampling copied from the 5090 tiel profile: temp 0.3 set there by hand on
    # real usage and read back, never 0 on these weights. Embedded template
    # kept: on the 5090 box tiel needs no derived template.
    Start-LLM 'tiel' (@(
      '-m',"$ModelsDir\tiel-coder-35b-a3b-mtp\Tiel-Coder-35B-A3B-MTP-UD-IQ3_XXS.gguf"
    ) + $cardRecipe + @(
      '--temp','0.3','--top-p','0.95','--top-k','20','--min-p','0'
    )) -envVars $cardEnv
    break
  }

  'qwen36' {
    # Qwen3.6-35B-A3B, unsloth UD-IQ3_XXS with MTP head (14.1 GB), the
    # publisher's own MoE.
    #
    # Sampling from unsloth's page for the thinking mode, read 2026-09-14:
    # temp 1.0, top-p 0.95, top-k 20, min-p 0. The page also lists
    # presence_penalty 1.5, which the 5090 qwen profile never set; left unset
    # here for the same reason, so that a bench compares like with like.
    #
    # Embedded template kept. It accepts a system message after the first user
    # turn, checked on 2026-09-14 with a direct request and then through Claude
    # Code, where Qwen3.8-27B's raised 'System message must be at the beginning'
    # on the late system messages Claude Code injects and needed a derived one.
    Start-LLM 'qwen36' (@(
      '-m',"$ModelsDir\qwen3.6-35b-a3b-mtp\Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf"
    ) + $cardRecipe + @(
      '--temp','1.0','--top-p','0.95','--top-k','20','--min-p','0'
    )) -envVars $cardEnv
    break
  }

  'stop'   { if ($Name) { Stop-One $Name } else { Stop-All }; break }
  'status' { Get-Status; break }
  'logs'   { Show-Logs $Name $Tail; break }
  default  { Write-Output "USAGE: llm-ctl.ps1 -Action tiel|qwen36|stop|status|logs" }
}
