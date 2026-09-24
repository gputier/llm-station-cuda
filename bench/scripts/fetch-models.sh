#!/usr/bin/env bash
# Downloads the new model and template files listed in spec section 5.3, onto
# the 99 only, next to the existing files in D:\models. Never touches the 97
# (parallel-rules.md: "Do not touch the 97 in any way (no ssh)").
#
# Every entry below was checked against the real Hugging Face repository
# tree on 2026-09-23 (the API's /api/models/<repo>?blobs=true endpoint, which
# gives the exact rfilename and published LFS sha256; the plain tree/main
# endpoint redacts lfs.oid on at least one of these repos and cannot be
# trusted). The SHA-256 is embedded here rather than looked up at run time,
# so a later change on the Hugging Face side cannot silently swap what this
# script accepts. Each file is fetched by fetch-one.ps1 (copied next to this
# script), which uses the station's "hf" CLI when present (its
# huggingface-cli is deprecated and downloads nothing, confirmed live on
# 2026-09-23) or Invoke-WebRequest otherwise. Either way the file is hashed
# locally after transfer and compared against the embedded SHA-256: the
# script refuses to continue on a mismatch, and a wrong or partial file is
# renamed with a .bad suffix rather than left under its final name.
#
# Two files from the spec's download list are NOT here because they are
# already present on the 99's disk, confirmed by Get-FileHash on 2026-09-23:
#   - the NVFP4 MEDIUM tier for qwen R1
#     (D:\models\qwen3.8-27b-nvfp4\Qwen3.8-27B-NVFP4-MTP-MEDIUM.gguf,
#     sha256 f0b4c538c75037f026bde3b650f0ca639d382c128a4572769dce1183db86253a,
#     esatapedico/Qwen3.8-27B-NVFP4-MTP-GGUF)
#   - the "z-lab drafter" for bonsai2 R1/R3, which turned out to already be
#     on disk under a different name
#     (D:\models\ternary-bonsai-2-27b\Qwen3.8-27B-DFlash2-Q4_K_M.gguf,
#     sha256 1a25c56858e1ebe93f2718ac1d49d1151f9323325c1bbfd6209370f4db131ebd,
#     z-lab/Qwen3.8-27B-DFlash2-GGUF)
set -euo pipefail

if [ -z "${STATION_99_SSH:-}" ]; then
    echo "STATION_99_SSH is not set (user@host of the 99, see bench/.env.example)." >&2
    exit 2
fi

