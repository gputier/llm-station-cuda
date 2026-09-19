param(
  [ValidateSet('bonsai','bonsai2','embed','kat','muse','nex','ornith','qwen','qwenf','qwent','qwenu','spark','tiel','whittle','stop','status','logs')]
  [string]$Action,
  [string]$Name,  # optional: for 'stop' and 'logs', targets a named instance
  [int]$Tail = 40, # for 'logs': history lines to show before following live
  # Extra llama-server flags appended to the profile, for a trial that should not
  # become a commit. Space separated, quoted as one string so it survives ssh:
  #   -Action nex -Extra "--spec-type ngram-cache --spec-draft-n-max 4"
  # They are appended LAST, which wins only for a flag that takes one value. A
  # flag that accumulates keeps the profile's value as well: --spec-type, see
  # -NoSpec below, and --n-cpu-moe, where a trailing 0 left the profile's 2 in
  # force on the 16 GB box on 2026-09-14. To take such a flag away, start the
  # server without it.
  # Nothing that proves itself here should stay here: a setting worth keeping
  # goes into its profile, where a comment can say why.
  [string]$Extra = '',
  # Strips the profile's own speculation flags before -Extra is appended, which
  # -Extra alone cannot do: --spec-type accumulates rather than replaces, so
  # asking for another type on a profile that already has one runs BOTH. That is
  # not academic, it cost 36% of tiel's decode on 2026-09-10.
  [switch]$NoSpec
)

# ---------------------------------------------------------------------------
# Paths. Adjust these three to match your machine; nothing else below is
# installation-specific.
# ---------------------------------------------------------------------------
$RootDir   = 'D:\LLM-Setup'
$ModelsDir = 'D:\models'
$CudaRoot  = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA'

# ---------------------------------------------------------------------------
# Four llama.cpp builds coexist on this box, on purpose. They are NOT
# interchangeable, and picking the wrong one is a silent failure.
#
#  - turboquant: a frozen custom fork (2026-04-07). Still serves 'embed'. Its
#    only reason to exist was the turbo3 cache quants of a model that has since
#    been removed, so it has no remaining technical justification and could be
#    retired once 'embed' is validated on upstream.
#  - upstream: official build of 2026-08-11. The only one of the first two that
#    knows the muse-glimmer architecture. Serves 'muse' and 'qwenu'.
#  - dated build (2026-08-27): see below. Serves 'qwen', and only it can. It
#    also served 'ornith' until 2026-09-08, see that profile.
#  - b10826 (2026-09-06): the official release binary, unzipped flat, no
#    compilation. Serves 'tiel' and, since 2026-09-08, 'ornith'. See its
#    block below.
# ---------------------------------------------------------------------------
$exe       = "$RootDir\llama-cpp-turboquant-win\build-win\bin\llama-server.exe"
$workDir   = "$RootDir\llama-cpp-turboquant-win\build-win\bin"
$cudaBin   = "$CudaRoot\v12.8\bin"

$exeUp     = "$RootDir\llama-cpp-upstream\build-win\bin\Release\llama-server.exe"
$workDirUp = "$RootDir\llama-cpp-upstream\build-win\bin\Release"
$cudaBinUp = "$CudaRoot\v13.3\bin"

# Build of 2026-08-27 (0.3.0-dev, build 479, commit 192067b72), in a SEPARATE
# directory. It became mandatory on 2026-08-28: it serves 'qwen', and it is the
# only build that can. The model weights are now NVFP4, ggml type 40, whose CUDA
# kernels exist only in this build, and it is compiled for sm_120, the RTX 5090
# architecture (CMAKE_CUDA_ARCHITECTURES=120). The NVFP4 code path is ABSENT
# from the 2026-08-11 binaries, verified. Do not delete this build.
#
# Its history is worth keeping. Measured on 2026-08-27 against the 2026-08-11
# build, fixed seed, identical MTP counters (495/907), it gained NOTHING on the
# same model: 118.49 tok/s versus 118.32. Recompiled again with
# GGML_CUDA_FA_ALL_QUANTS=ON, on the theory that the q4_0 cache lacked a
# dedicated flash-attention kernel: exactly the same result. It was therefore
# written off as useless, which was true at constant model and false the moment
# the model format changed. A dead end is only dead under the assumptions you
# tested it with.
#
# Two traps found while building it:
#  - This llama.cpp splits the server into DLLs. llama-server.exe is a 10 KB
#    launcher; the code lives in llama-server-impl.dll and ggml-cuda.dll. A
#    10 KB binary is NOT the sign of a failed build. It must therefore be
#    launched from its own directory, which Start-LLM does via workDirPath.
#  - Linking FAILS if an instance is still running on this build (LNK1104,
#    cannot open ggml-cuda.dll). Stop the server before recompiling.
$exeNew     = "$RootDir\llama-cpp-20260827\build-win\bin\Release\llama-server.exe"
$workDirNew = "$RootDir\llama-cpp-20260827\build-win\bin\Release"

# Official binary b10826 (2026-09-06), CUDA 13.3, flat layout like llama-cpp-b10740: the zip and
# its cudart unpacked into one directory, no compilation. Serves 'tiel' since 2026-09-06. Control
# against the 2026-08-27 build, same 65,615-token prompt, 400 tokens, seed 42, 3 runs, temperature
# 0.6: prefill 8,724 -> 9,155 tok/s (+4.9%), decode 211.3 -> 210.8 (identical), VRAM 31,707 ->
# 31,550 MiB, MTP counters within noise (339/229 against 341/228). Two behaviour changes it brings, both logged at startup:
# preserve_reasoning is on by default (turn off with --no-reasoning-preserve if prompts grow), and
# it recommends --image-min-tokens 1024 for this vision model.
$exeB10826     = "$RootDir\llama-cpp-b10826\llama-server.exe"
$workDirB10826 = "$RootDir\llama-cpp-b10826"

# Official binary b10883 (2026-09-09), CUDA 13.3, same flat layout: release zip and its cudart
# unpacked into one directory, no compilation. Installed 2026-09-10 for three candidate models that
# the older builds cannot serve, and it serves only those: 'nex', 'spark', 'bonsai'. Nothing in
# production was moved onto it.
#
# It exists because of ONE hard requirement. b10826 does not know the 'spark2_5' architecture,
# whose support landed in b10828, and an engine that does not know an architecture does not say so
# clearly: it fails at load. b10883 was taken rather than b10828 exactly to avoid doing this twice.
#
# It has NOT been benchmarked against b10826 on the production models. Do not move tiel, ornith or
# kat here on the assumption that newer is faster; the 2026-08-27 build taught that lesson at a
# cost of two full compilations for a gain of nothing.
$exeB10883     = "$RootDir\llama-cpp-b10883\llama-server.exe"
$workDirB10883 = "$RootDir\llama-cpp-b10883"

# THE ONE BINARY HERE THAT IS NOT AN UPSTREAM RELEASE. PrismML's fork of
# llama.cpp, release prism-b10685-7dffb15 (2026-09-15), win-cuda-13.3-x64,
# unpacked like the others and serving one profile: 'bonsai2'.
#
# It exists because the rule it breaks had no third option. Bonsai 2 ships only
# PQ2_0 and PTQ1_0, both of which upstream rejects as unknown types, and there
# is no Q2_g64 in that repository the way there was for Bonsai 1. Worse than a
# clean refusal: the model card states that upstream loads a Q2_0 file without a
# warning and produces garbage, having no Hadamard activation runtime. So the
# choice was this fork or no Bonsai 2 at all.
#
# Taken as a prebuilt release archive, not compiled, which keeps the one thing
# that mattered in the rule: nothing is built on this box. The cost is that the
# fork tracks upstream at its own pace, b10685 here against b10883 next door.
# Do not move any other profile onto it.
$exePrism      = "$RootDir\llama-cpp-prism-b10685\llama-server.exe"
$workDirPrism  = "$RootDir\llama-cpp-prism-b10685"
$instDir   = "$RootDir\instances"
New-Item -ItemType Directory -Force -Path $instDir | Out-Null

# Logs live in their own directory since 2026-09-10. They used to sit at the root
# of $RootDir, where they were indistinguishable from the scripts, the model
# notes and eight dated backups of this very file.
$logDir    = "$RootDir\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Every model action sits on port 8080 and they are mutually exclusive on the
# GPU: starting one unloads the others. This was a per-profile table until
# 2026-09-08, where every row held the same 8080 and its only real job was to
# enumerate the profile names. A third list of names to keep in step with the
# ValidateSet and with $builds, saying nothing of its own. The names now come
# from $builds, which does carry per-profile information.
$serverPort = 8080

