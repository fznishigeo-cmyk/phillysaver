# state_crypt.sh — sourced by commit_state.sh and restore_state.sh.
#
# The repository is public and state.db holds every member deal, so with the
# STATE_DB_KEY repository secret set the `data` branch carries state.db.enc
# (AES-256, key stretched with PBKDF2) instead of state.db. openssl reads the
# key from the environment: it never appears on a command line or in a log.

STATE_CIPHER=(-aes-256-cbc -pbkdf2 -iter 200000 -md sha256)

state_encrypt() (  # <state.db> <state.db.enc>
  set -o pipefail
  gzip -n -6 -c -- "$1" | openssl enc -e "${STATE_CIPHER[@]}" -salt -pass env:STATE_DB_KEY -out "$2"
)

state_decrypt() {  # <state.db.enc> <state.db> [require-gzip]
  local packed rc=1
  packed="$(mktemp)" || return 1
  if openssl enc -d "${STATE_CIPHER[@]}" -pass env:STATE_DB_KEY -in "$1" -out "$packed"; then
    if [[ "$(head -c 2 "$packed")" == $'\x1f\x8b' ]]; then
      if gzip -dc -- "$packed" > "$2"; then rc=0; fi
    elif [[ "$(head -c 15 "$packed")" == "SQLite format 3" && "${3:-}" != "require-gzip" ]]; then
      # Older snapshots encrypted SQLite directly. The optional flag makes
      # commit_state migrate those snapshots even when the bytes match.
      if cp "$packed" "$2"; then rc=0; fi
    fi
  fi
  rm -f "$packed"
  if [[ "$rc" -ne 0 ]]; then rm -f "$2"; fi
  return "$rc"
}
