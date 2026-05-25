#!/usr/bin/env bash
# =============================================================================
# setup-github-project.sh
# =============================================================================
# macOS only. Auto-installs gh and yq via Homebrew if missing.
# On Linux: install gh and yq manually, then run.
#
# Applies .github/project.yml to GitHub via gh CLI.
# Idempotent: safe to re-run. Skips existing labels, milestones, issues.
#
# Usage:
#   ./scripts/setup-github-project.sh
#
# What it does:
#   1. Creates labels in this repo
#   2. Creates milestones in this repo
#   3. Finds or creates the shared org project (zuwarskejes-labs)
#   4. Links this repo to the org project
#   5. Creates issues in this repo and adds them to the org project
#
# Model:
#   Each repo owns its own project.yml, labels, milestones, and issues.
#   All repos link to the shared org project as a unified view.
#   Run this script from each repo independently.
#
# Convention:
#   .github/project.yml is the source of truth for this repo's backlog.
#   Edit that file, then re-run. Future: GORA ADR Sync does this automatically.
# =============================================================================

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
CONFIG="$REPO_ROOT/.github/project.yml"

OWNER=$(yq '.project.owner' "$CONFIG")
REPO=$(yq '.project.repo' "$CONFIG")
ORG_PROJECT=$(yq '.project.org_project' "$CONFIG")

log()  { echo "▸ $*"; }
ok()   { echo "  ✓ $*"; }
skip() { echo "  – $* (already exists)"; }

# ── 0. Setup ──────────────────────────────────────────────────────────────────

# OS check
if [[ "$(uname)" != "Darwin" ]]; then
  echo "NOTE: this script auto-installs deps via Homebrew (macOS only)."
  echo "On Linux: install gh (https://cli.github.com) and yq (https://github.com/mikefarah/yq) manually, then re-run."
  echo ""
fi

# gh CLI
if ! command -v gh &>/dev/null; then
  if command -v brew &>/dev/null; then
    log "Installing gh CLI..."
    brew install gh
  else
    echo "ERROR: gh CLI not found and brew is not available."
    echo "Install manually: https://cli.github.com"
    exit 1
  fi
fi

# yq
if ! command -v yq &>/dev/null; then
  if command -v brew &>/dev/null; then
    log "Installing yq..."
    brew install yq
  else
    echo "ERROR: yq not found and brew is not available."
    echo "Install manually: https://github.com/mikefarah/yq"
    exit 1
  fi
fi

# gh auth
if ! gh auth status &>/dev/null; then
  log "Not authenticated with GitHub. Starting login..."
  gh auth login
fi

# project scopes — test with a lightweight projects API call
if ! gh project list --owner "$OWNER" --format json &>/dev/null; then
  log "Token missing project scopes. Refreshing..."
  gh auth refresh -s read:project,project
fi

log "Configuring $OWNER/$REPO → org project: $ORG_PROJECT"
echo ""

# ── 1. Labels ─────────────────────────────────────────────────────────────────
log "Labels"
LABEL_COUNT=$(yq '.labels | length' "$CONFIG")
for i in $(seq 0 $((LABEL_COUNT - 1))); do
  NAME=$(yq ".labels[$i].name" "$CONFIG")
  COLOR=$(yq ".labels[$i].color" "$CONFIG")
  DESC=$(yq ".labels[$i].description" "$CONFIG")

  if gh label list --repo "$OWNER/$REPO" --json name \
       | jq -r '.[].name' | grep -qx "$NAME"; then
    skip "label: $NAME"
  else
    gh label create "$NAME" \
      --repo "$OWNER/$REPO" \
      --color "$COLOR" \
      --description "$DESC" 2>/dev/null
    ok "label: $NAME"
  fi
done
echo ""