# Which build serves which profile. The one indirection that earns its keep:
# picking the wrong binary is a silent failure, and the profile-to-build pairing moves far more
# often than the port does (tiel on 2026-09-06, ornith on 2026-09-08). Quoting the binary by hand
# in each switch branch meant six independent places to keep in step. Change a pairing HERE, not
# in the branch. Start-LLM still accepts an explicit binary, which is what the bench launchers use
# to run a profile against another build without touching this file.
$builds = @{
  embed  = @{ Exe = $exe;         WorkDir = $workDir;         CudaBin = $cudaBin   }
  muse   = @{ Exe = $exeUp;       WorkDir = $workDirUp;       CudaBin = $cudaBinUp }
  qwenu  = @{ Exe = $exeUp;       WorkDir = $workDirUp;       CudaBin = $cudaBinUp }
  # Candidate of 2026-09-19, on the same build as the profile it is benched against.
  qwenf  = @{ Exe = $exeUp;       WorkDir = $workDirUp;       CudaBin = $cudaBinUp }
  qwent  = @{ Exe = $exeUp;       WorkDir = $workDirUp;       CudaBin = $cudaBinUp }
  qwen   = @{ Exe = $exeNew;      WorkDir = $workDirNew;      CudaBin = $cudaBinUp }
  tiel   = @{ Exe = $exeB10826;   WorkDir = $workDirB10826;   CudaBin = $cudaBinUp }
  # ornith moved off the 2026-08-27 build on 2026-09-08. The move bought no speed, it was taken
  # because Ornith is Q5_K_M and never needed the NVFP4 kernels. Figures in docs/tuning-log.md.
  ornith = @{ Exe = $exeB10826;   WorkDir = $workDirB10826;   CudaBin = $cudaBinUp }
  kat    = @{ Exe = $exeB10826;   WorkDir = $workDirB10826;   CudaBin = $cudaBinUp }
  # The three candidates of 2026-09-10. They are on b10883 because nothing older can serve them,
  # not because it is newer. See the b10883 block above.
  nex    = @{ Exe = $exeB10883;   WorkDir = $workDirB10883;   CudaBin = $cudaBinUp }
  spark  = @{ Exe = $exeB10883;   WorkDir = $workDirB10883;   CudaBin = $cudaBinUp }
  bonsai = @{ Exe = $exeB10883;   WorkDir = $workDirB10883;   CudaBin = $cudaBinUp }
  # Candidate of 2026-09-19: 'qwen4exp' is known to b10826 and b10883, checked in llama.dll.
  whittle = @{ Exe = $exeB10883;  WorkDir = $workDirB10883;   CudaBin = $cudaBinUp }
  # The only row pointing at the fork. See the $exePrism block above.
  bonsai2 = @{ Exe = $exePrism;   WorkDir = $workDirPrism;    CudaBin = $cudaBinUp }
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

# Wait for the graphics card to actually hand its memory back.
#
# Stop-Process returns as soon as the process is marked dead, but Windows frees
# device memory ASYNCHRONOUSLY. An instance relaunched before that hand-back
# completes sees a card that is still occupied. A FIXED delay cannot cover this
# properly: it is either too short or wasted time. So we wait for the processes
# to actually disappear, then for the card's usage to settle, with a 30 s guard
# rail.
#
# Verified by execution on 2026-08-28: 1.69 s when the card is already free, so
# LESS than the 2 fixed seconds it replaces; 2.51 s falling back when nvidia-smi
# is missing, instead of burning the timeout; and it does keep waiting while
# usage is still falling.
#
# WHAT THIS CODE IS *NOT* FOR, having first been believed so and measured wrong.
# The process holds ~2.9 GB of SHARED memory, meaning host RAM presented as
# graphics memory, on top of its ~25.7 GB dedicated. That is NOT an overflow
# caused by relaunching too fast: after a clean restart on 2026-08-29 the split
# came back identical to within one percent, while 5.7 GB of VRAM sat FREE, and
# throughput stayed nominal (median 118.35 tok/s over five seeds against a 123.4
# baseline). A genuine spill does not trigger with 5.7 GB free, and would not
# reproduce to the percent. The likely explanation is pinned host memory
# allocated by CUDA, which WDDM accounts under "Shared Usage". THIS IS UNPROVEN:
# do not build on it without measuring.
#
# To diagnose, only one path works: under WDDM nvidia-smi reports [N/A] per
# process and sees nothing. Only the Windows counters
# "\GPU Process Memory(pid_<PID>*)\Dedicated Usage" and its "Shared Usage"
# sibling give the split.

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
    # nvidia-smi unavailable: fall back to the old fixed delay rather than
    # burning the whole timeout. Control must not depend on that one tool.
    if ($used -lt 0) { Start-Sleep -Seconds 2; return }
    if ($used -eq $previous) { $stable++ } else { $stable = 0 }
    if ($stable -ge 2) { return }
    $previous = $used
    Start-Sleep -Milliseconds 500
  }
  # Reached only when the deadline ran out, possibly inside the first loop, in
  # which case the card was never read at all. Say so rather than load in silence.
  Write-Output "WARN device memory not confirmed released after $timeoutSec s"
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

# Live log tailing.
#
# llama-server writes ALL of its output to stderr, including progress lines and
# served requests. logs\llm-err-<name>.log carries everything; standard output
# went to NUL on 2026-09-10, after ten empty llm-out-*.log files had accumulated
# at the root. This action exists so nobody has to remember any of that: it picks
# the log of the running instance and follows it.
# Ctrl+C to exit; the server is unaffected.
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

function Start-LLM($name, $modelArgs, $cudaDevices = $null, $exePath = $null, $workDirPath = $null, $cudaBinPath = $null) {
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
    Write-Output 'NOSPEC drapeaux de speculation du profil retires'
  }
  # -Extra flags land here rather than in each of the eleven branches.
  if ($Extra) {
    $sup = @($Extra -split '\s+' | Where-Object { $_ })
    $modelArgs = @($modelArgs) + $sup
    Write-Output ("EXTRA " + ($sup -join ' '))
  }
  # Explicit arguments win, so a bench can run a profile against another build. Otherwise the
  # pairing comes from $builds, and a profile missing from it falls back to the turboquant paths.
  $b = $builds[$name]
  if (-not $exePath)     { $exePath     = if ($b) { $b.Exe }     else { $exe }     }
  if (-not $workDirPath) { $workDirPath = if ($b) { $b.WorkDir } else { $workDir } }
  if (-not $cudaBinPath) { $cudaBinPath = if ($b) { $b.CudaBin } else { $cudaBin } }
  $port = $serverPort
  # Free the port: kill any tracked instance on the same port.
  $killed = @()
  foreach ($i in Read-Instances) {
    if ($i.Port -eq $port) { Kill-Pid $i.Pid; $killed += $i.Pid; Remove-Item (Join-Path $instDir "$($i.Name).json") -Force -ErrorAction SilentlyContinue }
  }
  # Then any ORPHAN instance still listening on that port. A llama-server
  # started by hand, or one that survived a loss of tracking, used to block the
  # bind silently while this script reported STARTED and the health check went
  # green by querying the old process.
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    Where-Object { $killed -notcontains $_ -and (Get-Process -Id $_ -ErrorAction SilentlyContinue).ProcessName -eq 'llama-server' } |
    ForEach-Object { Write-Output "KILLED_ORPHAN pid=$_ port=$port"; Kill-Pid $_ }
  Wait-VramReleased

  # Standard output goes to NUL, and no llm-out-<name>.log is created any more.
  # Ten of them sat at the root of $RootDir, every single one at zero bytes, for
  # as long as this script has existed: llama-server writes everything to stderr,
  # progress lines and served requests included. They were pure noise.
  # To get them back, put "$RootDir\llm-out-$name.log" here instead of NUL.
  $errLog = "$logDir\llm-err-$name.log"
  Clear-Content $errLog -ErrorAction SilentlyContinue

  $quoted = ($modelArgs | ForEach-Object { Quote $_ }) -join ' '
  # No `set` inside the cmd line, and this is not a style choice. cmd /c strips
  # the outer quotes of the whole line, after which `set PATH=<value> && <rest>`
  # swallows ` && <rest>` INTO the value: nothing after it ever runs, no log file
  # is even created, and Win32_Process.Create still returns 0. Measured on this
  # machine 2026-09-01, on every quoting variant tried, including /s and a extra
  # wrapping pair. The environment is therefore set on THIS process before the
  # call and restored right after; the child inherits it. `cd /d` is still
  # required, dropping it makes the launch fail.
  $savedPath = $env:PATH
  $savedCuda = $env:CUDA_VISIBLE_DEVICES
  $env:PATH = "$cudaBinPath;$env:PATH"
  # CPU-only instances: hide the GPU to avoid a pointless CUDA init.
  if ($null -ne $cudaDevices) { $env:CUDA_VISIBLE_DEVICES = $cudaDevices }
  $inner = "cd /d `"$workDirPath`" && `"$exePath`" $quoted > NUL 2> `"$errLog`""
  $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = "cmd.exe /c $inner"; CurrentDirectory = $workDirPath }
  $env:PATH = $savedPath
  if ($null -eq $savedCuda) { Remove-Item Env:\CUDA_VISIBLE_DEVICES -ErrorAction SilentlyContinue }
  else { $env:CUDA_VISIBLE_DEVICES = $savedCuda }
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
    # almost certainly failed at startup. Reporting success hid the failure.
    Write-Output "FAILED name=$name port=$port (no llama-server started, see llm-err-$name.log)"
    Get-Content $errLog -Tail 5 -ErrorAction SilentlyContinue
  }
}

