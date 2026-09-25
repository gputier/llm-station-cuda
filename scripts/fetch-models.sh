#!/usr/bin/env bash
# Downloads model and template files onto a station's D:\models, verifying
# each one against an embedded SHA-256. Station 99 (default, no argument) or
# station 97 (first argument "97"): each keeps its own manifest below, since
# the two stations do not carry the same files. Usage:
#   ./fetch-models.sh        # station 99, needs STATION_99_SSH
#   ./fetch-models.sh 97     # station 97, needs STATION_97_SSH
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

STATION="${1:-99}"
case "$STATION" in
    99) SSH_VAR=STATION_99_SSH ;;
    97) SSH_VAR=STATION_97_SSH ;;
    *) echo "unknown station '${STATION}' (expected 99 or 97)" >&2; exit 2 ;;
esac
STATION_SSH="${!SSH_VAR:-}"
if [ -z "$STATION_SSH" ]; then
    echo "${SSH_VAR} is not set (user@host of the ${STATION}, see bench/.env.example)." >&2
    exit 2
fi

# repo|file_in_repo|dest_path_on_D|sha256
MANIFEST_99=(
    "XHToken/Spark-X2.5-1.7B-GGUF|Spark-X2.5-1.7B-Q8_0.gguf|D:\\models\\spark-x2.5-4b\\Spark-X2.5-1.7B-Q8_0.gguf|cd77c03185a834bb1162a4b7713520be5838058bfc54873645beff470bb24442"
    "spiritbuun/Qwen3.6-27B-DFlash-GGUF|dflash-draft-3.6-q8_0.gguf|D:\\models\\ternary-bonsai-27b\\dflash-draft-3.6-q8_0.gguf|29ba8b816eedea674e8bdabbd29db8da69539117c76da40e40d2207c0fb224db"
    "prism-ml/Ternary-Bonsai-2-27B-gguf|Ternary-Bonsai-2-27B-PTQ1_0.gguf|D:\\models\\ternary-bonsai-2-27b\\Ternary-Bonsai-2-27B-PTQ1_0.gguf|53107f530aa52eb00912263ab1ee29bd199261c87cd7b4ad4ca1318c1fe33ee3"
    # qwenu R1 left the Q6_K on 2026-09-25 (it filled the 32 GB card at 262144);
    # all three settings serve the Q5_K_M, sha256 read from
    # /api/models/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF?blobs=true on 2026-09-25.
    "JonathanColetti/Qwen3.8-27B-Uncensored-GGUF|Qwen3.8-27B-Uncensored-Q5_K_M.gguf|D:\\models\\qwen3.8-27b-uncensored\\Qwen3.8-27B-Uncensored-Q5_K_M.gguf|24780644a95f759a9aeeb228c3d852028f2fd40ce0b74d68134246ec4a959547"
    "froggeric/Qwen-Fixed-Chat-Templates|chat_template.jinja|D:\\models\\shared\\froggeric-chat-template.jinja|e57684bae4156211a55473c5a63be976a405a37ab5be5ae0e5abf1df5349c4b2"
    "unsloth/Muse-Glimmer-30B-GGUF|mmproj-Muse-Glimmer-30B-BF16.gguf|D:\\models\\muse-glimmer-30b\\mmproj-BF16.gguf|d08cdcfa0b41d8e20554b52df404ba4f7b440d0bc502a90038508b6407df8ee1"
    # Three models added 2026-09-24 on Guillaume's ruling (task t16a). Each
    # sha256 checked against /api/models/<repo>?blobs=true on 2026-09-24.
    "XingChen-AGI/Xing4.0-29B-A4B-GGUF|xing4_0-29b-IQ4_NL.gguf|D:\\models\\xing4.0-29b-a4b\\xing4_0-29b-IQ4_NL.gguf|9c9c81cf83b6ce974d3440088318de83b727c0064124ca6c3c919289adadb4dd"
    # VeriLoop-E2 moved on 2026-09-25 from Q8_0 (28595765600 bytes), which
    # spilled out of the 32 GB card at a 262144 window, to the Q6_K its
    # publisher names the overall sweet spot (QUANTIZATION_QUALITY.md of the
    # public repository). The gated VeriLoop-E2-Q8_0-GGUF repository answers
    # HTTP 401 anonymously since 2026-09-24, see docs/phase0-2026-09.md. The
    # BF16 draft is rejected by b11156 at load ("expected 19, got 18"); the
    # Q6_K draft is the same file as the Q5_K_M one (same sha256). Both
    # checked against
    # /api/models/tsinghua-sigs-robot-lab/VeriLoop-E2-GGUF?blobs=true on 2026-09-25.
    "tsinghua-sigs-robot-lab/VeriLoop-E2-GGUF|VeriLoop-E2-Q6_K.gguf|D:\\models\\veriloop-e2\\VeriLoop-E2-Q6_K.gguf|15d8f856471c4853f6bf0036b2a517426c6cb30a0313cef58a7f9577fd26fe9e"
    "tsinghua-sigs-robot-lab/VeriLoop-E2-GGUF|mtp-VeriLoop-E2-Q6_K.gguf|D:\\models\\veriloop-e2\\mtp-VeriLoop-E2-Q6_K.gguf|0e7f2dfe254f3a5d195d105832411acc1007fd7bb9ac980fe9aae4a1f290d671"
    "bartowski/Altworld_Hemmingway-1-GGUF|Altworld_Hemmingway-1-Q5_K_M.gguf|D:\\models\\hemmingway-1\\Altworld_Hemmingway-1-Q5_K_M.gguf|b0ebd5bab0919114303dfb20d8e517fdd75d92a8d20ffa8005b5de54abc05f2e"
    # hemmingway R1 serves the Q5_K_S since 2026-09-25 (Q5_K_M with KV q8_0
    # filled the card); R2 and R3 keep the Q5_K_M above.
    "bartowski/Altworld_Hemmingway-1-GGUF|Altworld_Hemmingway-1-Q5_K_S.gguf|D:\\models\\hemmingway-1\\Altworld_Hemmingway-1-Q5_K_S.gguf|bef192279e5c3cd4452ba47419bb217e8cb1f579b7cbc101e67b09acbd99d4cc"
)
# Station 97 (RTX 4080 SUPER, 16 GB, Ada): bonsai2 R1/R2/R3, ruling of
# Guillaume of 2026-09-24 (docs/phase0-2026-09.md, "Choix de packing bonsai2
# R1", Option B). Same PTQ1_0 file and sha256 as the 99's entry above (one
# Hugging Face repository, same published LFS sha256, checked again via
# /api/models/prism-ml/Ternary-Bonsai-2-27B-gguf?blobs=true on 2026-09-24);
# the chat template is not on Hugging Face, it is the same per-model file
# already deployed to the 99 (bench/configs/99/bonsai2/*.yaml), copied
# station to station and re-hashed on each side, not fetched again here.
#
# Three more models added to the 97 campaign, ruling of Guillaume of
# 2026-09-24: qwen36apex, katapex, occamy, all from the collection
# huggingface.co/collections/IsValorum/apex-i-miniplus-v21-current. Every
# size and sha256 below was read from
# /api/models/<repo>?blobs=true on 2026-09-24, which matches the byte counts
# Guillaume gave in the mandate exactly (15229762144, 14751301024,
# 14751300704). GGUF header read directly (ranged GET on the resolve URL,
# ASCII near the start of the file, no model loaded, no GPU touched):
# general.architecture "qwen35moe" on all three, same key our existing kat
# and nex configs on the 99 already run on b11156, so no --override-kv is
# needed (unlike nex/bonsai on the 99, whose real key differed from the
# assumed one).
MANIFEST_97=(
    "prism-ml/Ternary-Bonsai-2-27B-gguf|Ternary-Bonsai-2-27B-PTQ1_0.gguf|D:\\models\\ternary-bonsai-2-27b\\Ternary-Bonsai-2-27B-PTQ1_0.gguf|53107f530aa52eb00912263ab1ee29bd199261c87cd7b4ad4ca1318c1fe33ee3"
    "IsValorum/Qwen3.6-35B-A3B-MTP-APEX-I-MiniPlus-V2.1-GGUF|Qwen3.6-35B-A3B.APEX-I-MiniPlus-V2.1.gguf|D:\\models\\qwen36apex\\Qwen3.6-35B-A3B.APEX-I-MiniPlus-V2.1.gguf|afe2de904fa378863e25738bcdb0392ba48b9725b19ca8df738a5005139f3c42"
    "IsValorum/Qwen3.6-35B-A3B-MTP-APEX-I-MiniPlus-V2.1-GGUF|mtp-Qwen3.6-35B-A3B-Q4_0.gguf|D:\\models\\qwen36apex\\mtp-Qwen3.6-35B-A3B-Q4_0.gguf|623351aeccfb9a26991ea6a7f0d24ef993a975fb2a508e4d95d3be1f72bf58da"
    "IsValorum/KAT-Coder-V2.5-Dev-APEX-I-MiniPlus-V2.1-GGUF|KAT-Coder-V2.5-Dev.APEX-I-MiniPlus-V2.1.gguf|D:\\models\\katapex\\KAT-Coder-V2.5-Dev.APEX-I-MiniPlus-V2.1.gguf|aa01217072e43435daa9159076572d2da6b866824b3ebb507ce81652c864bca7"
    "IsValorum/Occamy-1.0-APEX-I-MiniPlus-V2.1-GGUF|Occamy-1.0.APEX-I-MiniPlus-V2.1.gguf|D:\\models\\occamy\\Occamy-1.0.APEX-I-MiniPlus-V2.1.gguf|5fa88e7a3e7ea4833d6e45bb3dcaf34aa5a8ebaf85cebe3794c4a94f4d4cb303"
    "IsValorum/Occamy-1.0-APEX-I-MiniPlus-V2.1-GGUF|mmproj-Accio-Lab_occamy-1.0-Q8_0.gguf|D:\\models\\occamy\\mmproj-Accio-Lab_occamy-1.0-Q8_0.gguf|17c9213e22ccf9b1b68c64eb7a4abd4ef5166d6164b7d9720d34e06c309fcd82"
)
# The Q4_0 MTP draft is the file IsValorum actually published (discussion #1
# of the Abliterated sibling repo, read 2026-09-24: a user named Axway
# re-quantized the forgotten Q8_0 draft down to Q3_K_M and measured the same
# acceptance rate, 0.86133 vs 0.86429 over about 3300 generated drafts;
# IsValorum replied "I forgot to quantize the MTP files for all the V2.1
# models" and uploaded Q4_0 as the fix). Q3_K_M was never republished by
# IsValorum, only measured by a third party in the discussion thread: this
# manifest takes the file that is actually on the repository, Q4_0. Q8_0
# (1989597056 bytes) is also published but not fetched: at 14.18 GiB of
# weights already on a 16 GiB card, the extra 0.85 GiB over Q4_0 buys nothing
# usable, see docs/phase0-2026-09.md for the VRAM measurement.
# occamy's mmproj is the one file the repository publishes; the base card
# (Accio-Lab/occamy-1.0) advertises OCR/document use of it, so it is kept
# mounted on all three of occamy's variants rather than reserved for one, the
# same way muse always mounts its own mmproj on the 99.
case "$STATION" in
    99) MANIFEST=("${MANIFEST_99[@]}") ;;
    97) MANIFEST=("${MANIFEST_97[@]}") ;;
esac
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
scp -q "$SCRIPT_DIR/fetch-one.ps1" "$STATION_SSH:D:/LLM-Setup/bench-specs/fetch-one.ps1"

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
    ssh -o ConnectTimeout=8 "$STATION_SSH" "powershell -NoProfile -ExecutionPolicy Bypass -File D:\\LLM-Setup\\bench-specs\\fetch-one.ps1 -Repo \"${repo}\" -RepoFile \"${path}\" -Dest \"${dest_win}\" -Expected \"${expected}\""
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