# ── 2. Milestones ─────────────────────────────────────────────────────────────
log "Milestones"
# No associative arrays — bash 3.2 (macOS default) doesn't support declare -A.
# Milestone numbers are resolved by API lookup at issue creation time.
MILESTONE_COUNT=$(yq '.milestones | length' "$CONFIG")
for i in $(seq 0 $((MILESTONE_COUNT - 1))); do
  TITLE=$(yq ".milestones[$i].title" "$CONFIG")
  DESC=$(yq ".milestones[$i].description" "$CONFIG")

  EXISTING=$(gh api "repos/$OWNER/$REPO/milestones" \
    | jq -r ".[] | select(.title == \"$TITLE\") | .number")

  if [ -n "$EXISTING" ]; then
    skip "milestone: $TITLE (#$EXISTING)"
  else
    NUMBER=$(gh api "repos/$OWNER/$REPO/milestones" \
      --method POST \
      --field title="$TITLE" \
      --field description="$DESC" \
      | jq -r '.number')
    ok "milestone: $TITLE (#$NUMBER)"
  fi
done
echo ""


# ── 3. Org project ────────────────────────────────────────────────────────────
log "Org project: $ORG_PROJECT"

EXISTING_PROJECT=$(gh project list --owner "$OWNER" --format json \
  | jq -r ".projects[] | select(.title == \"$ORG_PROJECT\") | .number" 2>/dev/null || true)

if [ -n "$EXISTING_PROJECT" ]; then
  skip "project: $ORG_PROJECT (#$EXISTING_PROJECT)"
  PROJECT_NUMBER="$EXISTING_PROJECT"
else
  PROJECT_NUMBER=$(gh project create \
    --owner "$OWNER" \
    --title "$ORG_PROJECT" \
    --format json | jq -r '.number')
  ok "created project: $ORG_PROJECT (#$PROJECT_NUMBER)"
fi

# Link this repo to the org project
gh project link "$PROJECT_NUMBER" \
  --owner "$OWNER" \
  --repo "$OWNER/$REPO" 2>/dev/null && ok "linked $OWNER/$REPO" || skip "repo link"

echo ""

# ── 4. Issues ─────────────────────────────────────────────────────────────────
log "Issues"
ISSUE_COUNT=$(yq '.issues | length' "$CONFIG")
for i in $(seq 0 $((ISSUE_COUNT - 1))); do
  TITLE=$(yq ".issues[$i].title" "$CONFIG")
  BODY=$(yq ".issues[$i].body" "$CONFIG")
  MILESTONE_TITLE=$(yq ".issues[$i].milestone" "$CONFIG")

  # Build labels arg
  LABEL_ARGS=""
  LABEL_LIST=$(yq ".issues[$i].labels[]" "$CONFIG" 2>/dev/null || true)
  for lbl in $LABEL_LIST; do
    LABEL_ARGS="$LABEL_ARGS --label $lbl"
  done

  # Check if issue already exists
  EXISTING_ISSUE=$(gh issue list \
    --repo "$OWNER/$REPO" \
    --state all \
    --json title,number \
    | jq -r ".[] | select(.title == \"$TITLE\") | .number")

  if [ -n "$EXISTING_ISSUE" ]; then
    skip "issue: $TITLE (#$EXISTING_ISSUE)"
    ISSUE_NUMBER="$EXISTING_ISSUE"
  else
    # gh issue create prints the issue URL on success — parse number from it
    # --milestone accepts the milestone name directly (no number lookup needed)
    # shellcheck disable=SC2086
    ISSUE_URL=$(gh issue create \
      --repo "$OWNER/$REPO" \
      --title "$TITLE" \
      --body "$BODY" \
      --milestone "$MILESTONE_TITLE" \
      $LABEL_ARGS)
    ISSUE_NUMBER=$(basename "$ISSUE_URL")
    ok "issue #$ISSUE_NUMBER: $TITLE"
  fi

  # Add to org project
  ISSUE_URL="https://github.com/$OWNER/$REPO/issues/$ISSUE_NUMBER"
  gh project item-add "$PROJECT_NUMBER" \
    --owner "$OWNER" \
    --url "$ISSUE_URL" 2>/dev/null || true
done
echo ""

# ── 5. Summary ────────────────────────────────────────────────────────────────
echo "============================================="
echo " Done: $OWNER/$REPO"
echo " Repo issues: https://github.com/$OWNER/$REPO/issues"
echo " Org project: https://github.com/orgs/$OWNER/projects/$PROJECT_NUMBER"
echo "============================================="
echo ""
echo "To set up gora: run this script from the gora repo."
echo "Both repos will share the same org project view."
