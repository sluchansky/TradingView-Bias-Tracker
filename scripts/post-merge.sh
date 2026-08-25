#!/bin/bash
set -euo pipefail

# Keep post-merge setup non-interactive and non-destructive. Drizzle's `push`
# asks for confirmation when it detects destructive schema changes; post-merge
# runs with stdin=/dev/null, so it either fails or could be replaced with a
# dangerous --force push. Schema changes are applied through the reviewed
# database/publish flow instead. This hook only reconciles dependencies.
pnpm install --frozen-lockfile
