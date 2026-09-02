# Legacy Docker build files

The files in this directory are **not built by CI** (see
`.github/workflows/docker-build-publish.yml`, which only builds
`Dockerfile.modern`, `Dockerfile.batch`, and `Dockerfile.native-arm64`) and **not referenced by**
`integral docker build` (`src/integral_cli/docker_mgr.py`, which only knows about
`Dockerfile.native-arm64` and `Dockerfile.modern`). They are kept for historical reference only.

- `Dockerfile` — the original CentOS 7 baseline build this project started from.
- `Dockerfile.apple-silicon`, `Dockerfile.arm64` — earlier ARM64 build iterations, superseded by
  `docker/Dockerfile.native-arm64` (see `docs/technical_rebuild_arm64.md` for the patches that made
  the native build work).
- `Dockerfile.x86` — an earlier x86_64 baseline, superseded by `docker/Dockerfile.modern`.
- `osa-container.sh`, `osa-docker.sh`, `run.sh` — pre-modernisation launcher scripts inherited from
  the upstream `ISDC-integral/osa-docker` project. They target `isdc.unige.ch`/
  `gitlab.astro.unige.ch`, which are no longer available (see `docs/reference_material.md`), and
  duplicate what `src/integral_cli/docker_mgr.py`'s `run_container()` now does.

If you need the current, actively-built container definitions, see the parent `docker/` directory
and `CLAUDE.md`'s "Docker images" section.
