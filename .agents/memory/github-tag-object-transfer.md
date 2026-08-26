---
name: GitHub tag object transfer
description: How to publish an immutable release tag when the validated commit exists only locally.
---

GitHub REST can create a tag ref only when its target object already exists in the remote repository. For a validated local-only commit, publish an annotated tag with a tag-only Git push; Git transfers the necessary commit objects without updating any branch.

**Why:** The repository's legacy Git remote credential can become stale even while the authorized GitHub REST connector remains healthy. The REST connector cannot upload arbitrary local Git objects, so it cannot tag a local-only SHA directly.

**How to apply:** Verify the worktree is clean and the exact SHA has passed the gate; create an annotated local tag; push only `refs/tags/<tag>` through an authorized, non-interactive Git credential path; then verify the remote peeled tag resolves to the validated SHA. Never force-update or retarget an existing release tag.

The Replit `GITHUB_TOKEN` environment variable or `GIT_ASKPASS` can shadow a stored `gh` OAuth login. For release pushes, omit both from the Git process and use the `gh` credential helper over direct HTTPS; the stored login must include `workflow` when the transferred history contains workflow files.

**Why:** A token can authenticate successfully and still be rejected during receive-pack because GitHub checks workflow permission while accepting the object history. A tag-only dry run validates the ref shape without creating the tag, but the real push must use the correctly scoped stored account.

**How to apply:** Inspect `gh auth status` with environment credentials omitted and confirm `workflow`. If `gh auth setup-git` cannot write the runtime global config, point `GIT_CONFIG_GLOBAL` at a temporary file for setup and push. In a partial/promisor checkout, the push may lazily fetch objects from Replit's internal SSH remote; use non-interactive strict host verification (`StrictHostKeyChecking=accept-new`) rather than allowing a host-key prompt to hang. Push only the exact tag ref, then compare protected branch refs and the remote peeled tag afterward.