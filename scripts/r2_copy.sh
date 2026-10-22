#!/usr/bin/env bash
# r2_copy.sh — copy the encrypted state to Cloudflare R2 (copy mode, operator
# 2026-10-22). Sourced by commit_state.sh after a successful push to the data
# branch, which stays the source of truth: restore_state.sh still reads git.
#
# Best effort by contract: r2_copy never fails its caller and never prints a
# secret, a key or an endpoint. Settings: config.yaml state.r2_copy (bool) and
# state.r2_keep (timestamped copies kept, besides state/latest.enc). Secrets:
# R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT, R2_BUCKET.

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

r2_aws() {
  # Newer AWS CLIs add checksums R2 may reject; only send them when required.
  AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
    AWS_DEFAULT_REGION=auto AWS_EC2_METADATA_DISABLED=true \
    AWS_REQUEST_CHECKSUM_CALCULATION=when_required AWS_RESPONSE_CHECKSUM_VALIDATION=when_required \
    timeout 180 aws --endpoint-url "$R2_ENDPOINT" "$@" 2>>"${R2_ERR_FILE:-/dev/null}"
}

r2_reason() {
  # One safe word for the log: the S3 error code (letters only), or a fixed
  # category. The raw message (endpoint, bucket, request ids) is never printed.
  local text code op
  text="$(cat "$1" 2>/dev/null || true)"
  code="$(grep -oE 'An error occurred \([A-Za-z]{1,40}\)' <<<"$text" | head -n1 | sed -E 's/.*\(([A-Za-z]+)\)/\1/')" || true
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

r2_copy() {
  local file="$1" config="$2" enabled keep key listing old
  read -r enabled keep <<<"$(r2_settings "$config")"
  if [[ "$enabled" != 1 ]]; then
    printf '[commit_state] r2 copy: off\n'
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
  key="state/$(date -u +%Y%m%dT%H%M%SZ)-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}.enc"
  R2_ERR_FILE="$(mktemp)" || R2_ERR_FILE=/dev/null
  # latest.enc is uploaded from the local file too: an in-bucket copy of a
  # multipart-sized object makes the CLI copy tags (GetObjectTagging), which R2
  # answers NotImplemented (fast #804).
  if ! r2_aws s3 cp --only-show-errors "$file" "s3://$R2_BUCKET/$key" >/dev/null ||
     ! r2_aws s3 cp --only-show-errors "$file" "s3://$R2_BUCKET/state/latest.enc" >/dev/null; then
    printf '[commit_state] r2 copy: failed: %s (the git copy is unaffected)\n' "$(r2_reason "$R2_ERR_FILE")"
    [[ "$R2_ERR_FILE" == /dev/null ]] || rm -f "$R2_ERR_FILE"
    R2_ERR_FILE=/dev/null
    return 0
  fi
  [[ "$R2_ERR_FILE" == /dev/null ]] || rm -f "$R2_ERR_FILE"
  R2_ERR_FILE=/dev/null
  printf '[commit_state] r2 copy: ok %s bytes -> %s\n' "$(wc -c < "$file" | tr -d ' ')" "$key"
  # Keep the newest $keep timestamped copies; latest.enc is never pruned.
  listing="$(r2_aws s3 ls "s3://$R2_BUCKET/state/")" || return 0
  old="$(awk '{print $NF}' <<<"$listing" | grep -E '^[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9]+-[0-9]+\.enc$' | sort | head -n "-$keep")" || true
  for key in $old; do
    r2_aws s3 rm --only-show-errors "s3://$R2_BUCKET/state/$key" >/dev/null || true
  done
  return 0
}
