#!/usr/bin/env bash
# Re-create the Kairos integration branch from main by merging every feature branch in order.
#
#   scripts/integrate.sh               # rebuild integration/all from origin/main
#   scripts/integrate.sh --resume      # after resolving a conflict by hand: continue where it stopped
#   scripts/integrate.sh --no-glue     # merges only, do not replay the integration commits
#   scripts/integrate.sh --branch NAME # build into another local branch (default integration/all)
#   scripts/integrate.sh --dry-run     # list what would be merged, change nothing
#
# What it does
#   1. Fetches origin and remembers the current tip of the integration branch (local, else origin).
#   2. Resets the branch to origin/main (unless --resume).
#   3. Merges each branch in BRANCHES with `git merge --no-ff`. Missing branches are skipped;
#      branches already contained in HEAD are skipped. git rerere is switched on, so a conflict that
#      was resolved once on this machine is resolved the same way again automatically.
#      An unresolved conflict stops the script (exit 2) and prints the files: fix them, `git add`,
#      `git commit --no-edit`, then run again with --resume.
#   4. Replays the "glue" commits from the old tip: non-merge commits whose subject starts with
#      "integration: " (scrubber unification, template caller fixes, the journey, INTEGRATION.md).
#      Glue commits already present (same subject) are skipped. A cherry-pick conflict stops the
#      script: resolve, `git cherry-pick --continue`, then run again with --resume.
#
# Nothing is pushed. Run the tests (see INTEGRATION.md), then `git push -f origin integration/all`.
set -euo pipefail

# The checkout below replaces this very file; run from a temporary copy so bash keeps reading it.
if [ -z "${KAIROS_INTEGRATE_COPY:-}" ]; then
  REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
  tmp="$(mktemp "${TMPDIR:-/tmp}/integrate.XXXXXX")"
  cp "$0" "$tmp"
  cd "$REPO_ROOT"
  KAIROS_INTEGRATE_COPY="$tmp" exec bash "$tmp" "$@"
fi
trap 'rm -f "$KAIROS_INTEGRATE_COPY"' EXIT

# Merge order (see INTEGRATION.md). Add new branches here, in the order they should land.
BRANCHES=(
  docs/claude-context          # 7
  k09/on-spine                 # 11
  k09/optimize                 # 17 (stacked on 11; also carries the opt-out work)
  k17-k18/voice-engine         # 15 (stacked on 11)
  k06/hitl-control             # 12
  k26/report-pack              # 13
  redteam/guardrail-suite      # 14
  redteam/fix-high             # 16
  redteam/fix-med              # MED fixes (skipped while missing / already contained)
  k07-k08/channel-adapters     # 10
  k22/template-selector        # 8
  feat/k05-k14-crm-voice       # 6
  k20/email-channel            # K20 email adapter
  cm03/whatsapp-servicing      # CM-03 WhatsApp servicing agent (skipped while missing)
  k09/intent-stack             # free-text intent stack (journey uses it when present)
  k27/auto-churn               # auto-churn cohort engine (journey uses it when present)
  k06/deterministic-states     # K06 deterministic states
  frontend/live-api            # frontend wired to the live API
  k07-k08/android-sms          # Android SMS adapter (if not pushed into k07-k08/channel-adapters)
)

BASE="origin/main"
TARGET="integration/all"
RESUME=0
GLUE=1
DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --resume) RESUME=1 ;;
    --no-glue) GLUE=0 ;;
    --dry-run) DRY=1 ;;
    --branch) TARGET="$2"; shift ;;
    --base) BASE="$2"; shift ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 64 ;;
  esac
  shift
done

cd "$(git rev-parse --show-toplevel)"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "working tree has uncommitted changes; commit or stash first" >&2
  exit 1
fi
if [ -d "$(git rev-parse --git-path MERGE_HEAD)" ] || [ -f "$(git rev-parse --git-path MERGE_HEAD)" ]; then
  echo "a merge is in progress; finish it (git commit --no-edit) or abort it first" >&2
  exit 1
