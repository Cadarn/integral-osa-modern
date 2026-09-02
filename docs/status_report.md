# INTEGRAL OSA Modernisation — Project Status & Handover Report

**Date**: 2 September 2026
**Status**: Native ARM64 recompilation complete and verified | Cloud batch scaffolding scoped, not yet wired up
**Target Environment**: Apple Silicon (macOS ARM64) & Kubernetes Cloud Batch

---

## 1. Executive Summary & Current Status

We have brought the **INTEGRAL Off-line Scientific Analysis (OSA 11)** pipeline from legacy manual
workflows into an automated Python CLI (`integral`) with verified end-to-end scientific reduction,
and completed a full native ARM64 recompilation of the ISDC C/C++/Fortran science binaries — the
piece of work this document previously described as an open decision (see §2) is now finished,
measured, and written up in `docs/technical_rebuild_arm64.md`.

### Key working milestones completed

1. **Full Science Reduction & Mosaicing (18–60 keV Band)**:
   - Successfully executed on **10 Science Windows** (`006000010010` ... `006000100010`) of
     Revolution 0060.
   - Generated **95 scientific FITS products** (`isgri_mosa_ima.fits`, `isgri_mosa_res.fits`,
     single-pointing sky images, variance/exposure maps, shadowgrams).
   - Extracted **10 significant point sources** with clear $\sqrt{N}$ signal-to-noise scaling
     (top detection: **XTE J1550-564** at **$263.9\,\sigma$**, $76.92 \pm 0.29$ cts/s;
     **IGR J16318-4848** at **$43.6\,\sigma$**).
2. **Native ARM64 recompilation — complete and benchmarked** (see `docs/technical_rebuild_arm64.md`
   §5 for the full methodology and per-HDU numerical comparison):
   - **Native ARM64** (`integralsw/osa:11-native-arm64`, `linux/arm64`): **150.44 s** total /
     **15.04 s per ScW** over the same 10-ScW IBIS/ISGRI reduction.
   - **Emulated x86_64 baseline** (`integralsw/osa:11.0` under Rosetta/QEMU, `linux/amd64`):
     **366.44 s** total / **36.64 s per ScW**.
   - **2.44× wall-clock speedup**, with the resulting `isgri_mosa_ima.fits` products matching the
     emulated-baseline run to a relative difference of **<0.07%** (attributable to ARM64 NEON vs
     x86_64 SSE2 floating-point instruction ordering, not a scientific discrepancy).
   - *Note*: an earlier version of this report recorded a different single-run baseline figure for
     the same emulated image (392.18 s / 39.22 s per ScW, ~7% higher than the 366.44 s / 36.64 s
     figure above). Neither run was repeated, so this is most likely ordinary run-to-run variance
     rather than a real regression — but it's a concrete illustration of why §3 below calls for
     repeated runs with reported variance before any number goes into a paper.
   - This single IBIS run is the only benchmark evidence collected so far — JEM-X, OMC, and SPI
     have not yet been benchmarked, and no ScW-count scaling curve (25/50/full-revolution) has been
     run yet either. Extending this into a proper multi-instrument, multi-repeat benchmark matrix
     is scoped in `docs/technical_roadmap.md` as groundwork for the planned MNRAS submission.
3. **Data Archive & Calibration Standardisation**:
   - High-throughput parallel downloader implemented in the Typer CLI (`integral data download`,
     now a `revolution`/`scw`/`file`/`calibration` sub-app rather than a single flat command).
   - All 214 Science Windows of Rev 0060 uncompressed and structured.
   - 115 Instrument Characteristics (IC) calibration indexes and mission reference trees
     (`aux/adp/ref/`: `tcoroffset`, `leap`, `de200`, `irot`) verified and active **for the original
     10-ScW benchmark dataset** (staged via `import-local` from a pre-obtained bundle). This does
     **not** carry over to data fetched fresh via the newer HEASARC-based `data download` command —
     see §3 item 6 below, which found that path currently produces an IC tree that fails calibration
     resolution during a real reduction.
4. **CLI & Visualisation Suite**:
   - `uv run integral analyse ibis` — batch reduction and mosaicing (also `jemx`, `omc`, `spi`).
   - `uv run integral view image` — automatic WCS celestial coordinate rendering with ZScale
     intensity stretching.
   - `uv run integral view sources` — terminal summary tables of detected point sources and fluxes.

---

## 2. Architecture & Benchmark Reality

| Software Layer | Status | Measured Benchmark | Notes |
| :--- | :---: | :---: | :--- |
| **Native ARM64 OSA Pipeline** (`integralsw/osa:11-native-arm64`) | **Complete & verified** | **15.04 s / ScW** (150.44 s for 10 ScWs, IBIS only) | Full native compilation of the ISDC C/Fortran binaries for `aarch64-linux-gnu`; see `docs/technical_rebuild_arm64.md` for the six categories of source patches required. |
| **Emulated x86_64 Baseline** (`integralsw/osa:11.0` via Rosetta/QEMU) | **100% Verified** | **36.64 s / ScW** (366.44 s for 10 ScWs) | Reference baseline used for both the speedup and numerical-consistency comparison above. |
| **Python Tooling Layer** (Host / Native ARM64) | **100% Verified** | **0.23 s** (catalog indexing / WCS render) | Isolated benchmark of the Python/Astropy data I/O layer only — does **not** reflect OSA reduction speed; kept separate to avoid conflating the two in future reporting. |

