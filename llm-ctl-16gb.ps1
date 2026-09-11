param(
  [ValidateSet('oxcoder','neohorse','stop','status','logs')]
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
# ONE build here, and that is the point: the official b10908 release binary,
# CUDA 13.3, unpacked flat with its cudart, no compilation. The 5090 box needs
# four builds because one of its models is NVFP4, a format whose CUDA kernels
# only exist in a build compiled for sm_120. Ada is sm_89 and none of the models
# below need anything but upstream, so a second build here would be dead weight.
#
# Verified by execution on 2026-09-11, driver 616.92:
#   llama-server.exe --list-devices
#   CUDA0: NVIDIA GeForce RTX 4080 SUPER (16375 MiB, 15061 MiB free)
# ---------------------------------------------------------------------------
$RootDir   = 'D:\LLM-Setup'
$ModelsDir = 'D:\models'

$exeMain     = "$RootDir\llama-cpp-b10908\llama-server.exe"
$workDirMain = "$RootDir\llama-cpp-b10908"

$instDir    = "$RootDir\instances"
$logDir     = "$RootDir\logs"
$serverPort = 8080

New-Item -ItemType Directory -Force -Path $instDir, $logDir | Out-Null

$builds = @{
  oxcoder  = @{ Exe = $exeMain; WorkDir = $workDirMain }
  neohorse = @{ Exe = $exeMain; WorkDir = $workDirMain }
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

function Start-LLM($name, $modelArgs, $exePath = $null, $workDirPath = $null) {
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
  $b = $builds[$name]
  if (-not $exePath)     { $exePath     = if ($b) { $b.Exe }     else { $exeMain }     }
  if (-not $workDirPath) { $workDirPath = if ($b) { $b.WorkDir } else { $workDirMain } }
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
  $inner = "cd /d `"$workDirPath`" && `"$exePath`" $quoted > NUL 2> `"$errLog`""
  $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = "cmd.exe /c $inner"; CurrentDirectory = $workDirPath }
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
# or generation_config.json on 2026-09-11, NOT copied from a neighbouring
# profile. Copying Qwen's shape onto nex and spark produced wrong and credible
# numbers on the 5090 box the day before.
#
# Context sizes are a starting point sized for 16 GB, to be corrected by the
# first VRAM reading, not by intuition.
# ---------------------------------------------------------------------------
switch ($Action) {


  'oxcoder' {
    # OxCoder-9B, a coding model published 2026-09-07 on a Qwen3.5 base
    # (Qwen3_5ForConditionalGeneration, 32 layers, 262K context). The 5090 box
    # has no Qwen3.5 anything: its two coders, tiel and kat, are both qwen35moe
    # mixtures of experts, and its Qwen weights are 3.8. Sampling from the model
    # card's SWE-bench protocol: temperature 1.0, top_p 0.95.
    Start-LLM 'oxcoder' @(
      '-m',"$ModelsDir\oxcoder-9b\OxCoder-9B.Q5_K_M.gguf",
      '--n-gpu-layers','99','--flash-attn','on','--jinja',
      # 262144 and not 131072: that IS the trained context of these weights, read
      # in the GGUF header (qwen35.context_length). Measured on 2026-09-11, the
      # doubling is FREE here: 80.9 tok/s at both sizes for oxcoder, 77.3 against
      # 77.5 for neohorse, 13_018 MiB of 16_376 either way. The 5090 box pays for
      # its window; this card does not, and halving it would only have thrown
      # away half the window for nothing.
      #
      # Asking for MORE is the trap. At --ctx-size 524288 the server still serves
      # 262144, says so in one log line ("exceeds the training context - capping"),
      # and keeps 15_882 MiB allocated: three gigabytes burned for zero context.
      '--host','0.0.0.0','--port','8080','--parallel','1','--ctx-size','262144',
      '-b','4096','-ub','2048',
      '--cache-type-k','q8_0','--cache-type-v','q8_0',
      '--temp','1.0','--top-p','0.95'
    )
    break
  }



  'neohorse' {
    # NeoHorse-1-9B, TokenRhythm, published 2026-09-05. Qwen3_5ForCausalLM,
    # 32 layers, 262K context. Its reported protocol is temperature 1.0,
    # top_p 0.95, top_k 20, min_p 0.0 AND presence_penalty 1.5, that last one
    # being a value no profile on the 5090 box uses. It is set here because the
    # publisher reports its numbers with it, and removing it would measure
    # something the publisher never claimed.
    Start-LLM 'neohorse' @(
      '-m',"$ModelsDir\neohorse-1-9b\NeoHorse-1-9B-Q5_K_M.gguf",
      '--n-gpu-layers','99','--flash-attn','on','--jinja',
      # 262144 and not 131072: that IS the trained context of these weights, read
      # in the GGUF header (qwen35.context_length). Measured on 2026-09-11, the
      # doubling is FREE here: 80.9 tok/s at both sizes for oxcoder, 77.3 against
      # 77.5 for neohorse, 13_018 MiB of 16_376 either way. The 5090 box pays for
      # its window; this card does not, and halving it would only have thrown
      # away half the window for nothing.
      #
      # Asking for MORE is the trap. At --ctx-size 524288 the server still serves
      # 262144, says so in one log line ("exceeds the training context - capping"),
      # and keeps 15_882 MiB allocated: three gigabytes burned for zero context.
      '--host','0.0.0.0','--port','8080','--parallel','1','--ctx-size','262144',
      '-b','4096','-ub','2048',
      '--cache-type-k','q8_0','--cache-type-v','q8_0',
      '--temp','1.0','--top-p','0.95','--top-k','20','--min-p','0','--presence-penalty','1.5'
    )
    break
  }

  'stop'   { if ($Name) { Stop-One $Name } else { Stop-All }; break }
  'status' { Get-Status; break }
  'logs'   { Show-Logs $Name $Tail; break }
  default  { Write-Output "USAGE: llm-ctl.ps1 -Action oxcoder|neohorse|stop|status|logs" }
}
