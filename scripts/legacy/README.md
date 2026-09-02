# Legacy scripts

The files in this directory are **not wired into `pyproject.toml`'s `[project.scripts]`** and are
not referenced from the README, CI, or any other script. They are kept for historical reference
only.

- `osa_run` — a bash launcher superseded by `integral docker run`
  (`src/integral_cli/docker_mgr.py`). It has also drifted from the rest of the project: it
  defaults to image tags (`integralsw/osa:latest-arm64`/`latest-x86`) that don't match what
  `docker_mgr.py`/CI actually produce (`integralsw/osa:11-native-arm64`/`11-modern-amd64`).
- `setup_local_env.sh` — a bash directory-layout initialiser superseded by `integral data init`
  (`src/integral_cli/data_mgr.py`).

If you need the current, actively-used equivalents, use the `integral` CLI (see the "Commands"
section of `CLAUDE.md`).
