# INTEGRAL OSA Modernization — Project Status & Handover Report

**Date**: September 1, 2026  
**Status**: Phases 1–3 Complete & Verified | Phase 4 Architecture Scoped  
**Target Environment**: Apple Silicon (macOS ARM64) & Kubernetes Cloud Batch  

---

## 1. Executive Summary & Current Status

We have brought the **INTEGRAL Off-line Scientific Analysis (OSA 11)** pipeline from legacy manual workflows into an automated Python CLI with verified end-to-end scientific reduction.

### Key Working Milestones Completed:
1. **Full Science Reduction & Mosaicing (18–60 keV Band)**:
   - Successfully executed on **10 Science Windows** (`006000010010` ... `006000100010`) of Revolution 0060 using the verified baseline image (`integralsw/osa:11.0`).
   - Generated **95 scientific FITS products** (`isgri_mosa_ima.fits`, `isgri_mosa_res.fits`, single-pointing sky images, variance/exposure maps, shadowgrams).
   - Extracted **10 significant point sources** with clear $\sqrt{N}$ signal-to-noise scaling (Top detection: **XTE J1550-564** at **$263.9\,\sigma$**, $76.92 \pm 0.29$ cts/s; **IGR J16318-4848** at **$43.6\,\sigma$**).
2. **Baseline Performance Benchmark Established (Base Image)**:
   - **Total Execution Time (10 ScWs)**: **392.18s** (6m 32s)
   - **Throughput Rate**: **39.22s per Science Window**
3. **Data Archive & Calibration Standardization**:
   - High-throughput parallel downloader implemented in Typer CLI.
   - All 214 Science Windows of Rev 0060 uncompressed and structured.
   - 115 Instrument Characteristics (IC) calibration indexes and mission reference trees (`aux/adp/ref/`: `tcoroffset`, `leap`, `de200`, `irot`) verified and active.
4. **CLI & Visualization Suite**:
   - `uv run integral analyze ibis` — Batch reduction and mosaicing.
   - `uv run integral view image` — Automatic WCS celestial coordinate rendering with ZScale intensity stretching.
   - `uv run integral view sources` — Terminal summary tables of detected point sources and fluxes.

---

## 2. Architecture & Benchmark Reality

| Software Layer | Status | Measured Benchmark | Notes |
| :--- | :---: | :---: | :--- |
| **Baseline Core Pipeline** (`integralsw/osa:11.0`) | **100% Verified** | **39.22s / ScW** (392.18s for 10 ScWs) | Full C/Fortran reduction (`ibis_science_analysis`) running via Docker's Rosetta 2 emulation. |
| **Python Tooling Layer** (Host / Native ARM64) | **100% Verified** | **0.23s** (catalog indexing / WCS render) | Isolated benchmark of modern Python/Astropy data I/O only — **does NOT reflect core OSA reduction speed**. |
| **Native Apple Silicon OSA Pipeline** (Phase 4) | *In Progress* | *Not yet benchmarked* | Full native compilation of ISDC C/Fortran binaries on ARM64 is still required to measure native pipeline performance. |

---

## 3. What Needs to Be Solved (Phase 4 Details)

### The Technical Challenge:
1. **The Legacy Binary Architecture**:
   The original OSA 11 science executables (`ii_skyimage`, `og_create`, `ii_shadow_build`, `ibis_isgr_energy`) were compiled as 64-bit x86 ELF binaries against **CentOS 7 (glibc 2.17)** and legacy ROOT 5.34.
2. **The Container Environment**:
   - When running the base CentOS 7 container (`integralsw/osa:11.0`), macOS translates instructions via Rosetta 2 and executes reliably (**392s for 10 ScWs**).
   - When constructing an Ubuntu 22.04 container, mixing CentOS 7 glibc 2.17 libraries with Ubuntu glibc 2.35 causes dynamic linker (`ld.so`) symbol mismatches.
3. **The Solution Path for Phase 4**:
   - **Option A (Recommended & Cleanest)**: Build our modernized container directly on top of the verified CentOS 7 base by adding standalone Python 3.12 and `uv` into the CentOS 7 environment. This avoids any glibc version clash and gives full pipeline access with zero library friction.
   - **Option B (Source Compilation)**: Recompile the ISDC C/Fortran source packages (`dal3ibis`, `ii_skyimage`, `og_create`) natively for ARM64 using GCC/GFortran on Ubuntu 22.04.

---

## 4. How to Resume Work

When resuming development:
1. **Verify Baseline Science Pipeline**:
   ```bash
   # Run 10-ScW reduction benchmark
   uv run integral analyze ibis rev:0060:10 --e-min 18 --e-max 60 --mosaic

   # View generated mosaic and detected sources
   uv run integral view sources work/obs/obs_ibis/isgri_mosa_res.fits
   uv run integral view image work/obs/obs_ibis/isgri_mosa_ima.fits
   ```
2. **Complete Option A Modernization**:
   Package the modern Python 3.12 / Astropy stack inside the verified CentOS 7 container image to benchmark against the base image.
3. **Run Multi-Pointing Benchmarks**:
   Compare execution times and memory throughput across 10, 25, and 50 Science Windows.
