---
name: GitHub tag object transfer
description: How to publish an immutable release tag when the validated commit exists only locally.
---

GitHub REST can create a tag ref only when its target object already exists in the remote repository. For a validated local-only commit, publish an annotated tag with a tag-only Git push; Git transfers the necessary commit objects without updating any branch.

**Why:** The repository's legacy Git remote credential can become stale even while the authorized GitHub REST connector remains healthy. The REST connector cannot upload arbitrary local Git objects, so it cannot tag a local-only SHA directly.

**How to apply:** Verify the worktree is clean and the exact SHA has passed the gate; create an annotated local tag; push only `refs/tags/<tag>` through an authorized, non-interactive Git credential path; then verify the remote peeled tag resolves to the validated SHA. Never force-update or retarget an existing release tag.