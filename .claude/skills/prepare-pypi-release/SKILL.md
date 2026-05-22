---
name: prepare-pypi-release
description: Bump version, update CHANGELOG, build and validate the PyPI distribution for abench-speckz. Run when the user asks to prepare, cut, or publish a release.
---

Prepare abench-speckz for a PyPI release. Follow these steps in order.

## Step 1 — Determine the new version

Check what has changed since the last release tag:

```bash
# What version is current?
grep '^version' pyproject.toml

# What commits are unreleased?
git log $(git describe --tags --abbrev=0 2>/dev/null || git rev-list --max-parents=0 HEAD)..HEAD --oneline
```

Apply semantic versioning to the unreleased commits:
- **patch** (0.x.Z) — bug fixes only
- **minor** (0.X.0) — any new feature, new field, or new behavior (backward-compatible)
- **major** (X.0.0) — breaking changes to the YAML schema, CLI flags, or public API

If the user specifies a version explicitly, use that.

## Step 2 — Update the three version locations

Version is stored in three places — all must match:

| File | Field |
|---|---|
| `pyproject.toml` | `version = "X.Y.Z"` |
| `src/abench_speckz/__init__.py` | `__version__ = "X.Y.Z"` |
| `CHANGELOG.md` | Replace `## [Unreleased]` with `## [Unreleased]\n\n## [X.Y.Z] - YYYY-MM-DD` |

Today's date for the CHANGELOG entry:

```bash
date +%Y-%m-%d
```

### CHANGELOG format

Move all content from the `## [Unreleased]` section into the new `## [X.Y.Z]` entry. If `[Unreleased]` is empty, summarise the unreleased commits yourself under the appropriate headings (`### Added`, `### Fixed`, `### Changed`, `### Removed`). Keep `## [Unreleased]` as an empty placeholder above the new entry.

## Step 3 — Run tests

```bash
source .venv/bin/activate
pytest -x -q
```

All tests must pass before proceeding. Fix any failures first.

## Step 4 — Build the distribution

```bash
source .venv/bin/activate
pip install build twine -q
python -m build
```

Expected output ends with:
```
Successfully built abench_speckz-X.Y.Z.tar.gz and abench_speckz-X.Y.Z-py3-none-any.whl
```

## Step 5 — Validate the build

```bash
source .venv/bin/activate
twine check dist/abench_speckz-X.Y.Z*
```

Both the `.whl` and `.tar.gz` must show `PASSED`. If either fails, fix the issue (usually a malformed README or missing metadata) before committing.

## Step 6 — Commit the version bump

Stage only the three version files (do not stage `dist/`):

```bash
git add pyproject.toml src/abench_speckz/__init__.py CHANGELOG.md
git commit -m "Bump version to X.Y.Z, update CHANGELOG"
```

## Step 7 — Report to the user

Tell the user:
- The new version
- The dist files produced (`dist/abench_speckz-X.Y.Z.tar.gz`, `dist/abench_speckz-X.Y.Z-py3-none-any.whl`)
- The publish command to run manually when ready:

```bash
twine upload dist/abench_speckz-X.Y.Z*
# or, for TestPyPI first:
twine upload --repository testpypi dist/abench_speckz-X.Y.Z*
```

And the tag command if they want to push a git tag:

```bash
git tag vX.Y.Z
git push origin main --tags
```

## Gotchas

- **`dist/` is not in `.gitignore`** — do not `git add dist/` or it will end up in the repo.
- **Version must match in all three files.** A mismatch between `pyproject.toml` and `__init__.py` will confuse users importing the package.
- **Do not amend published commits.** Once a version tag is pushed, create a new patch release for any follow-up fixes.
- **`python -m build` uses the installed setuptools**, not the venv's source tree. Make sure `pip install -e '.[dev]'` was run in the active venv.
