#!/usr/bin/env bash
# r2_copy.sh — copy the encrypted state to Cloudflare R2 (copy mode, operator
# 2026-10-22). Sourced by commit_state.sh after a successful push to the data
# branch, which stays the source of truth: restore_state.sh still reads git.
#
# Best effort by contract: r2_copy never fails its caller and never prints a
# secret, a key or an endpoint. Settings: config.yaml state.r2_copy (bool) and
# state.r2_keep (timestamped copies kept, besides state/latest.enc). Secrets:
# R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT, R2_BUCKET.
#
# R2 primary (state.r2_primary, phase 2): r2_save uploads BEFORE the git push
# and fails its caller when it cannot; restore_state.sh then follows the
# pointer file state.r2.json to that exact object. Same logging rules.
#
# The optional work (the copy and every prune) skips pre-send checkpoints
# (DEALRADAR_STATE_CHECKPOINT=1, set by dealradar/persistence.py): a stalled
# copy could outlive the checkpoint's own timeout after a good push, and a
# killed checkpoint stops every send of the run. The run's final save copies
# and prunes, all within one allowance. The required r2_save still runs in a
# checkpoint, bounded by the R2_DEADLINE persistence.py passes: a slow upload
# refuses that save before git is touched, and the final save retries.
R2_OPTIONAL_BUDGET_S="${R2_OPTIONAL_BUDGET_S:-180}"

r2_settings() {
  # Prints "<1|0> <keep>"; anything unreadable or invalid means off.
  python3 - "$1" <<'PY' 2>/dev/null || echo "0 0"
import sys, yaml
try:
    state = (yaml.safe_load(open(sys.argv[1])) or {}).get("state") or {}
    on, keep = state.get("r2_copy", False), state.get("r2_keep", 24)
    ok = on is True and type(keep) is int and 1 <= keep <= 1000
    print("1 %d" % keep if ok else "0 0")
except Exception:
    print("0 0")
PY
}

r2_primary_keep() {
  # Prints 0 unless state.r2_primary is true, else state.r2_keep; "invalid"
  # when that cannot be read (state_size.py has already validated the bool).
  python3 - "$1" <<'PY' 2>/dev/null || echo invalid
import os, sys, yaml
raw = yaml.safe_load(open(sys.argv[1])) if os.path.exists(sys.argv[1]) else None
state = (raw or {}).get("state") or {}
keep = state.get("r2_keep", 24)
if state.get("r2_primary", False) is not True:
    print(0)
else:
    print(keep if type(keep) is int and 1 <= keep <= 1000 else "invalid")
PY
}

r2_aws() {
  # 180 s per call, or what is left of R2_DEADLINE (epoch seconds) when set.
  local limit=180
  if [[ -n "${R2_DEADLINE:-}" ]]; then
    limit=$(( R2_DEADLINE - $(date +%s) ))
    if (( limit > 180 )); then limit=180; fi
    if (( limit <= 0 )); then
      echo "timed out: R2 allowance spent" >>"${R2_ERR_FILE:-/dev/null}"
      return 1
    fi
  fi
  # Newer AWS CLIs add checksums R2 may reject; only send them when required.
  AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
    AWS_DEFAULT_REGION=auto AWS_EC2_METADATA_DISABLED=true \
    AWS_REQUEST_CHECKSUM_CALCULATION=when_required AWS_RESPONSE_CHECKSUM_VALIDATION=when_required \
    timeout "$limit" aws --endpoint-url "$R2_ENDPOINT" "$@" 2>>"${R2_ERR_FILE:-/dev/null}"
}

r2_reason() {
  # One safe word for the log: the S3 error code (letters/digits), or a fixed
  # category. The raw message (endpoint, bucket, request ids) is never printed.
  local text code op
  text="$(cat "$1" 2>/dev/null || true)"
  code="$(grep -oE 'An error occurred \([A-Za-z0-9]{1,40}\)' <<<"$text" | head -n1 | sed -E 's/.*\(([A-Za-z0-9]+)\)/\1/')" || true
  op="$(grep -oE 'when calling the [A-Za-z]{1,40} operation' <<<"$text" | head -n1 | awk '{print $4}')" || true
  if [[ -n "$code" ]]; then printf '%s%s' "$code" "${op:+ on $op}"
  elif grep -qi 'could not connect\|connection\|name resolution' <<<"$text"; then printf 'endpoint unreachable'
  elif grep -qi 'invalid endpoint\|not a valid' <<<"$text"; then printf 'invalid endpoint'
  elif grep -qi 'unable to locate credentials' <<<"$text"; then printf 'no credentials'
  elif grep -qi 'ssl\|certificate' <<<"$text"; then printf 'tls error'
  elif grep -qi 'timed out\|timeout' <<<"$text"; then printf 'timeout'
  elif [[ -n "$text" ]]; then printf 'unrecognized error'
  else printf 'no error text'
  fi
}

r2_key() {
  printf 'state/%s-%s-%s.enc' "$(date -u +%Y%m%dT%H%M%SZ)" "${GITHUB_RUN_ID:-local}" "${GITHUB_RUN_ATTEMPT:-0}"
}

