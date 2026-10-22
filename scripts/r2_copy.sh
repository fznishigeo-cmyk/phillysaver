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
    timeout 180 aws --endpoint-url "$R2_ENDPOINT" "$@" 2>/dev/null
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
  if ! r2_aws s3 cp --only-show-errors "$file" "s3://$R2_BUCKET/$key" >/dev/null ||
     ! r2_aws s3 cp --only-show-errors "s3://$R2_BUCKET/$key" "s3://$R2_BUCKET/state/latest.enc" >/dev/null; then
    printf '[commit_state] r2 copy: failed (the git copy is unaffected)\n'
    return 0
  fi
  printf '[commit_state] r2 copy: ok %s bytes -> %s\n' "$(wc -c < "$file" | tr -d ' ')" "$key"
  # Keep the newest $keep timestamped copies; latest.enc is never pruned.
  listing="$(r2_aws s3 ls "s3://$R2_BUCKET/state/")" || return 0
  old="$(awk '{print $NF}' <<<"$listing" | grep -E '^[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9]+-[0-9]+\.enc$' | sort | head -n "-$keep")" || true
  for key in $old; do
    r2_aws s3 rm --only-show-errors "s3://$R2_BUCKET/state/$key" >/dev/null || true
  done
  return 0
}