# repo|file_in_repo|dest_path_on_D|sha256
MANIFEST=(
    "XHToken/Spark-X2.5-1.7B-GGUF|Spark-X2.5-1.7B-Q8_0.gguf|D:\\models\\spark-x2.5-4b\\Spark-X2.5-1.7B-Q8_0.gguf|cd77c03185a834bb1162a4b7713520be5838058bfc54873645beff470bb24442"
    "spiritbuun/Qwen3.6-27B-DFlash-GGUF|dflash-draft-3.6-q8_0.gguf|D:\\models\\ternary-bonsai-27b\\dflash-draft-3.6-q8_0.gguf|29ba8b816eedea674e8bdabbd29db8da69539117c76da40e40d2207c0fb224db"
    "prism-ml/Ternary-Bonsai-2-27B-gguf|Ternary-Bonsai-2-27B-PTQ1_0.gguf|D:\\models\\ternary-bonsai-2-27b\\Ternary-Bonsai-2-27B-PTQ1_0.gguf|53107f530aa52eb00912263ab1ee29bd199261c87cd7b4ad4ca1318c1fe33ee3"
    "JonathanColetti/Qwen3.8-27B-Uncensored-GGUF|Qwen3.8-27B-Uncensored-Q6_K.gguf|D:\\models\\qwen3.8-27b-uncensored\\Qwen3.8-27B-Uncensored-Q6_K.gguf|a50aa1478295b58ee3d93eabe02c17f6d5fcf6cb787fd8a0ab07ac629a46cae6"
    "froggeric/Qwen-Fixed-Chat-Templates|chat_template.jinja|D:\\models\\shared\\froggeric-chat-template.jinja|e57684bae4156211a55473c5a63be976a405a37ab5be5ae0e5abf1df5349c4b2"
    "unsloth/Muse-Glimmer-30B-GGUF|mmproj-Muse-Glimmer-30B-BF16.gguf|D:\\models\\muse-glimmer-30b\\mmproj-BF16.gguf|d08cdcfa0b41d8e20554b52df404ba4f7b440d0bc502a90038508b6407df8ee1"
)
# The muse entry is the mmproj muse/R3 needs and R3 never had: the file names
# already on disk for this profile (Muse-Glimmer-30B-UD-Q4_K_XL.gguf,
# mmproj-kquant.gguf, dflash-kquant.gguf) match unsloth/Muse-Glimmer-30B-GGUF
# byte for byte (checked via /api/models?blobs=true on 2026-09-24), which is
# what identifies it as the real source repository; the sha256 above is the
# one that API publishes for mmproj-Muse-Glimmer-30B-BF16.gguf, renamed on
# the way down to match the path muse/R3.yaml already expects.
# The froggeric entry is not LFS-tracked (28234 bytes, plain git blob): the
# Hugging Face API has no published sha256 for it, only a git blob sha1.
# The sha256 above was computed locally on 2026-09-23 from the live content
# at https://huggingface.co/froggeric/Qwen-Fixed-Chat-Templates/resolve/main/chat_template.jinja
# (shasum -a 256), and is what this script checks the download against.
# The repository serves ONE current template covering Qwen 3.5/3.6/3.8
# (its own header sets template_version "qwen3.8-froggeric-v22.5"); the
# archive/qwen3.5 and archive/qwen3.6 directories hold only OLD, superseded
# versions (v8 to v19/v16) and are not used here.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
scp -q "$SCRIPT_DIR/fetch-one.ps1" "$STATION_99_SSH:D:/LLM-Setup/bench-specs/fetch-one.ps1"

fetch_one() {
    local repo="$1" path="$2" dest_win="$3" expected="$4"

    # The values land inside double quotes on the remote command line: refuse
    # any character that would end or expand them rather than escape it.
    case "${repo}${path}${dest_win}${expected}" in
        *[\"\`\$]*) echo "refused: quote, backtick or dollar in manifest entry ${repo}/${path}" >&2; return 1 ;;
    esac
    echo "fetching ${repo}/${path} -> ${dest_win}"
    # Windows cmd.exe (which parses this command line before handing it to
    # powershell.exe) does not treat a single quote as a quoting character:
    # single-quoted arguments reach PowerShell with the quote characters
    # still literally attached, which corrupts a "D:\..." path into
    # something PowerShell reads as an invalid drive name. Double quotes are
    # the ones cmd.exe actually strips, so the arguments below use those.
    ssh -o ConnectTimeout=8 "$STATION_99_SSH" "powershell -NoProfile -ExecutionPolicy Bypass -File D:\\LLM-Setup\\bench-specs\\fetch-one.ps1 -Repo \"${repo}\" -RepoFile \"${path}\" -Dest \"${dest_win}\" -Expected \"${expected}\""
}

status=0
for entry in "${MANIFEST[@]}"; do
    IFS='|' read -r repo path dest expected <<<"$entry"
    if ! fetch_one "$repo" "$path" "$dest" "$expected"; then
        status=1
    fi
done

# KAT APEX-MTP is deliberately not in the manifest: its Hugging Face page
# returned HTTP 401 on 2026-09-23 (gbuzhf/KAT-Coder-V2.5-Dev-APEX-MTP-GGUF),
# so the announced 2.03x gain could not be read and kat R2 falls back to the
# existing file with the instruct-mode sampling of the same card (see
# bench/configs/99/kat/R2.yaml and docs/phase0-2026-09.md).

exit "$status"
