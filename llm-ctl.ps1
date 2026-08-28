param(
  [ValidateSet('embed','muse','qwen','qwenu','stop','status','logs')]
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
# Three llama.cpp builds coexist on this box, on purpose. They are NOT
# interchangeable, and picking the wrong one is a silent failure.
#
#  - turboquant: a frozen custom fork (2026-04-07). Still serves 'embed'. Its
#    only reason to exist was the turbo3 cache quants of a model that has since
#    been removed, so it has no remaining technical justification and could be
#    retired once 'embed' is validated on upstream.
#  - upstream: official build of 2026-08-11. The only one of the two that knows
#    the muse-glimmer architecture. Serves 'muse' and 'qwenu'.
#  - dated build (2026-08-27): see below. Serves 'qwen', and only it can.
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

$instDir   = "$RootDir\instances"
New-Item -ItemType Directory -Force -Path $instDir | Out-Null

# embed/muse/qwen/qwenu all sit on port 8080 and are mutually exclusive on the
# GPU: starting one unloads the others.
$ports = @{ embed = 8080; muse = 8080; qwen = 8080; qwenu = 8080 }

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
  if ($procs) { $procs | Stop-Process -Force; Start-Sleep -Milliseconds 500; Write-Output "STOPPED all" }
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
      Write-Output "NO_INSTANCE no tracked instance is running. Pass -Name (embed/muse/qwen/qwenu)."
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
  if (-not $exePath)     { $exePath     = $exe }
  if (-not $workDirPath) { $workDirPath = $workDir }
  if (-not $cudaBinPath) { $cudaBinPath = $cudaBin }
  $port = $ports[$name]
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
  Start-Sleep -Seconds 2

  $outLog = "$RootDir\llm-out-$name.log"
  $errLog = "$RootDir\llm-err-$name.log"
  Clear-Content $outLog -ErrorAction SilentlyContinue
  Clear-Content $errLog -ErrorAction SilentlyContinue

  $quoted = ($modelArgs | ForEach-Object { Quote $_ }) -join ' '
  # CPU-only instances: hide the GPU to avoid a pointless CUDA init.
  $cudaSet = if ($null -ne $cudaDevices) { "set `"CUDA_VISIBLE_DEVICES=$cudaDevices`" && " } else { "" }
  $inner  = "${cudaSet}set `"PATH=$cudaBinPath;%PATH%`" && cd /d `"$workDirPath`" && `"$exePath`" $quoted > `"$outLog`" 2> `"$errLog`""
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
      '--n-gpu-layers','99','--no-mmap','--mlock','--flash-attn','on',
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
      '--override-kv','muse-glimmer.context_length=int:1048576',
      '--host','0.0.0.0','--port','8080','--ctx-size','1048576',
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
      '--cache-type-k','q8_0','--cache-type-v','q8_0',
      '--temp','1.0','--top-p','0.95','--top-k','64'
    ) $null $exeUp $workDirUp $cudaBinUp
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
      '--spec-type','draft-mtp','--spec-draft-n-max','3',
      '--n-gpu-layers','99','--no-mmap','--mlock','--flash-attn','on',
      '--jinja',
      # Chat template DERIVED from the embedded one, a single line changed. The
      # original raises 'System message must be at the beginning' as soon as a
      # system message arrives after a user message. Agentic clients inject those
      # mid-session, so this model failed with HTTP 500 on the very first turn
      # while /v1/chat/completions worked perfectly. The derived template renders
      # a late system message as an ordinary ChatML system turn, which the format
      # supports natively, instead of raising.
      '--chat-template-file',"$ModelsDir\qwen3.8-27b\chat-template-system-anywhere.jinja",
      # 262144, NOT 524288. The GGUF declares a context_length of 262144:
      # llama.cpp caps the window at that value, in a single log line, BUT it
      # still SIZES ITS BUFFERS on the requested value. We were paying the memory
      # cost of 512k while never having it. This is one notch beyond the known
      # capping trap, which was about the window and not about memory.
      # Measured 2026-08-28, identical model and flags, only --ctx-size changing:
      #   262144 requested ... VRAM 27.2 GB ... decode 123.03 tok/s ... prefill 4,241
      #   524288 requested ... VRAM 31.9 GB ... decode  99.76 tok/s ... prefill 2,462
      # 23% of decode and 72% of prefill lost for nothing, the window being the
      # same in both cases. Past ~29 GB the card throttles; see the q8_0 cache
      # figures below, it is the same wall. Only raise this with an --override-kv
      # that actually extends the window, and then re-prove recall by measurement.
      '--host','0.0.0.0','--port','8080','--ctx-size','262144',
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
      # Qwen thinking-mode calibration values. top-k is 20 here and 64 on muse.
      # --min-p 0 is also a calibration value: llama.cpp imposes 0.05 by default
      # when nothing sets it, which clips the tail of the distribution ON TOP OF
      # the already-calibrated top-p and top-k, with no message. Quality setting,
      # no measurable throughput effect.
      '--temp','1.0','--top-p','0.95','--top-k','20','--min-p','0'
    # Build of 2026-08-27, NOT the 2026-08-11 one: NVFP4 is ggml type 40, whose
    # CUDA kernels exist only in that build, compiled for sm_120.
    ) $null $exeNew $workDirNew $cudaBinUp
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
      '--mmproj',"$ModelsDir\qwen3.8-27b-uncensored\Qwen3.8-27B-Uncensored-vision-f16.gguf",
      '--spec-type','draft-mtp','--spec-draft-n-max','3',
      '--n-gpu-layers','99','--no-mmap','--mlock','--flash-attn','on',
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
      '--temp','1.0','--top-p','0.95','--top-k','20','--min-p','0'
    ) $null $exeUp $workDirUp $cudaBinUp
  }

  'embed' {
    Start-LLM 'embed' @(
      '-m',"$ModelsDir\nomic-embed-text-v1.5\nomic-embed-text-v1.5.Q8_0.gguf",
      '--embeddings','--pooling','mean',
      '--n-gpu-layers','99','--no-mmap','--mlock','--flash-attn','on',
      '--host','0.0.0.0','--port','8080','--ctx-size','131072',
      '--parallel','4','-b','2048','-ub','2048'
    )
  }
}