fi

git fetch --quiet origin
git config rerere.enabled true
git config rerere.autoupdate true

OLD_TIP=""
if git rev-parse --verify --quiet "refs/heads/$TARGET" >/dev/null; then
  OLD_TIP="$(git rev-parse "refs/heads/$TARGET")"
elif git rev-parse --verify --quiet "refs/remotes/origin/$TARGET" >/dev/null; then
  OLD_TIP="$(git rev-parse "refs/remotes/origin/$TARGET")"
fi
# Keep the old tip reachable so its glue commits can be replayed even after the reset.
if [ -n "$OLD_TIP" ] && [ "$RESUME" = 0 ] && [ "$DRY" = 0 ]; then
  git update-ref "refs/integrate/previous" "$OLD_TIP"
fi
GLUE_SRC="$(git rev-parse --verify --quiet refs/integrate/previous || true)"

if [ "$DRY" = 1 ]; then
  for b in "${BRANCHES[@]}"; do
    if git rev-parse --verify --quiet "refs/remotes/origin/$b" >/dev/null; then
      echo "merge  $b  (+$(git rev-list --count "$BASE..origin/$b") commits over $BASE)"
    else
      echo "skip   $b  (no such branch on origin)"
    fi
  done
  [ -n "$GLUE_SRC" ] && git log --reverse --no-merges --format='glue   %h %s' --grep='^integration: ' "$BASE..$GLUE_SRC"
  exit 0
fi

if [ "$RESUME" = 0 ]; then
  git checkout --quiet -B "$TARGET" "$BASE"
  echo "reset $TARGET to $BASE ($(git rev-parse --short "$BASE"))"
else
  git checkout --quiet "$TARGET"
  echo "resuming on $TARGET at $(git rev-parse --short HEAD)"
fi

for b in "${BRANCHES[@]}"; do
  ref="refs/remotes/origin/$b"
  if ! git rev-parse --verify --quiet "$ref" >/dev/null; then
    echo "skip   $b  (no such branch on origin)"
    continue
  fi
  if git merge-base --is-ancestor "$ref" HEAD; then
    echo "have   $b  (already contained)"
    continue
  fi
  echo "merge  $b"
  if ! git merge --no-ff --no-edit -m "Merge origin/$b into $TARGET" "origin/$b" >/dev/null 2>&1; then
    if [ -z "$(git diff --name-only --diff-filter=U)" ]; then
      git commit --no-edit --quiet
      echo "       conflicts resolved from the rerere cache"
    else
      echo "CONFLICT merging $b. Unresolved files:" >&2
      git diff --name-only --diff-filter=U | sed 's/^/  /' >&2
      echo "Resolve them, git add, git commit --no-edit, then: scripts/integrate.sh --resume" >&2
      exit 2
    fi
  fi
done

if [ "$GLUE" = 1 ] && [ -n "$GLUE_SRC" ]; then
  have_subjects="$(git log --no-merges --format=%s "$BASE..HEAD")"
  for c in $(git rev-list --reverse --no-merges --grep='^integration: ' "$BASE..$GLUE_SRC"); do
    subj="$(git log -1 --format=%s "$c")"
    if printf '%s\n' "$have_subjects" | grep -Fxq -- "$subj"; then
      continue
    fi
    echo "glue   $(git rev-parse --short "$c") $subj"
    if ! git cherry-pick --allow-empty "$c" >/dev/null 2>&1; then
      if [ -z "$(git diff --name-only --diff-filter=U)" ]; then
        git -c core.editor=true cherry-pick --continue >/dev/null
      else
        echo "CONFLICT replaying glue commit $c. Unresolved files:" >&2
        git diff --name-only --diff-filter=U | sed 's/^/  /' >&2
        echo "Resolve, git add, git cherry-pick --continue, then: scripts/integrate.sh --resume" >&2
        exit 2
      fi
    fi
  done
fi

echo "done: $TARGET at $(git rev-parse --short HEAD). Now run the checks in INTEGRATION.md, then push."
