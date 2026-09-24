param(
    [string]$Repo,
    [string]$RepoFile,
    [string]$Dest,
    [string]$Expected
)
# Helper invoked by fetch-models.sh over ssh. Downloads one file from a
# Hugging Face repo, verifies it against the SHA-256 the caller expects, and
# refuses to leave a wrong file under its final name.
#
# Uses the "hf" CLI, not "huggingface-cli": the station's installed
# huggingface-cli (0.0.0.0, under C:\Python313\Scripts) prints a deprecation
# warning and downloads nothing, confirmed live on 2026-09-23. "hf" is the
# CLI that actually works on this station.
$ErrorActionPreference = "Stop"
try {
    New-Item -ItemType Directory -Force -Path (Split-Path $Dest) | Out-Null
    $cli = Get-Command hf -ErrorAction SilentlyContinue
    if ($cli) {
        $destDir = Split-Path $Dest
        & hf download $Repo $RepoFile --local-dir $destDir
        $downloaded = Join-Path $destDir $RepoFile
        if ($downloaded -ne $Dest -and (Test-Path $downloaded)) {
            Move-Item -Force $downloaded $Dest
        }
    } else {
        # Concatenation, not string interpolation: tested live on 2026-09-24
        # with a Repo/RepoFile value containing "$HOME", "$(whoami)" and a
        # backtick, through the exact ssh -> cmd.exe -> "powershell -File"
        # path this script is invoked from, and neither form expanded
        # anything (PowerShell -File argument binding does not re-parse the
        # bound value as code). Concatenation removes any doubt regardless.
        $uri = "https://huggingface.co/" + $Repo + "/resolve/main/" + $RepoFile
        Invoke-WebRequest -Uri $uri -OutFile $Dest
    }
    $hash = (Get-FileHash -Algorithm SHA256 $Dest).Hash.ToLower()
    if ($hash -ne $Expected) {
        Rename-Item $Dest ($Dest + ".bad")
        Write-Output ("HASH MISMATCH got " + $hash + " expected " + $Expected)
        exit 1
    } else {
        Write-Output ("OK " + $Dest + " " + $hash)
    }
} catch {
    Write-Output ("ERR " + $_.Exception.Message)
    exit 1
}