r2_put() {
  # <file> <key>: upload to <key> and state/latest.enc. On failure prints only
  # r2_reason's safe word and returns 1.
  local rc=0
  R2_ERR_FILE="$(mktemp)" || R2_ERR_FILE=/dev/null
  # latest.enc is uploaded from the local file too: an in-bucket copy of a
  # multipart-sized object makes the CLI copy tags (GetObjectTagging), which R2
  # answers NotImplemented (fast #804).
  if ! r2_aws s3 cp --only-show-errors "$1" "s3://$R2_BUCKET/$2" >/dev/null ||
     ! r2_aws s3 cp --only-show-errors "$1" "s3://$R2_BUCKET/state/latest.enc" >/dev/null; then
    r2_reason "$R2_ERR_FILE"
    rc=1
  fi
  [[ "$R2_ERR_FILE" == /dev/null ]] || rm -f "$R2_ERR_FILE"
  R2_ERR_FILE=/dev/null
  return "$rc"
}

r2_prune() {
  # Keep the newest <keep> timestamped copies; latest.enc is never pruned.
  # Optional: skipped in a pre-send checkpoint, bounded by one allowance.
  [[ "${DEALRADAR_STATE_CHECKPOINT:-}" == 1 ]] && return 0
  local R2_DEADLINE="${R2_DEADLINE:-$(( $(date +%s) + R2_OPTIONAL_BUDGET_S ))}"
  local listing old key
  listing="$(r2_aws s3 ls "s3://$R2_BUCKET/state/")" || return 0
  old="$(awk '{print $NF}' <<<"$listing" | grep -E '^[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9]+-[0-9]+\.enc$' | sort | head -n "-$1")" || true
  for key in $old; do
    r2_aws s3 rm --only-show-errors "s3://$R2_BUCKET/state/$key" >/dev/null || true
  done
  return 0
}

r2_copy() {
  local file="$1" config="$2" enabled keep key reason
  read -r enabled keep <<<"$(r2_settings "$config")"
  if [[ "$enabled" != 1 ]]; then
    printf '[commit_state] r2 copy: off\n'
    return 0
  fi
  if [[ "${DEALRADAR_STATE_CHECKPOINT:-}" == 1 ]]; then
    printf '[commit_state] r2 copy: skipped (pre-send checkpoint; the final save copies)\n'
    return 0
  fi
  if [[ -z "${R2_ACCESS_KEY_ID:-}" || -z "${R2_SECRET_ACCESS_KEY:-}" || -z "${R2_ENDPOINT:-}" || -z "${R2_BUCKET:-}" ]]; then
    printf '[commit_state] r2 copy: skipped (R2 secrets not set)\n'
    return 0
  fi
  if ! command -v aws >/dev/null 2>&1; then
    printf '[commit_state] r2 copy: skipped (aws cli missing)\n'
    return 0
  fi
  # One allowance for both uploads and the prune (each call alone had 180 s).
  local R2_DEADLINE=$(( $(date +%s) + R2_OPTIONAL_BUDGET_S ))
  key="$(r2_key)"
  if ! reason="$(r2_put "$file" "$key")"; then
    printf '[commit_state] r2 copy: failed: %s (the git copy is unaffected)\n' "$reason"
    return 0
  fi
  printf '[commit_state] r2 copy: ok %s bytes -> %s\n' "$(wc -c < "$file" | tr -d ' ')" "$key"
  r2_prune "$keep"
}

r2_save() {
  # R2 primary: <file> goes up first and must land; then <pointer> names that
  # exact object {key, bytes, sha256, saved_at, run_id}. Returns 1 on any
  # failure (missing secrets or CLI included): the caller pushes nothing.
  local file="$1" pointer="$2" key reason
  if [[ -z "${R2_ACCESS_KEY_ID:-}" || -z "${R2_SECRET_ACCESS_KEY:-}" || -z "${R2_ENDPOINT:-}" || -z "${R2_BUCKET:-}" ]]; then
    printf '[commit_state] r2 primary: R2 secrets not set; refusing push\n'
    return 1
  fi
  if ! command -v aws >/dev/null 2>&1; then
    printf '[commit_state] r2 primary: aws cli missing; refusing push\n'
    return 1
  fi
  key="$(r2_key)"
  if ! reason="$(r2_put "$file" "$key")"; then
    printf '[commit_state] r2 primary: upload failed: %s; refusing push\n' "$reason"
    return 1
  fi
  if ! python3 - "$file" "$key" "$pointer" <<'PY'
import hashlib, json, os, sys
from datetime import datetime, timezone
digest = hashlib.sha256()
with open(sys.argv[1], "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        digest.update(chunk)
pointer = {"key": sys.argv[2], "bytes": os.path.getsize(sys.argv[1]), "sha256": digest.hexdigest(),
           "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "run_id": os.environ.get("GITHUB_RUN_ID") or "local"}
with open(sys.argv[3], "w") as f:
    f.write(json.dumps(pointer, indent=2, sort_keys=True) + "\n")
PY
  then
    printf '[commit_state] r2 primary: could not write the pointer; refusing push\n'
    return 1
  fi
  printf '[commit_state] r2 primary: ok %s bytes -> %s\n' "$(wc -c < "$file" | tr -d ' ')" "$key"
}
