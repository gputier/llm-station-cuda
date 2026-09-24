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
#
# hf transfers through Xet (over 100 MB/s, HF_XET_HIGH_PERFORMANCE set);
# without it the script stops rather than crawl on one HTTP connection.
# "hf download" truncates its target before writing, so a file already
# present with the right hash is never fetched again, and every download
# lands in a staging directory, moved onto Dest only after the hash check.
$ErrorActionPreference = "Stop"
try {
    New-Item -ItemType Directory -Force -Path (Split-Path $Dest) | Out-Null

    if (Test-Path $Dest) {
        $existingHash = (Get-FileHash -Algorithm SHA256 $Dest).Hash.ToLower()
        if ($existingHash -eq $Expected) {
            Write-Output ("OK " + $Dest + " " + $existingHash + " (deja present, non retelecharge)")
            exit 0
        }
    }

    $stagingDir = $Dest + ".staging"
    Remove-Item -Recurse -Force $stagingDir -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $stagingDir | Out-Null
    $staged = Join-Path $stagingDir (Split-Path $Dest -Leaf)

    $cli = Get-Command hf -ErrorAction SilentlyContinue
    if (-not $cli) {
        Remove-Item -Recurse -Force $stagingDir -ErrorAction SilentlyContinue
        Write-Output ("ERR hf CLI missing on this station: refusing to fall back to a single-connection " +
                       "download (see huggingface-bride-le-debit-par-connexion.md in the second brain, " +
                       "incident of 2026-09-24). Install or provision hf on the station, or fetch this " +
                       "file through a station that has it and copy it over the local network instead.")
        exit 1
    }
    $env:HF_XET_HIGH_PERFORMANCE = "1"
    & hf download $Repo $RepoFile --local-dir $stagingDir
    $downloaded = Join-Path $stagingDir $RepoFile
    if ($downloaded -ne $staged -and (Test-Path $downloaded)) {
        Move-Item -Force $downloaded $staged
    }

    $hash = (Get-FileHash -Algorithm SHA256 $staged).Hash.ToLower()
    if ($hash -ne $Expected) {
        Rename-Item $staged ($staged + ".bad")
        Write-Output ("HASH MISMATCH got " + $hash + " expected " + $Expected + " (fichier corrompu isole dans " + $stagingDir + ", Dest non touche)")
        exit 1
    } else {
        Move-Item -Force $staged $Dest
        Remove-Item -Recurse -Force $stagingDir -ErrorAction SilentlyContinue
        Write-Output ("OK " + $Dest + " " + $hash)
    }
} catch {
    Write-Output ("ERR " + $_.Exception.Message)
    exit 1
}
