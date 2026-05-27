# Release Process

probe publishes to PyPI from GitHub Actions using PyPI Trusted Publishing.

## One-Time PyPI Setup

Configure the existing `probe-search` PyPI project with a trusted publisher:

- Owner: `zeroentropy-ai`
- Repository name: `probe`
- Workflow filename: `publish.yml`
- Environment name: `pypi`

In GitHub, create a `pypi` environment for the repository. Keep required
reviewers enabled if you want a manual approval gate before publishing.

No long-lived PyPI token is needed for CI. The workflow requests an OIDC token
from GitHub and PyPI exchanges it for a short-lived publishing credential.

## Publishing

1. Update the version in `pyproject.toml` and `src/probe/__init__.py`.
2. Add a `CHANGELOG.md` entry.
3. Run local verification:

   ```bash
   python -m pytest
   ruff check .
   python -m build
   twine check dist/*
   ```

4. Merge to the default branch (`main`, or `master` if that is the repo default).

The publish workflow runs on `main` and `master`, smoke-tests the wheel, checks
whether that version already exists on PyPI, and publishes only when the version
is new.

## Manual Release Smoke Test

After publish, verify PyPI and installation:

```bash
python -m venv /tmp/probe-pypi-smoke
/tmp/probe-pypi-smoke/bin/python -m pip install --no-cache-dir probe-search
/tmp/probe-pypi-smoke/bin/probe --version
/tmp/probe-pypi-smoke/bin/probe mcp --help
```
