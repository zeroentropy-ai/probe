# Contributing

Thanks for helping improve probe.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Local Checks

Run these before opening a pull request:

```bash
python -m pytest
ruff check .
python -m build
twine check dist/*
```

For an install smoke test, build a wheel and install it into a fresh environment:

```bash
rm -rf /tmp/probe-wheel-smoke
python -m venv /tmp/probe-wheel-smoke
/tmp/probe-wheel-smoke/bin/python -m pip install dist/probe_search-*.whl
/tmp/probe-wheel-smoke/bin/probe --version
/tmp/probe-wheel-smoke/bin/probe mcp --help
```

## Release Checklist

1. Update `pyproject.toml`, `src/probe/__init__.py`, and `CHANGELOG.md`.
2. Run the local checks above.
3. Merge to the default branch (`main`, or `master` if that is the repo default).
4. The PyPI publishing workflow builds from `main`/`master` and publishes only when that version is not already on PyPI.
