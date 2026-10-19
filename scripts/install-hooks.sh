#!/bin/sh
# Point git at the repo's tracked hooks (works in worktrees too).
set -e
git config core.hooksPath scripts/hooks
echo "Hooks installed (core.hooksPath=scripts/hooks)."
