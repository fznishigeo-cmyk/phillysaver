#!/usr/bin/env bash
# commit_state.sh — commit state.db (and sources_report.md when present) to the
# `data` branch of the current repository. NEVER touches main.
#
# Behavior:
#   * no state.db in the repo root            -> no-op, exit 0
#   * state.db identical to origin/data copy  -> no-op, exit 0
#   * origin/data missing                     -> creates it as an orphan branch
#   * push races (concurrent runs)            -> up to 4 retries, backoff 2 4 8 16 s
#
# Runs against the repository that contains the current working directory, so it
# works the same from GitHub Actions (`bash scripts/commit_state.sh` at the repo
# root), a laptop, or a VPS cron.
set -euo pipefail

BRANCH="data"
BOT_NAME="dealradar-bot"
BOT_EMAIL="actions@users.noreply.github.com"

REPO_DIR="$(git rev-parse --show-toplevel)"
WORKTREE="$REPO_DIR/.state-worktree"

log() { printf '[commit_state] %s\n' "$*"; }

if [[ ! -f "$REPO_DIR/state.db" ]]; then
  log "state.db not found — nothing to commit (no-op)"
  exit 0
fi

cleanup() {
  git -C "$REPO_DIR" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
  rm -rf "$WORKTREE"
  git -C "$REPO_DIR" worktree prune >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Clear any stale worktree left behind by an earlier interrupted run.
cleanup

git -C "$REPO_DIR" fetch origin "$BRANCH" || true

if git -C "$REPO_DIR" rev-parse --verify --quiet "origin/$BRANCH" >/dev/null; then
  git -C "$REPO_DIR" worktree add "$WORKTREE" "origin/$BRANCH" >/dev/null
else
  log "origin/$BRANCH does not exist yet — creating an orphan branch"
  # A stale local `data` branch (from an earlier failed push) would block
  # `checkout --orphan`; it only ever holds unpushed state snapshots.
  git -C "$REPO_DIR" branch -D "$BRANCH" >/dev/null 2>&1 || true
  git -C "$REPO_DIR" worktree add --detach "$WORKTREE" >/dev/null
  git -C "$WORKTREE" checkout --orphan "$BRANCH" >/dev/null 2>&1
  # Drop everything inherited from the checkout; data holds only state files.
  git -C "$WORKTREE" rm -rf --quiet . >/dev/null 2>&1 || true
  find "$WORKTREE" -mindepth 1 -maxdepth 1 -not -name '.git' -exec rm -rf {} +
fi

cp "$REPO_DIR/state.db" "$WORKTREE/state.db"
git -C "$WORKTREE" add state.db
if [[ -f "$REPO_DIR/sources_report.md" ]]; then
  cp "$REPO_DIR/sources_report.md" "$WORKTREE/sources_report.md"
  git -C "$WORKTREE" add sources_report.md
fi

if git -C "$WORKTREE" diff --quiet --cached HEAD -- 2>/dev/null; then
  log "state unchanged — nothing to commit (no-op)"
  exit 0
fi

STAMP="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
git -C "$WORKTREE" -c user.name="$BOT_NAME" -c user.email="$BOT_EMAIL" \
  commit --quiet -m "state: $STAMP [skip ci]"

# Push to the data branch only — never main. Retry up to 4 times on failure
# (a concurrent fast-lane/hourly run may have pushed first) with exponential
# backoff; after each failure, rebase onto the moved branch tip.
pushed=0
for delay in 2 4 8 16 0; do
  if git -C "$WORKTREE" push origin "HEAD:$BRANCH"; then
    pushed=1
    break
  fi
  if [[ "$delay" -eq 0 ]]; then
    break
  fi
  log "push failed — retrying in ${delay}s"
  sleep "$delay"
  git -C "$REPO_DIR" fetch origin "$BRANCH" || true
  if git -C "$REPO_DIR" rev-parse --verify --quiet "origin/$BRANCH" >/dev/null; then
    git -C "$WORKTREE" -c user.name="$BOT_NAME" -c user.email="$BOT_EMAIL" \
      rebase "origin/$BRANCH" || git -C "$WORKTREE" rebase --abort || true
  fi
done

if [[ "$pushed" -ne 1 ]]; then
  log "push to origin/$BRANCH failed after 4 retries"
  exit 1
fi
log "state committed to origin/$BRANCH"
