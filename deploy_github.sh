#!/usr/bin/env bash
# One-command deploy of the Pattern Scout paper bot to GitHub (Actions + Pages).
# Usage: ./deploy_github.sh <github-username> [repo-name]
set -euo pipefail

USER="${1:-}"
REPO="${2:-pattern-scout-eth}"
if [ -z "$USER" ]; then
  echo "Uso: ./deploy_github.sh <github-username> [repo-name]"
  exit 1
fi
if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI 'gh' non trovato. Installa: brew install gh && gh auth login"
  exit 1
fi

cd "$(dirname "$0")"

# Init git if needed
if [ ! -d .git ]; then
  git init -q
  git branch -M main
fi
git add -A
git commit -q -m "Pattern Scout paper bot (ETH, paper live)" || echo "Niente da committare."

# Create the repo (public so GitHub Pages is free) and push
if ! gh repo view "$USER/$REPO" >/dev/null 2>&1; then
  gh repo create "$USER/$REPO" --public --source=. --remote=origin --push
else
  git remote add origin "https://github.com/$USER/$REPO.git" 2>/dev/null || true
  git push -u origin main
fi

# Enable Pages via GitHub Actions and grant workflow write permission
gh api -X PUT "repos/$USER/$REPO/actions/permissions/workflow" \
  -f default_workflow_permissions=write -F can_approve_pull_request_reviews=false >/dev/null 2>&1 || true
gh api -X POST "repos/$USER/$REPO/pages" -f "build_type=workflow" >/dev/null 2>&1 || \
  gh api -X PUT "repos/$USER/$REPO/pages" -f "build_type=workflow" >/dev/null 2>&1 || true

# Kick off the first run
gh workflow run "paper-crypto.yml" -R "$USER/$REPO" >/dev/null 2>&1 || true

echo ""
echo "Fatto. Repo: https://github.com/$USER/$REPO"
echo "Dashboard (tra qualche minuto): https://$USER.github.io/$REPO/"
echo "Actions: https://github.com/$USER/$REPO/actions"
