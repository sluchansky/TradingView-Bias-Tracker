---
name: GitHub local-object transfer
description: How to transfer an exact local-only commit for remote validation or an immutable release tag.
---

GitHub REST can create a tag ref only when its target object already exists in the remote repository. For a validated local-only commit, publish an annotated tag with a tag-only Git push; Git transfers the necessary commit objects without updating any branch.

**Why:** The repository's legacy Git remote credential can become stale even while the authorized GitHub REST connector remains healthy. The REST connector cannot upload arbitrary local Git objects, so it cannot tag a local-only SHA directly.

**How to apply:** Verify the worktree is clean and the exact SHA has passed the gate; create an annotated local tag; push only `refs/tags/<tag>` through an authorized, non-interactive Git credential path; then verify the remote peeled tag resolves to the validated SHA. Never force-update or retarget an existing release tag.

The Replit `GITHUB_TOKEN` environment variable or `GIT_ASKPASS` can shadow a stored `gh` OAuth login. For release pushes, omit both from the Git process and use the `gh` credential helper over direct HTTPS; the stored login must include `workflow` when the transferred history contains workflow files.

**Why:** A token can authenticate successfully and still be rejected during receive-pack because GitHub checks workflow permission while accepting the object history. A tag-only dry run validates the ref shape without creating the tag, but the real push must use the correctly scoped stored account.

**How to apply:** Inspect `gh auth status` with environment credentials omitted and confirm `workflow`. If `gh auth setup-git` cannot write the runtime global config, point `GIT_CONFIG_GLOBAL` at a temporary file for setup and push. In a partial/promisor checkout, the push may lazily fetch objects from Replit's internal SSH remote; use non-interactive strict host verification (`StrictHostKeyChecking=accept-new`) rather than allowing a host-key prompt to hang. Push only the exact tag ref, then compare protected branch refs and the remote peeled tag afterward.

For exact-commit CI validation, push only a temporary branch ref at the reviewed SHA. When the workflow file itself is new to GitHub, the first push may register the workflow without scheduling it; after confirming the workflow is active, delete and recreate the same temporary ref at the same SHA to generate the push run. Remove the branch after the run completes; the Actions record remains.

**Why:** GitHub cannot run a workflow for an object it does not have, and REST ref creation cannot upload the missing Git objects. Registering a workflow from a non-default branch may also precede its first scheduled push run.

**How to apply:** Confirm protected branches before and after, authenticate Git through `gh` with `repo` and `workflow` scopes, push only `HEAD:refs/heads/<temporary-validation-branch>`, verify the run's `headSha`, and delete only that temporary ref after preserving the run URL.