function Get-Status {
  $any = $false
  foreach ($i in Read-Instances) {
    $any = $true
    $alive = [bool](Get-Process -Id $i.Pid -ErrorAction SilentlyContinue)
    $health = 'no answer'
    try {
      $resp = Invoke-WebRequest -Uri "http://localhost:$($i.Port)/health" -TimeoutSec 3 -UseBasicParsing
      if ($resp.StatusCode -eq 200) { $health = 'ok' }
    } catch { $health = 'no answer' }
    Write-Output "RUNNING name=$($i.Name) pid=$($i.Pid) port=$($i.Port) alive=$alive health=$health"
  }
  if (-not $any) { Write-Output "NOT_RUNNING" }
}

switch ($Action) {
  'stop'   { if ($Name) { Stop-One $Name } else { Stop-All }; break }
  'status' { Get-Status; break }
  'logs'   { Show-Logs $Name $Tail; break }

  'muse' {
    # Muse Glimmer 30B: dense backbone + perception encoder + DFlash drafter
    # (block size 16: 1 anchor + 15 proposed tokens).
    # Runs on the UPSTREAM build: the turboquant fork does not know the
    # muse-glimmer architecture.
    Start-LLM 'muse' @(
      '-m',"$ModelsDir\muse-glimmer-30b\Muse-Glimmer-30B-UD-Q4_K_XL.gguf",
      # kquant mmproj (1.30 GB), not Q8_0 (1.91) and not BF16 (3.58). Measured on
      # a deliberately hard bank statement (11pt body text, low contrast, rotated,
      # blurred), 15 lines, ground truth compared field by field over 3 runs: the
      # kquant makes NO value error, where the Q8_0 reads -88.90 instead of
      # -88.940. Lighter AND more faithful, which was not the intuition.
      # Total VRAM 29.0 GB out of 32.6.
      '--mmproj',"$ModelsDir\muse-glimmer-30b\mmproj-kquant.gguf",
      # DFlash drafter WITHOUT --spec-draft-n-max. The DFlash block is 16 tokens
      # (1 anchor + 15 proposed), which makes forcing 15 look obvious: it is a
      # trap, measured here. Forcing the depth does not merely add cost, it
      # COLLAPSES the acceptance rate.
      #   no drafter ....... 82.24 tok/s
      #   n-max 15 ......... 101.27 tok/s, acceptance 10.2% (3482 drafted / 364 kept)
      #   default .......... 107.33 tok/s, acceptance 39.9% (818 drafted / 326 kept)
      # Nearly the same gain for 4.3x less wasted draft compute.
      '--spec-type','draft-dflash',
      '--spec-draft-model',"$ModelsDir\muse-glimmer-30b\dflash-kquant.gguf",
      '--spec-draft-ngl','99',
      '--n-gpu-layers','99','--load-mode','mlock','--flash-attn','on',
      '--jinja',
      # 262,144 since 2026-08-31, down from 1,048,576: a million tokens cost more
      # than they returned. The million remains a proven capability of the model,
      # recall verified by needle-in-a-haystack at 556,390 tokens, and raising the
      # two numbers below is all it takes to get it back. Note that the memory cost
      # is NOT linear, see the qwen block.
      #
      # ONE lock, and it is not YaRN: llama.cpp caps the slot on the
      # context_length written IN THE GGUF ("the slot context exceeds the training
      # context of the model - capping") and ignores --ctx-size beyond it. Without
      # this override, asking for 262144 or 786432 yields exactly 131072, silently.
      #
      # The three YaRN flags were REMOVED on 2026-08-12: they did nothing. Without
      # --rope-scaling yarn --rope-scale 8 --yarn-orig-ctx 131072, the server
      # allocates n_ctx_slot = 1048576 with no capping line, and recall is proven
      # at 556,390 tokens (needle in a haystack). The architecture explains it:
      # only the sliding-window layers use RoPE, the global layers have no
      # positional encoding at all, so there is no position to stretch. The 1M
      # context did not work thanks to YaRN, it worked despite it.
      '--override-kv','muse-glimmer.context_length=int:262144',
      '--host','0.0.0.0','--port','8080','--ctx-size','262144',
      # -ub 512, NOT 4096. This is the critical setting of this profile and it
      # carries TWO failures on its own, discovered in this order:
      #  1. with --mmproj the prompt goes through the multimodal pipeline, whose
      #     compute buffer explodes past ~10k tokens and returns
      #     "decode() failed: bad allocation" (500), seen from the client as
      #     "400 Failed to tokenize prompt";
      #  2. that same buffer weighed 10 GB of VRAM, which starved the DFlash
      #     drafter and SILENTLY disabled it past 96k of context. The context
      #     was not the cause. 83 tok/s instead of 128, with no error.
      # No cost: generation unchanged, and prompt processing goes from 1376 to
      # 3000 tok/s.
      # Note: the -ub sweep was only redone on Metal (630 tok/s at 512, 566 at
      # 4096, outside any memory constraint), not on this card. 512 is kept
      # because it is proven HERE by the two failures above, not by transposition.
      '--parallel','1','-b','2048','-ub','512',
      # -cram 24576 and NOT the 8192 default. This flag sizes the host-RAM PROMPT cache, where
      # --cache-idle-slots parks a context that went idle before a new task overwrites it. It was
      # set on 'qwen' after measurement and never carried over here, although this profile runs
      # the same 262144 window. At this profile's own logged prefill rate, 3284 tok/s, losing a
      # 150k context to the cache costs 46 s of recompute. Costs host RAM only, no VRAM.
      '-cram','24576',
      '--cache-type-k','q8_0','--cache-type-v','q8_0',
      '--temp','1.0','--top-p','0.95','--top-k','64'
    )
  }

  'qwen' {
    # Qwen3.8-27B (Apache 2.0, weights released 2026-08-14). Hybrid dense model:
    # 48 Gated DeltaNet layers + 16 full-attention layers, architecture 'qwen35'.
    # Native vision.
    Start-LLM 'qwen' @(
      # NVFP4 since 2026-08-28, LOW tier. The model body is NVFP4, a format the
      # RTX 5090 tensor cores execute NATIVELY. Measured here against the previous
      # UD-Q5_K_XL, same prompt, same protocol, speculation DISABLED on both sides
      # to remove MTP acceptance noise:
      #   decode ..... 58.84 -> 70.64 tok/s   (+20.1%)
      #   prefill .... 3,015 -> 4,584 tok/s   (+52.0%)
      #   VRAM ....... 28.5 -> 24.4 GB        (4.1 GB freed)
      # With speculation on, median over 5 seeds: 98.46 -> 122.79 tok/s (+24.7%).
      # On a real workload of 60 reasoning problems: 233.1 s -> 188.6 s.
      #
      # QUALITY: no measurable loss. MMLU over 500 stratified questions, identical
      # question set, 5-shot, temperature 0: 72.4% -> 72.8%, two questions out of
      # five hundred, well inside the roughly 2-point uncertainty. GSM8K over 60
      # problems: 56/60 for BOTH, failing the same problems. Recall verified at
      # 198,625 tokens, 4/4.
      #
      # The MEDIUM tier was measured and REJECTED: slower (98.05 tok/s), heavier,
      # at equal quality (71.8%). Do not revisit without a new reason. The
      # repository publishes nine tiers sharing an identical NVFP4 body, differing
      # only in the precision of the heads.
      '-m',"$ModelsDir\qwen3.8-27b-nvfp4\Qwen3.8-27B-NVFP4-MTP-LOW.gguf",
      # Vision projector from the SAME repository. It is NOT identical to the
      # mmproj-F16 shipped elsewhere, contrary to what the model card claims:
      # sizes and hashes differ (927,607,488 versus 931,146,432 bytes).
      '--mmproj',"$ModelsDir\qwen3.8-27b-nvfp4\mmproj-BF16.gguf",
      # MTP: unlike the DFlash drafter of muse, there is NO draft file at all.
      # The blk.*.nextn.* tensors are already inside the quant; llama.cpp loads
      # them and IGNORES them without this flag. One flag enables speculation.
      #
      # n-max 3. Re-swept on 2026-08-27 with a fixed seed, 800 tokens, 3 runs,
      # median. The earlier sweep concluded 2; it was invalidated by the absence
      # of a seed, since generation varies run to run and draft acceptance with
      # it. Fixed-seed values, vision profile at -ub 2048:
      #   n-max 2 ...... 114.90 tok/s, acceptance 64% (449/697)
      #   n-max 3 ...... 117.75 tok/s, acceptance 54% (495/907)  <-- kept
      #   n-max 4 ...... 113.53 tok/s, acceptance 45% (516/1128)
      #   n-max 6 ......  97.76 tok/s, acceptance 33% (534/1575)
      # The underlying law is the same as the DFlash drafter above: past the
      # useful depth, drafted tokens grow faster than kept tokens and the gain
      # inverts. A speculation sweep without a fixed seed measures nothing: the
      # 2-3-4 neighbourhood fits entirely inside the noise.
      # --image-min-tokens 1024 is requested by the server itself at load time: "Qwen-VL models
      # require at minimum 1024 image tokens to function correctly on grounding tasks". Without
      # it the model reads values correctly but mislabels what they point at. It only costs
      # tokens when an image is actually sent.
      '--image-min-tokens','1024',
      # n-max 4 and NOT 3. The earlier sweep concluded 3, but it ran on a 10608-token prompt
      # only. Re-swept on 2026-08-31 at both empty and full (150k) context, two distinct tasks,
      # 3 seeds, median decode tok/s:
      #                       n-max 2   n-max 3   n-max 4   n-max 5   n-max 6   n-max 8
      #   reasoning / empty      .       152.50    169.83    165.28    159.20    125.22
      #   reasoning / 150k     71.01      76.90     83.03     79.81     79.38     69.10
      #   code / empty           .       139.24    129.88    125.47    118.33    100.58
      #   code / 150k          65.06      66.19     71.21     73.82     61.05     60.92
      # 4 wins 3 cases out of 4, by 7.6 to 11.4 %, and only loses on code at empty context
      # (-6.7 %), the least representative case: under an agentic client the context is never
      # empty, the system prompt alone exceeds ten thousand tokens on the first turn.
      # WHY the law inverts: at full context decoding ONE token costs far more, since attention
      # sweeps the whole context. Verifying several tokens in a single pass therefore amortises a
      # longer draft, whereas at short context the draft dominates the cost. Acceptance rate
      # falls monotonically with n-max and is NOT the criterion; only throughput is. At 63.3 %
      # acceptance n-max 3 yields less than n-max 4 at 55.1 %.
      '--spec-type','draft-mtp','--spec-draft-n-max','4',
      '--n-gpu-layers','99','--load-mode','mlock','--flash-attn','on',
      '--jinja',
      # TWO flags deliberately ABSENT, both tried and measured on 2026-08-31. Documented here
      # because the server suggests one of them at EVERY startup, and nothing else stops the next
      # reader from redoing the test.
      #
      # --reasoning-preserve: the log prints "chat template supports preserving reasoning,
      # consider enabling it". Tested across all FOUR combinations, same 3-turn conversation, same
      # seed, measuring the turn-3 prompt:
      #   client resends reasoning + flag on ..... 2,846 tokens
      #   client resends reasoning + flag off .... 2,846
      #   client resends nothing   + flag on ......... 219
      #   client resends nothing   + flag off ........ 219
      # The flag changes nothing in either direction. What carries reasoning across turns is the
      # CLIENT resending reasoning_content, not the server. Do not raise it again.
      #
      # --cache-reuse: looks like the obvious lever for long contexts, since it recovers cached KV
      # by shifting when the prompt changes in its MIDDLE, which is what an agentic client does on
      # every turn. It does not apply to this model. Qwen3.8 is hybrid, 48 Gated DeltaNet layers
      # with a recurrent state against 16 full-attention layers, and a recurrent state can neither
      # be truncated nor shifted. llama.cpp disables the flag SILENTLY on such architectures.

      # Chat template DERIVED from the embedded one, a single line changed. The
      # original raises 'System message must be at the beginning' as soon as a
      # system message arrives after a user message. Agentic clients inject those
      # mid-session, so this model failed with HTTP 500 on the very first turn
      # while /v1/chat/completions worked perfectly. The derived template renders
      # a late system message as an ordinary ChatML system turn, which the format
      # supports natively, instead of raising.
      '--chat-template-file',"$ModelsDir\qwen3.8-27b\chat-template-system-anywhere.jinja",
      # 393216 since 2026-09-01, and it takes TWO flags, not one. The GGUF declares
      # context_length=262144: llama.cpp caps the slot on that value and ignores a
      # larger --ctx-size, in a single log line, exactly as it did on muse. The
      # override lifts the declared value, --ctx-size then sizes both the slot and
      # the buffers on it. Verified in /props: default_generation_settings.n_ctx
      # reads 393216, and the log prints n_ctx_slot with no capping line.
      #
      # 384k IS FREE ON THIS CARD, 512k IS NOT, and the two were measured rather
      # than reasoned about. Same 50,480-token prompt, 800 tokens forced, fixed
      # seed, cold prefill on a fresh process each time:
      #   262144 ... VRAM 27,110 MiB ... decode 123.6 tok/s ... prefill 4,007 tok/s
      #   393216 ... VRAM 31,291 MiB ... decode 122.5 tok/s ... prefill 4,035 tok/s
      #   524288 ... VRAM 31,858 MiB ... decode  94.2 tok/s ... prefill 2,308 tok/s
      # Half the extra window costs 4.2 GB of VRAM and NOTHING else. The full
      # doubling costs a quarter of the decode and 43% of the prefill, and it also
      # stops being reproducible: six runs at 512k spread from 71.9 to 94.9 tok/s
      # decode and 1,716 to 2,333 prefill, where 262k and 384k both hold within 1%.
      # The throttling wall on this card therefore sits BETWEEN 31.3 and 31.9 GB,
      # not at the ~29 GB the q8_0 cache reading had suggested: that earlier figure
      # was the point where a heavier KV cache started costing, not a hard edge.
      #
      # Only 1,316 MiB of VRAM are left free here. This profile has little
      # headroom: another GPU tenant pushes it out of memory.
      #
      # RECALL PAST 262144 IS NOT PROVEN. A window the server accepts says nothing
      # about what the model still finds in it, and 262144 is where the model was
      # trained. A needle-in-a-haystack run at 300k+ was attempted on 2026-09-01
      # and abandoned when the client dropped the connection mid-prefill; the
      # server was fine. Treat the top third of this window as unproven.
      #
      # CLAUDE_CODE_MAX_CONTEXT_TOKENS in the client launcher must move with this
      # value, in the same commit: a client promised more than the server serves is
      # truncated server-side with no warning.
      '--override-kv','qwen35.context_length=int:393216',
      '--host','0.0.0.0','--port','8080','--ctx-size','393216',
      # --parallel 1: speculative decoding is a SINGLE-STREAM optimisation, its
      # gain evaporates past a few concurrent streams.
      #
      # -ub 2048 since 2026-08-27, NOT 512. The 512 had been copied from muse on
      # the assumption that the multimodal buffer would explode as soon as it was
      # raised: measured here, that is false at 2048, the vision encoder stays
      # loaded with no penalty. Overflow only happens at 4096. Fixed seed,
      # 10,608-token prompt:
      #   -ub  512 ... prefill 3,106 tok/s ... decode 117.08 tok/s ... VRAM 29.5 GB
      #   -ub 2048 ... prefill 3,252 tok/s ... decode 117.75 tok/s ... VRAM 30.7 GB  <--
      #   -ub 4096 ... prefill 2,582 tok/s ... decode 111.81 tok/s ... VRAM 31.5 GB
      # At 4096 the card throttles and BOTH metrics regress.
      #
      # Removing --mmproj does NOT gain throughput, contrary to the starting
      # hypothesis: 115.70 tok/s without the encoder versus 114.90 with, which is
      # noise. It costs 1.3 GB of VRAM and nothing else. Do not drop it for speed.
      '--parallel','1','-b','4096','-ub','2048',
      # KV cache in q4_0, NOT q8_0 like muse. This is not a quality-for-speed
      # trade, it is a VRAM trade, and it was measured. At 262k, q8_0 leaves
      # 675 MB of headroom out of 32.6 GB and throughput COLLAPSES:
      #   q8_0, 262k .... 31,932 MB VRAM ...  73.81 tok/s
      #   q4_0, 262k .... 28,370 MB VRAM ... 100.32 tok/s
      #   q8_0, 131k .... 26,846 MB VRAM ...  99.00 tok/s
      # Below ~29 GB throughput saturates around 100 tok/s: q4_0 at 262k and q8_0
      # at 131k run at the same speed, and the former gives twice the window.
      # The q4 cache does not cost recall: needle in a haystack found at 207,067
      # tokens of prompt, verified by execution and not deduced.
      '--cache-type-k','q4_0','--cache-type-v','q4_0',
      # -cram 24576 rather than the 8192 default. This flag does NOT size the KV
      # cache, it sizes the PROMPT cache in host RAM, where --cache-idle-slots,
      # on by default, parks a context that has gone idle before a new task
      # overwrites it. This, and not the slot count, decides whether coming back
      # to a conversation costs nothing or a full minute.
      # Measured on three disjoint ~150k-token contexts, replayed A, A, B, C, A:
      #                            default 8192      -cram raised
      #   A cold ................. 139,810 / 61.3 s  139,810 / 61.5 s
      #   A replayed at once .....       4 /  0.3 s        4 /  0.3 s
      #   A after B and C ........ 139,810 / 62.6 s        4 /  0.3 s
      # A 140k context weighs 2,461 MB in that cache, so 8 GB does not hold three
      # of them: TWO interleaved contexts are enough to lose one entirely. Cost is
      # host RAM only, 11,732 to 16,946 MB for the process with 91,866 MB still
      # free, and VRAM is untouched. The A/B above was run at -cram 49152; the
      # ceiling was brought down to 24576 MB on 2026-09-01 to bound the host-RAM
      # footprint. At 2,461 MB per 140k context that still holds about ten of
      # them, well past the two the measurement showed were needed.
      # MEASUREMENT TRAP: a cache test whose contexts all fit in the budget does
      # not measure the cache, it measures that nothing had to be evicted. Our
      # first attempt, at 8k per context, wrongly concluded interleaving was free.
      '-cram','24576',
      # Qwen thinking-mode calibration values. top-k is 20 here and 64 on muse.
      # --min-p 0 is also a calibration value: llama.cpp imposes 0.05 by default
      # when nothing sets it, which clips the tail of the distribution ON TOP OF
      # the already-calibrated top-p and top-k, with no message. Quality setting,
      # no measurable throughput effect.
      '--temp','1.0','--top-p','0.95','--top-k','20','--min-p','0'
    # Build of 2026-08-27, NOT the 2026-08-11 one: NVFP4 is ggml type 40, whose
    # CUDA kernels exist only in that build, compiled for sm_120.
    )
  }

  'qwenu' {
    # Qwen3.8-27B Uncensored: same base model as 'qwen' (Apache 2.0), abliterated,
    # Q5_K_M quant. Deployed ALONGSIDE 'qwen', not instead of it: the aligned
    # profile stays the reasoning and coding model, this one is only used when the
    # aligned version refuses a legitimate task.
    #
    # The launch profile is taken VERBATIM from the 'qwen' block above, where all
    # the measurements that justify it live (MTP n-max 3, q4_0 cache, -ub 2048,
    # top-k 20). Retuned on 2026-08-27 like 'qwen': 120.90 -> 125.21 tok/s decode
    # (+3.6%) and 3,210 -> 3,354 tok/s prefill (+4.5%), measured separately on
    # both profiles rather than transposed. Do not re-derive anything here: only
    # the weight paths differ.
    #
    # Two file-level differences from 'qwen', and they are the only ones:
    #  - standard Q5_K_M (18.19 GiB) rather than UD-Q5_K_XL (18.8 GiB): no
    #    abliterated repository publishes a Dynamic quant. Slightly smaller, so
    #    the VRAM budget holds as is. Q6_K (20.89 GiB) would push the total to
    #    30.4 GB, above the ~29 GB where throughput collapses on this card:
    #    rejected.
    #  - the repository publishes the quant WITH the MTP head (the -noMTP-
    #    variants are 0.28 GiB smaller), so --spec-type draft-mtp has something
    #    to work with. Re-prove this on every change by checking for draft_n in
    #    the timings of a response.
    Start-LLM 'qwenu' @(
      '-m',"$ModelsDir\qwen3.8-27b-uncensored\Qwen3.8-27B-Uncensored-Q5_K_M.gguf",
      '--mmproj',"$ModelsDir\qwen3.8-27b-uncensored\mmproj-Qwen3.8-27B-Uncensored-F16.gguf",
      '--spec-type','draft-mtp','--spec-draft-n-max','3',
      '--n-gpu-layers','99','--load-mode','mlock','--flash-attn','on',
      '--jinja',
      # Chat template SHARED with 'qwen', and that is a choice, not a shortcut.
      # The two GGUFs do not embed the same one: the aligned build carries a
      # 9,993-character version that merges multiple system messages and knows the
      # 'developer' role; the abliterated repository carries an earlier 8,952
      # character one that knows neither. Both raise the same
      # 'System message must be at the beginning' as soon as a system message
      # follows a user message, which agentic clients do mid-session. We therefore
      # load the derivative of the more recent one, already proven in production.
      # Consequence worth knowing: deleting the qwen3.8-27b directory breaks this
      # action.
      '--chat-template-file',"$ModelsDir\qwen3.8-27b\chat-template-system-anywhere.jinja",
      # 262144, NOT 524288. Same buffer-sizing trap documented in the 'qwen' block
      # above: llama.cpp caps the window at the GGUF's context_length but still
      # sizes its buffers on what was requested.
      '--host','0.0.0.0','--port','8080','--ctx-size','262144',
      '--parallel','1','-b','4096','-ub','2048',
      '--cache-type-k','q4_0','--cache-type-v','q4_0',
      # -cram 24576: prompt cache in host RAM, see the 'qwen' block for the
      # measurement. Carried over as part of the identical profile, not re-measured
      # on this quant.
      '-cram','24576',
      '--temp','1.0','--top-p','0.95','--top-k','20','--min-p','0'
    )
  }

  'qwenf' {
    # CANDIDATE, not in service. DavidAU's Qwen3.8-27B TURBO Fable Cold-Fusion
    # Heretic NEO-CODER-MAX, fetched 2026-09-19 to be benched against 'qwenu',
    # the profile it would replace. A merge of a Qwen3.6-27B lineage (Fable
    # Fusion 711 Heretic) with a retrained Qwen3.8-27B (Cold-Fusion GAIN),
    # de-censored by ablation. Its card claims half to a tenth of the thinking
    # tokens; that claim and its ARC figures are what the bench is for.
    #
    # The profile is 'qwenu' verbatim on purpose, down to the build, the quant
    # and the shared chat template: anything else and the bench measures the
    # setting, not the model. Q5_K_M with MTP (19.73 GiB), NOT Q6_K (22.38 GiB):
    # 'qwenu' already rejected Q6_K for crossing the ~29 GB where throughput
    # collapses on this card, and this Q5_K_M is 1.5 GiB heavier than its own.
    # Check VRAM at load before trusting any speed figure.
    Start-LLM 'qwenf' @(
      '-m',"$ModelsDir\qwen3.8-27b-turbo-fcf\Qwen3.8-27B-TurboFCFusion-735-882-Here-Uncen-NEO-CODER-MAX-MTP-Q5_K_M.gguf",
      '--mmproj',"$ModelsDir\qwen3.8-27b-turbo-fcf\mmproj-F16.gguf",
      # MTP tensors are Q8_0 in this repository. The author warns that below 50%
      # acceptance the plain quant is faster: read draft_n and draft_n_accepted.
      '--spec-type','draft-mtp','--spec-draft-n-max','3',
      '--n-gpu-layers','99','--load-mode','mlock','--flash-attn','on',
      '--jinja',
      # Same template as 'qwenu'. The embedded one is the author's, whose default
      # reasoning_effort is 'xhigh'; loading it would compare two templates as
      # much as two models.
      '--chat-template-file',"$ModelsDir\qwen3.8-27b\chat-template-system-anywhere.jinja",
      '--host','0.0.0.0','--port','8080','--ctx-size','262144',
      '--parallel','1','-b','4096','-ub','2048',
      '--cache-type-k','q4_0','--cache-type-v','q4_0',
      '-cram','24576',
      '--temp','1.0','--top-p','0.95','--top-k','20','--min-p','0'
    )
  }

  'qwent' {
    # CANDIDATE, not in service. DavidAU's TWIN-TURBO 709-L, fetched 2026-09-19
    # to be benched against 'qwenf': the same merge, retrained once more for
    # shorter thinking (the card claims down to a twentieth) and five instruct
    # modes, at an ARC-C the author puts 2.6 points lower (0.709 vs 0.735).
    # "L" is the lighter de-censoring; the ULTRA-HERETIC sibling loses another
    # point on ARC-C and was left out.
    #
    # The profile is 'qwenf' verbatim, so only the weights differ. NEO-MAX MTP
    # Q5_K_M, 21,182,281,216 bytes, eight more than qwenf's file: same layout
    # (MAX = output tensor in 16 bit, MTP tensors in Q8_0), same VRAM footprint.
    # The author's calibration here is the generic NEO, not qwenf's CODER one.
    # The embedded template adds '{REASON:xxx}' switches typed in the message;
    # the shared template does not know them, so the bench measures the default
    # modes only. The author's templates sit next to the weights.
    Start-LLM 'qwent' @(
      '-m',"$ModelsDir\qwen3.8-27b-twin-turbo-709l\Qwen3.8-27B-TTURBO-Fable-C-Fusion-709-L-Uncen-NM-DAU-NEO-MAX-MTP-Q5_K_M.gguf",
      '--mmproj',"$ModelsDir\qwen3.8-27b-twin-turbo-709l\mmproj-F16.gguf",
      '--spec-type','draft-mtp','--spec-draft-n-max','3',
      '--n-gpu-layers','99','--load-mode','mlock','--flash-attn','on',
      '--jinja',
      '--chat-template-file',"$ModelsDir\qwen3.8-27b\chat-template-system-anywhere.jinja",
      '--host','0.0.0.0','--port','8080','--ctx-size','262144',
      '--parallel','1','-b','4096','-ub','2048',
      '--cache-type-k','q4_0','--cache-type-v','q4_0',
      '-cram','24576',
      '--temp','1.0','--top-p','0.95','--top-k','20','--min-p','0'
    )
  }

  'whittle' {
    # CANDIDATE, not in service. logic65's Whittle-Qwen-3.8-35B-A3B, fetched 2026-09-19. A
    # one-person distillation of Qwen3.8-27B (1,840 thinking traces, 3.3 h on one GPU) into a
    # 'qwen4exp' MoE: 180 experts, 8 active, ~3B active parameters, plus a 10B n-gram memory
    # table the body depends on. The student cannot out-reason its teacher, the base of 'qwenu':
    # what it can bring is 'tiel'-class speed. That is the question the bench answers. Its own
    # card calls it a research preview, 2,861 steps on maths and code-review traces only, and
    # reports 46/60 on MATH levels 2-4 and 44/50 on GSM8K, served Q8_0, thinking on.
    #
    # Context: the file declares 262144 and this profile keeps it for parity with the others,
    # but the author tested reading only up to 75k (5/6 there). Nothing past 75k is known.
    #
    # Q6_K (27.27 GiB, SHA-256 checked against Hugging Face). The memory table stays in host RAM
    # as the author prescribes: it is read one row per token per head, so the GPU holds the
    # body only and stays under the ~29 GB where throughput collapses on this card.
    Start-LLM 'whittle' @(
      '-m',"$ModelsDir\whittle-qwen3.8-35b-a3b\Whittle-Qwen-3.8-35B-A3B-Q6_K.gguf",
      '-ot','per_layer_token_embd=CPU',
      '--n-gpu-layers','99','--load-mode','mlock','--flash-attn','on','--jinja',
      # Embedded template (7,764 chars): it knows enable_thinking, not reasoning_effort, and it
      # raises 'System message must be at the beginning', so it breaks Claude Code but not the
      # bench. No MTP head in this model.
      '--host','0.0.0.0','--port','8080','--ctx-size','262144',
      '--parallel','1','-b','4096','-ub','2048',
      '--cache-type-k','q4_0','--cache-type-v','q4_0',
      '-cram','24576',
      # The card's sampler. It warns that greedy decoding loops on this family, and both benches
      # run at temperature 0: count the loops before reading a score.
      '--temp','0.7','--top-p','0.8','--top-k','20','--min-p','0','--repeat-penalty','1.05'
    )
  }

  'tiel' {
    # Tiel-Coder-35B-A3B (MIT). Sparse MoE, architecture 'qwen35moe': 41 blocks,
    # 256 experts, 8 active per token, so ~3B of 35B parameters do the work.
    # general.name in the GGUF is 'Ornith-1.5-35B': this is a requantisation of
    # Ornith-1.5-35B-A3B with an MTP head added, not a separate model.
    Start-LLM 'tiel' @(
      '-m',"$ModelsDir\tiel-coder-35b-a3b\Tiel-Coder-35B-A3B-MTP-UD-Q4_K_XL.gguf",
      '--mmproj',"$ModelsDir\tiel-coder-35b-a3b\mmproj-BF16.gguf",
      # Measured 2026-09-01 against the 'qwen' profile, same 37,981-token prompt,
      # seed 42, three runs, median. Quality on a fresh 500-question MMLU set
      # spanning 25 subjects plus 60 GSM8K problems, temperature 0, both models
      # on the identical set:
      #   decode ..... 104.53 -> 161.43 tok/s   (+54.4%)
      #   prefill .... 4,264  -> 8,616  tok/s   (x2.02)
      #   VRAM ....... 30,952 -> 29,465 MB
      #   MMLU ....... 82.0%  -> 82.2%          (one question in five hundred)
      #   GSM8K ...... 52/60  -> 58/60
      # The gain is structural, not a setting: far fewer bytes reread per token,
      # which is exactly the bandwidth ceiling this machine runs into. Note that
      # MTP acceptance is WORSE than qwen's, 27.2% against 38.7%, and the model
      # still wins by half again: acceptance rate does not predict throughput.
      # n-max 2 since 2026-09-03, not 4. Swept on this model, production flags otherwise, one
      # 38,000-token prompt of real llama.cpp sources, fixed seed, 3 runs, median decode:
      #   n-max 2 ... 184.8 tok/s, acceptance 46.9%
      #   n-max 3 ... 181.9 tok/s, acceptance 40.1%
      #   n-max 4 ... 156.8 tok/s, acceptance 27.2%   <-- was in production
      #   n-max 6 ... 133.3 tok/s, acceptance 18.4%
      # Raising --spec-draft-p-min to 0.40 or 0.60 lifts acceptance to 50-66% and HALVES throughput.
      # Acceptance is not the criterion, throughput is. Measured at short context only; the
      # 2026-08-31 lesson on qwen (ranking inverts at full context) has not been replayed here.
      '--spec-type','draft-mtp','--spec-draft-n-max','2',
      '--n-gpu-layers','99','--load-mode','mlock','--flash-attn','on','--jinja',
      # BACK TO --parallel 1 on 2026-09-08, after two days at 2 slots with --kv-unified.
      #
      # Two slots share ONE pool of 393216 tokens, so an honest client may only fill half of it,
      # and the launcher has to announce that half. That is fatal to the way this box is actually
      # used: agents spawned inside a single session each hit the halved ceiling and compact their
      # context away long before the model would have run out. Guillaume's words, the day it bit:
      # "ca arrete pas de niquer mes sous agents". A slot that makes a caller compact early is
      # worse than a caller that waits its turn.
      #
      # So: one slot, the whole 393216 for whoever holds it, concurrent callers queue. The cost is
      # real and accepted, a second caller waits instead of running at half speed.
      #
      # What the two-slot measurement of 2026-09-06 established, kept because it stays true if the
      # question is ever reopened: 260 tok/s on one stream, 202 + 188 on two, 97 to 104 each on
      # four with the card saturated. The prefill is what hurts: while one slot reads a 40k prompt
      # (4 to 5 s), the other's generation falls to 15 to 50 tok/s. And --kv-unified is mandatory
      # the moment --parallel exceeds 1, since without it llama-server splits -c between slots
      # while /props still announces the total.
      '--host','0.0.0.0','--port','8080','--parallel','1',
      # The GGUF declares context_length 262144 and llama.cpp caps on the file,
      # not on --ctx-size. This override is the ONLY lock, same as on muse, and
      # no YaRN flag is needed. Recall verified 2026-09-01 by needle-in-haystack
      # at 269,274 then 378,540 tokens, needle at 10/50/90% depth, 6 hits out of
      # 6. VRAM then sits at 31,617 MB of 32,607: about 990 MB of headroom, NOT
      # yet exercised with an image on input while the projector is loaded.
      '--override-kv','qwen35moe.context_length=int:393216',
      '--ctx-size','393216',
      '-b','4096','-ub','2048',
      '--cache-type-k','q4_0','--cache-type-v','q4_0',
      '-cram','24576',
      # temp 0.3 since 2026-09-10, down from 0.6, down from 1.0 before that. Ornith's model card
      # recommends 0.6 for general use and reserves 1.0 for reproducing its benchmarks, so 0.3 is
      # below what the authors document. It was set on the box by hand, as a trial on real usage,
      # and read back from there into this file. Do NOT go to 0: Qwen documents that greedy
      # decoding on these weights degrades quality and produces endless repetitions.
      '--temp','0.3','--top-p','0.95','--top-k','20','--min-p','0'
    )
  }

  'ornith' {
    # Ornith-1.5-9B (MIT). Dense 9B, the small sibling of the 'tiel' backbone.
    # Best capability-per-byte of the parc: 12,441 MB of VRAM, a third of the
    # others, leaving room to run something else alongside.
    Start-LLM 'ornith' @(
      '-m',"$ModelsDir\ornith-1.5-9b\Ornith-1.5-9B-Q5_K_M.gguf",
      '--mmproj',"$ModelsDir\ornith-1.5-9b\mmproj-Ornith-1.5-9B-BF16.gguf",
      # Measured 2026-09-01, same protocol and same question set as 'tiel':
      #   decode ..... 167.73 tok/s
      #   prefill .... 10,497 tok/s
      #   VRAM ....... 12,441 MB
      #   MMLU ....... 73.0%   (against 82.0% for qwen: it knows much less)
      #   GSM8K ...... 53/60   (against 52/60 for qwen, a 27B model)
      # Read that pair the right way: equal reasoning, far less knowledge. This
      # is a fast second-opinion and short-task model, not a replacement.
      #
      # Speculation on since 2026-09-08, and the draft head is NOT a new file: these
      # very weights carry blk.32.nextn.* tensors, which llama-server logs as
      # 'unused tensor ... -- ignoring' and drops whenever --spec-type is absent.
      # It had been running that way since the profile was created. Measured the
      # same day, bench.ps1, 38,000-token prompt, seed 42, 3 runs, median:
      #   decode ..... 167.18 -> 179.64 tok/s   (+7.5%, acceptance 58.4%)
      #   prefill .... 10,721 -> 7,715  tok/s   (-28%)
      #   VRAM ....... 11,648 -> 14,179 MB      (+2,531, projector excluded)
      # Taken because this profile answers short questions, where the head earns
      # its memory, and gives up prefill it rarely uses. The published
      # Ornith-1.5-9B-MTP-BF16-ASHQ1-6500 file, fetched to answer this same
      # question, is SLOWER than these weights at 164.25 tok/s: it was not kept.
      '--spec-type','draft-mtp','--spec-draft-n-max','2',
      '--n-gpu-layers','99','--load-mode','mlock','--flash-attn','on','--jinja',
      '--host','0.0.0.0','--port','8080','--parallel','1','--ctx-size','262144',
      '-b','4096','-ub','2048',
      '--cache-type-k','q4_0','--cache-type-v','q4_0',
      '-cram','24576',
      # temp 0.6 since 2026-09-10, not 1.0. Set on the box by hand and read back from there. The
      # model card reserves 1.0 for reproducing benchmarks and recommends 0.6 for general use,
      # which is what this profile actually serves.
      '--temp','0.6','--top-p','0.95','--top-k','20','--min-p','0'
    )
  }

  'kat' {
    # KAT-Coder-V2.5-Dev-35B-A3B, abliterated, requantised with an MTP head by
    # jakeroxs. Same 'qwen35moe' architecture and the same 3B-of-35B sparsity as
    # 'tiel', which is why it inherits that profile's arguments unchanged.
    #
    # On TRIAL since 2026-09-08, it replaces nothing. It is the only one of the
    # four candidates benched that day to match 'tiel' everywhere and beat it
    # where this box does its work, and the trial is what decides whether it
    # takes over. Same protocol as 'tiel', b10826, projector on neither side:
    #   decode, 38k prompt ..... 197.71 tok/s against 199.71 for tiel
    #   prefill ................ 8,072  against 8,758
    #   VRAM ................... 29,702 MB against 30,936
    #   MTP accepted ........... 52.8% against 56.2%
    #   writing PowerShell ..... 295.1 tok/s against 284.3
    #   four hand-scored tasks . 4/4, same as tiel
    # Running it STOPS 'tiel': one model at a time on this card, and 29.7 GB
    # leaves no room for a second. This is an alternation, not a coexistence.
    #
    # No projector is published for these weights, so this profile is text only,
    # unlike 'tiel' which loads mmproj-BF16. Anything sending an image must stay
    # on 'tiel'.
    Start-LLM 'kat' @(
      '-m',"$ModelsDir\kat-coder-v25-35b-a3b-mtp\KAT-Philly-MTP-Q4_K_M.gguf",
      # n-max 2 carried over from 'tiel' without a sweep of its own. The 2026-09-03
      # sweep that settled that value was run on the other weights; re-run it here
      # before reading anything into this model's acceptance rate.
      '--spec-type','draft-mtp','--spec-draft-n-max','2',
      '--n-gpu-layers','99','--load-mode','mlock','--flash-attn','on','--jinja',
      # One slot, like tiel and for the same reason: a shared pool forces the launcher to announce
      # half a window, which makes a session's agents compact early. See the tiel block.
      '--host','0.0.0.0','--port','8080','--parallel','1',
      '--override-kv','qwen35moe.context_length=int:393216',
      '--ctx-size','393216',
      '-b','4096','-ub','2048',
      '--cache-type-k','q4_0','--cache-type-v','q4_0',
      '-cram','24576',
      # temp 0.3 since 2026-09-10, not 0.6. Set on the box by hand alongside tiel, same trial,
      # and read back from there. Never 0 on these weights, see the tiel block.
      '--temp','0.3','--top-p','0.95','--top-k','20','--min-p','0'
    )
  }

  # -------------------------------------------------------------------------
  # The three candidates installed on 2026-09-10. NOTHING below this comment has
  # been measured on this box: the figures in each block are budgets computed from
  # the architecture, not readings. Treat every one of them as a hypothesis until
  # the 110-question MMLU plus 60 GSM8K bench has run.
  # -------------------------------------------------------------------------
  'nex' {
    # Nex-N2.5-mini, architecture 'qwen3_5_moe': 40 blocks, hybrid attention mixing
    # Gated DeltaNet linear layers with 10 full-attention layers. Weights are
    # mradermacher's i1-Q4_K_M, quantised with a published importance matrix.
    Start-LLM 'nex' @(
      '-m',"$ModelsDir\nex-n2.5-mini\Nex-N2.5-mini.i1-Q4_K_M.gguf",
      '--mmproj',"$ModelsDir\nex-n2.5-mini\mmproj-Nex-N2.5-mini-F16.gguf",
      # NO speculation here, and that is not an oversight. config.json declares
      # mtp_num_hidden_layers 1, but the safetensors index carries zero MTP tensors and
      # exactly 40 layers, 0 to 39: the head is announced and not shipped. Verified at the
      # source on 2026-09-09. Should the loader trip on that declaration, the workaround is
      #   --override-kv qwen35moe.block_count=int:40,qwen35moe.nextn_predict_layers=int:0
      # NO speculation, and this is a measured conclusion, not an omission.
      # Measured 2026-09-10, 45k-token prompt through /v1/chat/completions, seed 42,
      # 3 runs, median decode:
      #   no speculation .......... 215.1 tok/s
      #   ngram-cache n-max 4 ..... 110.4 tok/s   <-- HALF
      #   draft-dflash + drafter .. see below
      #
      # ngram-cache was briefly put in this profile on the strength of 408.8 against
      # 213.1, measured through /completion on a raw block of code. That number was real
      # and it was useless: completing code means literally repeating structures already
      # in the buffer, which is the one case pattern-guessing wins. Ask the same model to
      # answer a question and every guess is rejected, and every rejected guess is paid
      # for. The bench has to look like the use, or it measures the bench.
      #
      # Same verdict on tiel, which has a real MTP head: 230.4 tok/s with it, 198.3 with
      # nothing, 103.8 with ngram-cache instead. And stacking ngram-cache ON TOP of MTP
      # gives 147.6, because --spec-type accumulates and the two fight over the same
      # candidates.
      #
      # The DFlash drafter, measured on the raw endpoint, reached 316.7 tok/s but divided
      # prefill by three and cost 4.6 GiB: its 392 MiB file drags a KV cache sized for the
      # whole 262,144 window, leaving 487 MiB of headroom. Not retested on chat because
      # the memory cost alone rules it out here.
      '--n-gpu-layers','99','--load-mode','mlock','--flash-attn','on','--jinja',
      '--host','0.0.0.0','--port','8080','--parallel','1',
      # No --override-kv on the window: this GGUF already declares context_length 262144,
      # unlike tiel whose file caps lower than the profile asks for.
      '--ctx-size','262144',
      '-b','4096','-ub','2048',
      # q8_0 and not q4_0, unlike the production profiles. The budget allows it: 10 full
      # layers, 2 KV heads, head_dim 256 cost about 10 KiB per token in q8_0, so roughly
      # 2,700 MiB of cache at 262,144 tokens, against 20,180 MiB of weights and 860 of
      # projector. About 24 GiB in all, comfortably inside 32. Drop to q4_0 only if the
      # measured figure says otherwise.
      '--cache-type-k','q8_0','--cache-type-v','q8_0',
      '-cram','24576',
      # KNOWN DEFECT, llama.cpp issue 27931, open: the server crashes on this family of
      # hybrid recurrent models with a projector loaded when text and image turns ALTERNATE
      # in one conversation. A single image in one turn may well pass. The proposed fix,
      # pull request 28007, is not merged as of 2026-09-10.
      #
      # 0.7 and top-k 40 come from the model card, which is explicit: "For the best
      # generation quality, we recommend temperature 0.7, top_p 0.95, top_k 40". This
      # profile ran at 0.6 and top-k 20 for its first hours because it was written by
      # copying the shape of the Qwen profiles without opening this model's card.
      '--temp','0.7','--top-p','0.95','--top-k','40','--min-p','0'
    )
  }

  'spark' {
    # Spark-X2.5-4B, architecture 'spark2_5': hybrid attention, most layers on a 512-token
    # sliding window, 9 full-attention layers. Supported upstream since pull request 27868,
    # in build b10828; the fork the model card sends you to compile is obsolete, 15 commits
    # ahead all merged upstream and 385 behind. Do not compile it.
    Start-LLM 'spark' @(
      '-m',"$ModelsDir\spark-x2.5-4b\Spark-X2.5-4B-Q8_0.gguf",
      '--n-gpu-layers','99','--load-mode','mlock','--flash-attn','on','--jinja',
      '--host','0.0.0.0','--port','8080','--parallel','1',
      # 262,144 rather than the 64k asked for, because the room is there and a window costs
      # nothing until it is filled. Counter-intuitive figure worth keeping: this 4B model
      # costs MORE cache per token than the 35B one, 9 full layers with 4 KV heads at
      # head_dim 256 against 10 layers with 2 heads. About 18 KiB per token in q8_0, so
      # roughly 4,800 MiB of cache at full window on top of 4,170 MiB of weights.
      '--ctx-size','262144',
      '-b','4096','-ub','2048',
      '--cache-type-k','q8_0','--cache-type-v','q8_0',
      '-cram','24576',
      # NEVER --swa-full on this one: it would drop the sliding-window saving that makes the
      # cache affordable and hold every layer at full width.
      #
      # top-k 0, meaning the filter is OFF, and that is what this model asks for: its
      # generation_config.json carries top_k -1 with temperature 1.0. It ran at 0.6 and
      # top-k 20 for its first hours, bridled by a profile copied from the Qwen models
      # without opening its own configuration. Temperature stays at 0.6 rather than the
      # published 1.0 until the sampling sweep says otherwise, since 1.0 is what publishers
      # quote for reproducing their own benchmarks more often than for daily use.
      '--temp','0.6','--top-p','0.95','--top-k','0','--min-p','0'
    )
  }

  'bonsai' {
    # Ternary-Bonsai-27B, a ternary quantisation of Qwen3.6-27B, weights in {-1, 0, +1} with
    # group-wise FP16 scaling, about 1.71 bits per weight. 27B parameters in 7.06 GiB.
    Start-LLM 'bonsai' @(
      # Q2_g64 and NOT PQ2_0. The two files are the same model: PQ2_0 is packed for PrismML's
      # own fork of llama.cpp, Q2_g64 is the variant meant for upstream builds, and this
      # profile stays on upstream because its file exists. The fork was read at install time
      # as a compilation to maintain; that was wrong, it publishes binary archives, and it
      # now serves 'bonsai2' below. See models/ternary-bonsai-27b/README.md.
      '-m',"$ModelsDir\ternary-bonsai-27b\Ternary-Bonsai-27B-Q2_g64.gguf",
      '--mmproj',"$ModelsDir\ternary-bonsai-27b\Ternary-Bonsai-27B-mmproj-BF16.gguf",
      # DSpark, the model's own semi-autoregressive drafter, announced by its authors at
      # 1.34x. EXPECT THIS TO FAIL: llama.cpp issue 26337 reports the drafter refusing to
      # load on an inconsistent tensor offset, 'dspark.fc.weight has offset 337718592,
      # expected 357584192'. Open, never confirmed by a maintainer, never fixed, reported on
      # b10197 and untested on b10883. If it does fail, drop these three flags and the model
      # still serves; only the speed claim goes.
      # The three DSpark flags were REMOVED on 2026-09-10 after the failure predicted by
      # llama.cpp issue 26337 happened here: the profile was loaded during the bench
      # campaign and never answered, 420 seconds, no health, the runner moved on. Loaded
      # again with -NoSpec, it came up and served at 102.6 tok/s. The drafter was the
      # whole problem. The file stays on disk at
      #   D:\models\ternary-bonsai-27b\Ternary-Bonsai-27B-dspark-Q4_1.gguf
      # for the day the defect is fixed; the flags to put back are
      #   --spec-type draft-dspark --spec-draft-n-max 2 -md <that file>
      #
      # Do not reach for ngram-cache as a replacement: measured on nex and tiel the same
      # day, it HALVES decode on conversational use. See the nex block.
      '--n-gpu-layers','99','--load-mode','mlock','--flash-attn','on','--jinja',
      # Added 2026-09-18, and it should have been here from the start: this profile could never
      # answer Claude Code. Its template raises 'System message must be at the beginning.' and
      # the server returns 500 on the first request, because Claude Code puts system turns in
      # the middle of a conversation. The bench campaign of 2026-09-10 never saw it, having
      # driven the raw endpoint rather than a client. Same one-line fix as the other profiles,
      # applied to this model's own template.
      '--chat-template-file',"$ModelsDir\ternary-bonsai-27b\chat-template-system-anywhere.jinja",
      '--host','0.0.0.0','--port','8080','--parallel','1',
      '--ctx-size','262144',
      '-b','4096','-ub','2048',
      # q8_0 like the other two candidates. The cache budget here is the LEAST certain of the
      # three: the backbone is announced as roughly three quarters linear attention, but the
      # exact count of full layers and KV heads has not been read out of the file. Weights,
      # projector and drafter together already sit near 9,975 MiB.
      '--cache-type-k','q8_0','--cache-type-v','q8_0',
      '-cram','24576',
      # 0.7 and not 0.6: this is what the model card gives for its own benchmark runs, and
      # unlike the Qwen profiles there is no second recommended value for general use.
      '--temp','0.7','--top-p','0.95','--top-k','20','--min-p','0'
    )
  }

  'bonsai2' {
    # Bonsai 2 27B, the successor to 'bonsai' and a different base model: Qwen3.8-27B where the
    # first one was Qwen3.6-27B. Same ternary idea, {-1, 0, +1} with FP16 scaling per group of
    # 128, but the weights are now stored in a Hadamard-rotated basis, which is exactly what
    # upstream llama.cpp cannot undo. Hence the fork. Installed 2026-09-18.
    Start-LLM 'bonsai2' @(
      # PQ2_0 and NOT PTQ1_0, on this card and no other. The two packs are a real trade and the
      # model card measures both on an RTX 5090: PQ2_0 decodes at 129.9 tok/s against 120.5, and
      # processes prompts at 3893 against 1805, more than twice as fast. PTQ1_0 wins on the Ada
      # parts and the L4, where memory is the binding constraint; here it is not. The price is
      # 1.26 GB more on disk and in VRAM.
      '-m',"$ModelsDir\ternary-bonsai-2-27b\Ternary-Bonsai-2-27B-PQ2_0.gguf",
      '--mmproj',"$ModelsDir\ternary-bonsai-2-27b\Ternary-Bonsai-2-27B-mmproj-BF16.gguf",
      # No drafter at all, unlike 'bonsai'. Bonsai 2's repository ships none, and Bonsai 1's does
      # not transfer: measured 2026-09-18, its published bf16 drafter converted with
      # gguf-dspark-to-dflash against THIS model as tokenizer donor loads and runs, and drafts
      # nothing useful. Six tokens accepted out of 2,028, 0.3%, decode halved to 44.9 tok/s from
      # 98.3 and 10,780 MiB spilled into shared memory. The demo repository says drafters are
      # target-specific; this is what that costs when you do not believe it.
      #
      # BLACKWELL INVESTIGATION, 2026-09-18. Measured decode of 97.2-103.4 tok/s here against the
      # model card's 129.9 tok/s looked like a 25% loss to chase. It is not a configuration defect:
      # `cuobjdump --list-elf` on this build's ggml-cuda.dll lists sm_120a and sm_121a cubins
      # alongside sm_86/sm_89, and the card reports compute_cap 12.0, so the native Blackwell
      # kernels are already in use. Cut the bench prompt from 56k to roughly 500 tokens and decode
      # rose to 130.4 tok/s, matching the card's figure almost exactly; a live /v1/chat/completions
      # call with a 105-token prompt independently measured 129.26 tok/s. The model card's number
      # comes from llama-bench at "batch size 1 and depth 0, no vision tower", i.e. decode from a
      # near-empty context. This profile serves 262,144 tokens of context with the mmproj loaded,
      # and full attention over a long KV cache is the more expensive regime. The gap is structural,
      # not a missing flag: three trials on the full-length prompt, three runs each, gained nothing
      # outside noise: `--no-cont-batching` gave 102.6 against 103.2 baseline, `-ub 4096` gave 102.8
      # at the cost of 1,416 MiB more VRAM and 1,104 MiB more shared-memory spill, and
      # `--no-mmproj-offload` gave 102.6 while only freeing 1.1 GB of VRAM. None kept. See
      # docs/tuning-log.md, 2026-09-18 entry, for the raw numbers.
      '--n-gpu-layers','99','--load-mode','mlock','--flash-attn','on','--jinja',
      # THE THIRD TIME THE SAME DEFECT HAS HAD TO BE WORKED AROUND HERE. Claude Code puts system
      # turns in the middle of a conversation; this model's own template raises
      # 'System message must be at the beginning.' and the server answers 500 before generating
      # anything. Measured 2026-09-18 with `claude -p`, which failed on its first request.
      #
      # The file is this model's OWN template with exactly one line changed: where it raised, it
      # now emits a system block. Not the qwen3.8-27b file two profiles up, although Bonsai 2
      # derives from that base and the two templates are nearly identical: that one is Unsloth's
      # rework and carries changes of its own (a developer role, tool-call argument validation,
      # high mapped onto xhigh) that this model never declared. Sharing it would import them
      # silently.
      '--chat-template-file',"$ModelsDir\ternary-bonsai-2-27b\chat-template-system-anywhere.jinja",
      '--host','0.0.0.0','--port','8080','--parallel','1',
      '--ctx-size','262144',
      '-b','4096','-ub','2048',
      # q8_0 and not q4_0, which the upstream demo offers as BONSAI_KV4 for long contexts.
      # Measured here on 2026-09-18, same bench, same prompt: q4_0 frees 4,098 MiB of VRAM and
      # changes nothing else, 98.3 tok/s against 97.2 and identical ingestion. It buys memory
      # this box does not need, 12 GB being free either way and 262,144 already the model's
      # ceiling, and its own page warns that the K cache loses a little accuracy without a
      # calibration bias that has to be built and kept in step with the weights.
      '--cache-type-k','q8_0','--cache-type-v','q8_0',
      '-cram','24576',
      # 1.0 and not the 0.7 of the first Bonsai. The card gives two sets and says which is which:
      # these are the thinking-mode values, and thinking mode is what produced its published
      # figures. The model thinks by default at 'xhigh' effort and its authors state that 'low'
      # is not supported, behaving close to xhigh when asked for.
      '--temp','1.0','--top-p','0.95','--top-k','20','--min-p','0'
    )
  }

  'embed' {
    Start-LLM 'embed' @(
      '-m',"$ModelsDir\nomic-embed-text-v1.5\nomic-embed-text-v1.5.Q8_0.gguf",
      '--embeddings','--pooling','mean',
      # No --load-mode here: 'embed' is served by the frozen turboquant build
      # (2026-04-07), which predates that flag and dies on it with
      # 'invalid argument: --load-mode'. Verified 2026-09-05.
      '--n-gpu-layers','99','--flash-attn','on',
      '--host','0.0.0.0','--port','8080','--ctx-size','131072',
      '--parallel','1','-b','2048','-ub','2048'
    )
  }
}
