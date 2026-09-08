# Fetching model weights

Use the official client. On this workstation it is `hf.exe`, from `huggingface_hub` 1.27.0.

```powershell
hf download el4/Ornith-1.5-35B-A3B-ONYX-GGUF Ornith-1.5-35B-A3B-ONYX-compact.gguf `
            --local-dir D:\models\ornith-35b-onyx-compact
```

No environment variable is needed. `HF_HUB_ENABLE_HF_TRANSFER` is deprecated and now ignored: the
client transfers through Xet, which fetches deduplicated blocks from a CDN instead of streaming one
range per connection. `HF_XET_HIGH_PERFORMANCE` exists but was not set for the measurement below,
so the figure is the out-of-the-box one.

## Why this page used to say the opposite

Hugging Face does throttle per connection, which was measured on 2026-09-02: 1.66 MB/s on a single
stream against 7.55 MB/s on four parallel ranges. That observation was correct and led to an
in-house byte-range fetcher. It was still the wrong conclusion, because it optimised the slow path
instead of leaving it.

Measured 2026-09-08, same machine, same line, four minutes apart:

- in-house fetcher, six parallel ranges through `curl`: 21.27 GB in 58 min 48 s, **6.2 MB/s**
- `hf download`, Xet: 20.22 GB in 194 s, **106.8 MB/s**

Seventeen times faster. The in-house script was deleted the same day. The lesson is not about
download speed: a capability that looks missing is worth checking for in the tool that already ships
with the job, before reimplementing it.

## The one check the client does not do

Xet verifies what it transferred, but it does not know what a GGUF file is. After a fetch, read the
first four bytes and require them to be `GGUF`:

```powershell
$fs = [System.IO.File]::OpenRead($path)
$b = New-Object byte[] 4
$null = $fs.Read($b, 0, 4)
$fs.Close()
if ([Text.Encoding]::ASCII.GetString($b) -ne 'GGUF') { throw "entete non conforme : $path" }
```

A file of the right size that is an HTML error page reads as a perfectly healthy download
everywhere else.

## Measuring a transfer honestly

Two traps, both hit on 2026-09-08.

**A rate read over the first tens of megabytes is a lie.** An edge cache makes the start of a
transfer much faster than its sustained rate. A cumulative figure of 18.8 MB/s taken mid-run on the
in-house fetcher turned out to be 6.2 MB/s over the full transfer. Only total duration over a
complete transfer counts.

**File length is not progress when Xet is in play.** It writes blocks at their final offsets, so the
length of the incomplete file jumps ahead of what has actually arrived. Sampling it gave an
impossible 6 644 MB/s. Watch the client's own output, or the total duration, never the file size.
