# llm-launch.sh - shared body of every launcher. Sourced, never run.
#
# A launcher lists its variants with llm_variant, then calls llm_launch "$@". A
# variant is one model on one machine: the menu picks it, the body loads it
# there if needed, and Claude Code is pointed at that machine. A launcher that
# declares a single variant asks nothing and goes straight to it.
#
# One launcher per model family with a menu, rather than one launcher per model
# per machine: the same families live on a 32 GB box and on a 16 GB box, and a
# name per box doubled what there is to remember. Decided 2026-09-14, when this
# replaced the qwenu launcher.
#
# The six single-model launchers joined on 2026-09-14 as well. They each carried
# their own copy of the same hundred lines, and the copies had drifted: some
# announced a window the rule below forbids, and all six read a busy server as
# an empty one and reloaded over it without asking. One body means one place
# where that is decided.
#
# Written for the bash 3.2 that ships with macOS: no associative arrays, no
# ${var,,}, no mapfile.

set -euo pipefail

LLM_PORT=8080
LLM_CTL='D:\LLM-Setup\llm-ctl.ps1'
# The largest value the per-model launchers used before this one.
LLM_LOAD_TIMEOUT=240
LLM_OUTPUT_TOKENS=16384
# Which MCP servers Claude Code keeps. "none" gives it no server at all;
# "mail-imap" keeps a mail server expected at ~/.claude/bin/mail-imap-mcp, which
# this repository does not ship. A launcher overrides it between sourcing this
# file and calling llm_launch.
LLM_MCP=none

_llm_labels=(); _llm_hosts=(); _llm_actions=(); _llm_match=(); _llm_exclude=()
_llm_ids=(); _llm_windows=()

err() { printf '%s\n' "$*" >&2; }

# llm_variant LABEL HOST ACTION MATCH EXCLUDE MODEL_ID WINDOW
#   ACTION   the llm-ctl.ps1 action that loads the model on HOST
#   MATCH    lowercase text the served model_path must contain
#   EXCLUDE  lowercase text it must NOT contain, or '' when none is needed
#   WINDOW   the window the server really serves, n_ctx in /props
llm_variant() {
  _llm_labels+=("$1"); _llm_hosts+=("$2"); _llm_actions+=("$3"); _llm_match+=("$4")
  _llm_exclude+=("$5"); _llm_ids+=("$6"); _llm_windows+=("$7")
}

# What port 8080 on HOST says, printed as one of three states:
#   free          the connection is refused: nothing listens, nobody to disturb
#   <model_path>  lowercased, the model answering /props
#   unknown       anything else: a timeout, an error, a model still loading
# Both servers are single-slot, so /props and not /health says WHICH model holds
# the port. Only "free" lets a load go ahead without asking: a server busy on a
# long prompt can miss a 3-second timeout, and reading that as "nothing running"
# would load over whoever is using it. Found by review on 2026-09-14 and
# reproduced with a stubbed curl; the per-model launchers before this one had
# the same hole.
_llm_state() {
  local out rc=0 m
  out=$(curl -sS -m 3 "http://$1:${LLM_PORT}/props" 2>/dev/null) || rc=$?
  if (( rc == 7 )); then echo free; return; fi
  m=$(printf '%s' "$out" | sed -n 's/.*"model_path":"\([^"]*\)".*/\1/p' | tr '[:upper:]' '[:lower:]')
  if (( rc == 0 )) && [[ -n "$m" ]]; then echo "$m"; else echo unknown; fi
}

_llm_up() { curl -sS -m 3 "http://$1:${LLM_PORT}/health" 2>/dev/null | grep -q '"status":"ok"'; }

# _llm_matches INDEX STATE. Bash substring tests and not grep: under pipefail,
# grep -q can close the pipe before printf has written, and a match then reads
# as a failure.
_llm_matches() {
  [[ "$2" != free && "$2" != unknown && "$2" == *"${_llm_match[$1]}"* ]] || return 1
  [[ -z "${_llm_exclude[$1]}" || "$2" != *"${_llm_exclude[$1]}"* ]]
}

_llm_serves() { _llm_matches "$1" "$(_llm_state "${_llm_hosts[$1]}")"; }

# Plain ssh by default. LLM_SSH_KEY names a key and adds IdentitiesOnly, for an
# agent holding so many keys that the server refuses the connection before the
# right one is tried ("Too many authentication failures", which says nothing
# about the cause).
_llm_ssh() {
  local host=$1; shift
  local opts=(-o ConnectTimeout=10)
  if [[ -n "${LLM_SSH_KEY:-}" ]]; then opts+=(-o IdentitiesOnly=yes -i "$LLM_SSH_KEY"); fi
  ssh "${opts[@]}" "${LLM_SSH_USER:-youruser}@${host}" "$@"
}

# Loads over ssh, then polls until the model itself answers: there is never a
# manual warm-up to perform.
_llm_load() {
  local i=$1 host=${_llm_hosts[$1]} waited=0
  err "Chargement de ${_llm_labels[$i]}..."
  _llm_ssh "$host" "powershell -NoProfile -ExecutionPolicy Bypass -File ${LLM_CTL} -Action ${_llm_actions[$i]}" >&2
  while (( waited < LLM_LOAD_TIMEOUT )); do
    if _llm_up "$host" && _llm_serves "$i"; then
      err "Prêt."
      return 0
    fi
    sleep 5
    waited=$(( waited + 5 ))
  done
  err "Échec : le modèle n'a pas répondu en ${LLM_LOAD_TIMEOUT} s."
  err "Journal : D:\\LLM-Setup\\logs\\llm-err-${_llm_actions[$i]}.log sur ${host}."
  return 1
}

