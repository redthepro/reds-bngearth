---
name: bngearth-release-integration
description: Integrate upstream BeamNG.Earth changes into this fork or handle versioning, CI failures, executable/server packaging and release verification.
---

# Upstream integration and releases

Start with the current branch/worktrees, remotes, `VERSION`, `CHANGELOG.md` and the actual workflow files.
The usual remotes are fork `redthepro/reds-bngearth` and upstream `TecnaGamer/BeamNG.Earth`; verify their URLs.

## Integrate upstream

1. Start from a clean or isolated checkout, fetch the intended upstream branch and identify the merge base.
2. Resolve semantics, not just markers. Retain compatible improvements from both sides: camera/driving priorities,
   deferred imagery, resource budgets, native API registration, courtyards/parts, mapped lane widths, lights,
   social state, peer authentication and the fork update channel.
3. Review automatically merged callers and cache keys. A changed helper signature can leave syntactically valid but
   broken call sites. Keep packaging lists as a union where both dependencies are needed (for example `apppaths.py`
   and `UPDATE_REPO`). Keep the fork's version; upstream's number describes inherited work.
4. Rebuild native terrain modules for both platforms when the API changes; match `server/fast.py` and the Rust API
   version. Preserve optional native voice builds. Never replace loaded DLL/SO bytes in place.
5. Run focused conflict regressions, then broad checks appropriate to the merge. Confirm both upstream and the
   authorized fork changes are ancestors of the delivery commit. Preserve upstream attribution in README/changelog.

## Publish through CI

- `.github/workflows/release.yml` runs on every `main` push, calls cross-platform tests, builds/smoke-tests the EXE and
  AppImage, packages the BeamMP server and publishes checksums. `tests.yml` is the source of exact CI setup commands.
- A version bump needs matching release notes. Named tags use `v<VERSION>`; commits after that tag exists use
  `v<VERSION>-build.<run-number>.g<sha>`. The computed version must reach both executables, server ZIP and metadata.
- Inspect in-flight runs before pushing another commit for an unreleased version. The current workflow retries an
  absent named tag, so two overlapping runs can select the same name. Finish/inspect the existing named release first
  when practical; do not invent a documentation-only application bump or force-move a tag to avoid the race.
- Failed checks prevent publication. Do not disable required checks, cancel unrelated runs or replace tested release
  artifacts with a local build. If a failure is an asynchronous race, reproduce the condition and await completion
  rather than loosening behavior assertions or adding a fixed sleep.
- For failures, inspect the failing job and uploaded test-log artifacts; the console often omits the traceback.
  Report the relevant run URL, commit and remaining work.
- The publishing job alone gets release write access. A historical build with workflow files differing from current
  main can hit GitHub's workflow-write restriction. Preserve the token boundary; do not export a maintainer token into
  CI or rewrite workflow history as a workaround. An already-authorized maintainer can publish verified artifacts.
- Older runs must not replace the current main build as Latest. Preserve the workflow's head check and prerelease rules.

## Verify deliverables

Check GitHub run success, tag/target commit, draft/prerelease state and all four assets: EXE, AppImage, server ZIP and
`SHA256SUMS.txt`. Compare downloaded checksums with asset hashes/digests. Packaging tests must retain the fork's
`UPDATE_REPO`, optional launcher imports, social modules, installer and voice service. Smoke-test the frozen
`--server --help`; include a real bridge/UI check for cross-language changes.

Keep local test builds labeled as such, record their commit/hash, and distinguish them from published releases.
Do not rebuild the entire application solely to validate Markdown changes.
