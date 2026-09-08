param(
  [ValidateSet('embed','kat','muse','ornith','qwen','qwenu','tiel','stop','status','logs')]
  [string]$Action,
  [string]$Name,  # optional: for 'stop' and 'logs', targets a named instance
  [int]$Tail = 40 # for 'logs': history lines to show before following live
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
$instDir   = "$RootDir\instances"
New-Item -ItemType Directory -Force -Path $instDir | Out-Null

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
  qwen   = @{ Exe = $exeNew;      WorkDir = $workDirNew;      CudaBin = $cudaBinUp }
  tiel   = @{ Exe = $exeB10826;   WorkDir = $workDirB10826;   CudaBin = $cudaBinUp }
  # ornith moved off the 2026-08-27 build on 2026-09-08. The move bought no speed, it was taken
  # because Ornith is Q5_K_M and never needed the NVFP4 kernels. Figures in docs/tuning-log.md.
  ornith = @{ Exe = $exeB10826;   WorkDir = $workDirB10826;   CudaBin = $cudaBinUp }
  kat    = @{ Exe = $exeB10826;   WorkDir = $workDirB10826;   CudaBin = $cudaBinUp }
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
# served requests: llm-out-<name>.log stays empty forever and is NOT the file to
# read. llm-err-<name>.log carries everything. This action exists so nobody has
# to remember that: it picks the log of the running instance and follows it.
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
  $errLog = "$RootDir\llm-err-$name.log"
  if (-not (Test-Path $errLog)) { Write-Output "NO_LOG $errLog not found"; return }
  Write-Output "TAILING name=$name file=$errLog (Ctrl+C to exit)"
  Get-Content $errLog -Tail $tail -Wait
}

function Start-LLM($name, $modelArgs, $cudaDevices = $null, $exePath = $null, $workDirPath = $null, $cudaBinPath = $null) {
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

  $outLog = "$RootDir\llm-out-$name.log"
  $errLog = "$RootDir\llm-err-$name.log"
  Clear-Content $outLog -ErrorAction SilentlyContinue
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
  $inner = "cd /d `"$workDirPath`" && `"$exePath`" $quoted > `"$outLog`" 2> `"$errLog`""
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
      # 1M context. ONE lock, and it is not YaRN: llama.cpp caps the slot on the
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
      # --parallel 2 --kv-unified since 2026-09-06: two workstations call this model. With
      # --kv-unified the 393216 window is ONE shared pool, not 2 x 196608: without it llama-server
      # splits -c between slots while /props still announces the total.
      # Measured the same day on b10826, 400 tokens forced, pure generation:
      #   1 stream .... 260 tok/s
      #   2 streams ... 202 + 188 tok/s (390 total)
      #   4 streams ... 97 to 104 tok/s each (400 total, card saturated)
      # The cost is the prefill: while one slot reads a 40k prompt (4 to 5 s), the other's
      # generation drops to 15 to 50 tok/s. Four slots were rejected: no gain over two for two
      # callers, half the per-stream rate, and 580 MB of VRAM headroom under load.
      '--host','0.0.0.0','--port','8080','--parallel','2','--kv-unified',
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
      # temp 0.6 since 2026-09-06, not 1.0. Ornith's model card recommends 0.6 for general use and
      # reserves 1.0 for reproducing its benchmarks. Claude Code sends no temperature, so this value
      # is the one every session runs at. Trial on real usage; revert to 1.0 if nothing improves.
      '--temp','0.6','--top-p','0.95','--top-k','20','--min-p','0'
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
      '--temp','1.0','--top-p','0.95','--top-k','20','--min-p','0'
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
      '--host','0.0.0.0','--port','8080','--parallel','2','--kv-unified',
      '--override-kv','qwen35moe.context_length=int:393216',
      '--ctx-size','393216',
      '-b','4096','-ub','2048',
      '--cache-type-k','q4_0','--cache-type-v','q4_0',
      '-cram','24576',
      '--temp','0.6','--top-p','0.95','--top-k','20','--min-p','0'
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
