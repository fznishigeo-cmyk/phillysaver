# state_crypt.sh — sourced by commit_state.sh and restore_state.sh.
#
# The repository is public and state.db holds every member deal, so with the
# STATE_DB_KEY repository secret set the `data` branch carries state.db.enc
# (AES-256, key stretched with PBKDF2) instead of state.db. openssl reads the
# key from the environment: it never appears on a command line or in a log.

STATE_CIPHER=(-aes-256-cbc -pbkdf2 -iter 200000 -md sha256)

state_encrypt() {  # <state.db> <state.db.enc>
  openssl enc -e "${STATE_CIPHER[@]}" -salt -pass env:STATE_DB_KEY -in "$1" -out "$2"
}

state_decrypt() {  # <state.db.enc> <state.db>
  openssl enc -d "${STATE_CIPHER[@]}" -pass env:STATE_DB_KEY -in "$1" -out "$2"
}