This table previously framed the native ARM64 build as *"in progress, not yet benchmarked"*, with
Option A (layering Python/`uv` onto the verified CentOS 7 base image) recommended as the safer path
and Option B (full native recompilation) as the unproven alternative. That framing is now out of
date: Option B was completed, and its results are what's reported above.

---

## 3. What's Actually Left to Solve

Now that the native ARM64 build itself is done, the real open items are:

1. **Benchmark coverage is IBIS-only and single-run.** No repeats (so no variance/confidence
   interval on the "2.44×" figure — see the run-to-run discrepancy noted in §1), no per-pipeline-
   stage timing breakdown, and no coverage of JEM-X, OMC, or SPI. This matters for the planned
   MNRAS methods paper — see `docs/technical_roadmap.md` §A for the proposed benchmark matrix and
   automation plan.
2. **No automated test suite.** `pytest`/`pytest-asyncio` are declared as dev dependencies in
   `pyproject.toml`, and `ruff`/`mypy` are configured, but none of the three run anywhere —
   there's no CI job that lints, type-checks, or tests the Python package (CI currently only
   builds and pushes Docker images).
3. **The Kubernetes batch path isn't reconciled with the local CLI.** `pipeline/runner_scw.sh`
   (the in-pod worker driven by `k8s/job-template.yaml`) uses `og_create`/`IC_Group`/`IC_Alias`
   conventions that differ from `src/integral_cli/analysis.py`'s, only handles IBIS/JEM-X (not
   OMC/SPI), and never actually stages data from or uploads results to the `S3_BUCKET`/
   `GCS_BUCKET` the job template configures. Nothing in the CLI submits or monitors a cloud job
   either — see `docs/technical_roadmap.md` §B for the proposed cloud-scalability options.
4. **No GUI beyond the CLI**, and the legacy OSA interactive display tools (which relied on X11
   forwarding) can't run in the slim modernised containers, which strip X11/GUI libraries by
   design. See `docs/technical_roadmap.md` §C for GUI framework options under discussion.
5. **Superseded Dockerfile variants and pre-modernisation launcher scripts have been archived**
   under `docker/legacy/` and `scripts/legacy/` (see the README in each) rather than left mixed in
   with the actively-built/used files.
6. **Data fetched via `integral data download` doesn't currently produce a working reduction.**
   Verified end-to-end: downloaded 5 real ScWs from Rev 0060 plus catalogs, IC index, aux data, and
   the full IBIS/SC/IREM IC calibration trees (~4.3GB), then ran a real `integral analyse ibis`
   reduction against it. It fails during background estimation:
   `Could not open IC Master Group ... status = -2004` / `IC problem for category ISGR-BACK-BKG`.
   Traced this by hand as far as the data allows: the `IC_Alias="OSA"` alias, its
   `ISGR_BACK_BKG=7` version selector, the matching index rows, and the referenced
   `isgr_back_bkg_0007.fits` file are all present, valid, and correctly path-resolvable — yet the
   real ARM64 binary still can't resolve the category. The install guide documents the
   *authoritative* IC distribution as `rsync isdcarc.unige.ch::arc/FTP/arc_dist/ic_tree/prod/`
   (confirmed unreachable — that ISDC service is down), so `data download` necessarily uses an
   unofficial HEASARC mirror instead, which may not be equivalent to what OSA expects. Traced the
   failure into the actual DAL source (`support-sw/isdcroot/icaccess.c`,
   `ICsubIndexGetDOLs`) but the numeric meaning of status `-2004` isn't defined in any bundled
   header, so pinning this down further needs ISDC's own DAL error-code reference or support —
   noted in `README.md`'s Quick Start as a known limitation in the meantime.

## 4. How to Resume Work

1. **Re-verify the baseline science pipeline still reproduces the headline numbers**, using data
   staged via `import-local` from the original verified bundle (not the newer `data download`
   command — see §3 item 6):
   ```bash
   # Run the 10-ScW IBIS reduction benchmark
   uv run integral analyse ibis rev:0060:10 --e-min 18 --e-max 60 --mosaic

   # View the generated mosaic and detected sources
   uv run integral view sources work/obs/obs_ibis/isgri_mosa_res.fits
   uv run integral view image work/obs/obs_ibis/isgri_mosa_ima.fits
   ```
2. **Resolve the IC calibration resolution failure** (§3 item 6) before relying on
   `data download`-sourced data for anything real — either by finding a working mirror of ISDC's
   official `ic_tree/prod` bundle, or via ISDC/INTEGRAL help-desk support on DAL status `-2004`.
3. **Build out the MNRAS benchmark matrix** described in `docs/technical_roadmap.md` §A: extend
   `integral_cli/benchmark.py` to cover JEM-X/OMC/SPI, multiple ScW counts, and repeats, and check
   in the resulting `benchmark_results.jsonl` as the paper's evidence trail.
4. **Pick a direction on the cloud and GUI tracks** in `docs/technical_roadmap.md` §B/§C — both
   are written up as options for discussion, not yet started.
