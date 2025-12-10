# GitHub Actions Setup Guide

## Overview

Three automated workflows have been created for your project:

1. **tests.yml** - Runs tests on every push and PR
2. **publish.yml** - Publishes to PyPI on version tags
3. **codeql.yml** - Security scanning with CodeQL

---

## Workflow Details

### 1. Tests Workflow (tests.yml)

**Triggers:**
- Every push to `main` or `develop` branch
- Every pull request to `main` or `develop`

**What it does:**
- Runs tests on 3 operating systems: Ubuntu, macOS, Windows
- Tests on Python 3.10, 3.11, 3.12 (9 total combinations)
- Verifies package structure with `verify.py`
- Runs unit tests with pytest
- Runs benchmarks (Ubuntu + Python 3.12 only)
- Generates code coverage report
- Uploads coverage to Codecov

**Status:** Green check (✅) on PR means all tests passed!

### 2. Publish Workflow (publish.yml)

**Triggers:**
- When you push a version tag (e.g., `v0.1.0`, `v0.2.0`)

**What it does:**
1. **Build Job:**
   - Checks out code
   - Verifies package with `verify.py`
   - Runs all tests
   - Builds wheel and source distribution
   - Validates build with twine

2. **Publish Job:**
   - Publishes to PyPI (uses OIDC Trusted Publisher)
   - Creates GitHub Release with built files
   - Includes installation instructions in release notes

**How to trigger:**
```bash
git tag v0.2.0
git push origin v0.2.0
```

### 3. CodeQL Workflow (codeql.yml)

**Triggers:**
- Every push to `main` or `develop`
- Every PR
- Weekly (Sunday at midnight)

**What it does:**
- Analyzes Python code for security vulnerabilities
- Reports findings to GitHub Security tab
- Runs automatically, no action needed

---

## Setup Instructions

### Step 1: Verify Files Exist

The three workflow files have been created in `.github/workflows/`:
```
.github/
└── workflows/
    ├── tests.yml
    ├── publish.yml
    └── codeql.yml
```

### Step 2: Create CHANGELOG.md

The CHANGELOG.md file has been created with release notes.

### Step 3: Configure PyPI Publishing (Optional but Recommended)

For automatic PyPI publishing, you have two options:

**Option A: Trusted Publisher (Recommended)**
1. Go to PyPI: https://pypi.org/manage/account/publishing/
2. Click "Add a new pending publisher"
3. Fill in:
   - PyPI Project Name: `advanced-caching`
   - GitHub Repository Owner: Your GitHub username
   - Repository Name: `advanced_caching` (replace with your repo name)
   - Workflow Name: `publish.yml`
   - Environment Name: (leave empty)
4. Verify with GitHub
5. Done! No token needed

**Option B: API Token (Backup method)**
1. Go to PyPI: https://pypi.org/manage/account/tokens/
2. Click "Create token"
3. Name: "GitHub Actions"
4. Scope: "entire repository"
5. Copy the token
6. Go to GitHub repo → Settings → Secrets and variables → Actions
7. Click "New repository secret"
8. Name: `PYPI_API_TOKEN`
9. Value: Paste the token

---

## Publishing a New Version

### Step 1: Update Version

Edit `pyproject.toml`:
```toml
[project]
version = "0.2.0"  # Change from 0.1.0
```

### Step 2: Update CHANGELOG

Add entry to `CHANGELOG.md` under `## [Unreleased]`:

```markdown
## [0.2.0] - 2025-12-11

### Added
- New feature 1
- New feature 2

### Fixed
- Bug fix 1
```

Then move it to the proper dated section.

### Step 3: Commit Changes

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "Release v0.2.0: New features"
git push origin main
```

### Step 4: Create Release Tag

```bash
git tag v0.2.0
git push origin v0.2.0
```

**That's it!** The workflow automatically:
- ✅ Runs all tests
- ✅ Builds the package
- ✅ Publishes to PyPI
- ✅ Creates GitHub Release
- ✅ Uploads build artifacts

### Step 5: Monitor (Optional)

Watch the workflow progress:
1. Go to GitHub repo → Actions tab
2. Click on the "Publish to PyPI" workflow
3. Watch jobs: Build → Publish
4. Once complete, check PyPI: https://pypi.org/project/advanced-caching/

---

## Complete Publish Example

```bash
# 1. Update version
sed -i '' 's/version = "0.1.0"/version = "0.2.0"/' pyproject.toml

# 2. Add CHANGELOG entry (manually edit CHANGELOG.md)

# 3. Commit
git add pyproject.toml CHANGELOG.md
git commit -m "Release v0.2.0: New features and improvements"
git push origin main

# 4. Tag and push (TRIGGERS WORKFLOW)
git tag v0.2.0
git push origin v0.2.0

# 5. Done! Check GitHub Actions tab for progress
# Package will appear on PyPI in 2-5 minutes
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Tests fail in CI | Run `pytest tests/test_correctness.py -v` locally first |
| PyPI publish fails | Ensure Trusted Publisher is configured or API token is set |
| Release not on PyPI | Wait 2-5 minutes, PyPI caches updates |
| Tag not triggering | Ensure tag format is `v*` (e.g., `v0.1.0`) |
| Can't find workflow runs | Check GitHub repo → Actions tab |

---

## Workflow Status Badges

Add these to your README.md for status badges:

```markdown
[![Tests](https://github.com/YOUR_USERNAME/advanced_caching/workflows/Tests/badge.svg)](https://github.com/YOUR_USERNAME/advanced_caching/actions/workflows/tests.yml)
[![Publish](https://github.com/YOUR_USERNAME/advanced_caching/workflows/Publish%20to%20PyPI/badge.svg)](https://github.com/YOUR_USERNAME/advanced_caching/actions/workflows/publish.yml)
```

Replace `YOUR_USERNAME` with your GitHub username.

---

## Next Steps

1. ✅ Workflows are set up
2. ✅ CHANGELOG.md created
3. ⏭️ Push to GitHub
4. ⏭️ Configure Trusted Publisher (optional)
5. ⏭️ Test with a release (tag v0.1.0 or v0.1.1)

---

## Files Created

- `.github/workflows/tests.yml` - Test automation
- `.github/workflows/publish.yml` - PyPI publishing
- `.github/workflows/codeql.yml` - Security scanning
- `CHANGELOG.md` - Release notes and version history

All files are ready to use!

