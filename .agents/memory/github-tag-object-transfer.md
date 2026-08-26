---
name: GitHub tag object transfer
description: How to publish an immutable release tag when the validated commit exists only locally.
---

GitHub REST can create a tag ref only when its target object already exists in the remote repository. For a validated local-only commit, publish an annotated tag with a tag-only Git push; Git transfers the necessary commit objects without updating any branch.

**Why:** The repository's legacy Git remote credential can become stale even while the authorized GitHub REST connector remains healthy. The REST connector cannot upload arbitrary local Git objects, so it cannot tag a local-only SHA directly.

**How to apply:** Verify the worktree is clean and the exact SHA has passed the gate; create an annotated local tag; push only `refs/tags/<tag>` through an authorized, non-interactive Git credential path; then verify the remote peeled tag resolves to the validated SHA. Never force-update or retarget an existing release tag.

The Replit `GITHUB_TOKEN` environment variable can shadow a stored `gh` OAuth login. For release pushes, omit both `GITHUB_TOKEN` and `GH_TOKEN` from the Git process and invoke `gh auth git-credential` over direct HTTPS; the stored login must include `workflow` when the transferred history contains workflow files.

**Why:** A token can authenticate successfully and still be rejected during receive-pack because GitHub checks workflow permission while accepting the object history. A tag-only dry run validates the ref shape without creating the tag, but the real push must use the correctly scoped stored account.

**How to apply:** Inspect `gh auth status` with environment credentials omitted, confirm the stored account lists `workflow`, run a tag-only `git push --dry-run`, then perform only the exact tag ref push and compare all pre-existing refs afterward.