llm_launch() {
  local n=${#_llm_labels[@]} choice i answer

  # LLM_CHOICE skips the menu. Without a terminal it is required: a menu read
  # from a pipe would take the caller's input as a choice.
  if [[ -n "${LLM_CHOICE:-}" ]]; then
    choice=$LLM_CHOICE
  elif (( n == 1 )); then
    # A launcher with one variant has nothing to ask, on a terminal or not.
    choice=1
  elif [[ -t 0 ]]; then
    for (( i = 0; i < n; i++ )); do
      if _llm_serves "$i"; then
        err "  $(( i + 1 ))) ${_llm_labels[$i]}, déjà chargé"
      else
        err "  $(( i + 1 ))) ${_llm_labels[$i]}"
      fi
    done
    read -r -p "Lequel ? [1-${n}] " choice || { err "Abandon."; exit 1; }
  else
    err "Pas de terminal pour choisir : fixe LLM_CHOICE entre 1 et ${n}."
    exit 1
  fi
  # Two digits at most: a longer number overflows bash arithmetic and slips past
  # the bound, then crashes on the array index (found by review on 2026-09-14).
  if [[ ! "$choice" =~ ^[1-9][0-9]?$ ]] || (( choice > n )); then
    err "Choix invalide : ${choice}."
    exit 1
  fi
  i=$(( choice - 1 ))

  local host=${_llm_hosts[$i]} state
  state=$(_llm_state "$host")
  if ! _llm_matches "$i" "$state"; then
    if [[ "$state" != free ]]; then
      if [[ "$state" == unknown ]]; then
        err "La machine ${host} ne dit pas clairement ce qu'elle sert : délai dépassé, erreur ou chargement en cours."
      else
        err "La machine ${host} sert « ${state##*\\} », pas ce modèle."
      fi
      # Someone else may be using it: never swap without a person to say yes.
      if [[ ! -t 0 ]]; then err "Pas de terminal pour confirmer le remplacement : abandon."; exit 1; fi
      read -r -p "Le remplacer ? [o/N] " answer || answer=""
      [[ "$answer" =~ ^[oOyY]$ ]] || { err "Abandon."; exit 1; }
    fi
    _llm_load "$i"
  fi

  local id=${_llm_ids[$i]}
  # AUTH_TOKEN and not API_KEY: the latter triggers Claude Code's custom-key
  # approval prompt. llama-server started without --api-key accepts any bearer.
  export ANTHROPIC_AUTH_TOKEN="local"
  export ANTHROPIC_BASE_URL="http://${host}:${LLM_PORT}"
  # Every id resolves to the single loaded model; naming them keeps the UI honest
  # and stops Claude Code from reaching for a real Anthropic model.
  export ANTHROPIC_MODEL="$id" ANTHROPIC_DEFAULT_OPUS_MODEL="$id" \
    ANTHROPIC_DEFAULT_SONNET_MODEL="$id" ANTHROPIC_DEFAULT_HAIKU_MODEL="$id" \
    ANTHROPIC_SMALL_FAST_MODEL="$id"

  # An INPUT budget, not the window: n_ctx counts input and output in one pool,
  # so the client is told the window minus its output budget, and keeps room to
  # write at the end of a full session. The rule came from a /compact that
  # answered "summarization produced empty response" twice at 372,738 tokens of
  # 393,216 on 2026-09-08; that cause is probable, not proven, and the rule is
  # kept on that basis.
  export CLAUDE_CODE_MAX_OUTPUT_TOKENS=$LLM_OUTPUT_TOKENS
  export CLAUDE_CODE_MAX_CONTEXT_TOKENS=$(( ${_llm_windows[$i]} - LLM_OUTPUT_TOKENS ))

  # Read for PRESENCE, not for value: any non-empty string turns the disabling
  # on, "0" included (checked 2026-08-31 against the env-vars page).
  export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
  # Read by VALUE, unlike the line above: only "0", "false", "no" or "off"
  # disable it (2.1.251 binary, 2026-08-31). Dropping the attribution block keeps
  # the first tokens of every request stable, so the server reuses its prompt
  # cache instead of re-reading the whole context.
  export CLAUDE_CODE_ATTRIBUTION_HEADER=0

  err "Claude Code sur ${_llm_labels[$i]}, fenêtre annoncée ${CLAUDE_CODE_MAX_CONTEXT_TOKENS}."
  # At most one mail server. The others stay dropped: measured on muse, their 70
  # tool schemas weigh 108 KB of prompt, overhead that bites hard on a local
  # window. Skills, commands, memory and CLAUDE.md are untouched.
  local mcp='{"mcpServers":{}}'
  if [[ "$LLM_MCP" == mail-imap ]]; then
    mcp="{\"mcpServers\":{\"mail-imap\":{\"command\":\"${HOME}/.claude/bin/mail-imap-mcp\"}}}"
  fi
  exec claude --strict-mcp-config --mcp-config "$mcp" "$@"
}
