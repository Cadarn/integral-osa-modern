# INTEGRAL Off-line Scientific Analysis (OSA) Modern Containers

Modernized, high-performance container images for ESA's **INTEGRAL Off-line Scientific Analysis (OSA 11.2)** pipeline, built for modern cloud and local compute architectures (**ARM64 / Apple Silicon / AWS Graviton** and **x86_64 / AMD64**).

Official Project Repository: [Cadarn/integral-osa-modern](https://github.com/Cadarn/integral-osa-modern)  
Upstream ESA Science Data Archive & Documentation: [ESA Cosmos INTEGRAL](https://www.cosmos.esa.int/web/integral) | [HEASARC INTEGRAL](https://heasarc.gsfc.nasa.gov/docs/integral/)

---

## 1. Container Variants & Architecture Overview

We provide two tiers of Docker containers for OSA processing:

| Image Tier | Description | Image Size | Instrument Characteristics (CALDB) | Best Used For |
| :--- | :--- | :--- | :--- | :--- |
| **Base Lean Images** | Minimal runtime environment containing the modernized OSA 11.2 binary distribution. | **~1.0 GB - 3.3 GB** | **Not included**. You must mount your local calibration directory to `/opt/osa/caldb` (or set `REP_BASE_PROD`). | Workflows with existing shared IC trees, multi-instrument jobs sharing local storage, or storage-constrained environments. |
| **Dedicated Instrument Images** | Fully hermetic, self-contained images tailored for a single instrument with all required calibration files (IC tree, master index, and catalogs) **pre-baked**. | **~2.5 GB - 7.9 GB** | **Pre-baked & configured** inside `/opt/osa/caldb`. Requires **zero** calibration downloads or mounts. | Cloud compute (AWS EC2/Batch/Lambda), serverless jobs, CI/CD pipelines, and quick standalone reductions where you only mount raw Science Windows (`/data/scw`). |

---

## 2. Tag Ontology & Versioning Convention

Image tags follow a semantic naming scheme:

```text
cadarn/osa:[OSA_VERSION]-[CALIBRATION_RELEASE]-[ARCH]-[INSTRUMENT]
```

### Components

- **`OSA_VERSION`**: The underlying OSA release (e.g. `11.2`, `11`).
- **`CALIBRATION_RELEASE`**:
  - `icYYYYMM` (e.g. `ic202505`): Extracted directly from the official ESA `ic_master_file.fits` release timestamps. Represents the latest modern calibration baseline.
  - `esa-2022`: Pinned historical baseline matching the frozen ESA reference archive.
  - Content digest (e.g. `ic-a1b2c3d4`): Cryptographic SHA-256 fingerprint of the staged calibration payload for immutable scientific reproducibility.
- **`ARCH`**:
  - `arm64`: Native AArch64 binary build (Apple Silicon, AWS Graviton 2/3/4). Delivers **2.0x – 2.7x faster** throughput than x86_64.
  - `amd64`: Standard x86_64 binary build for Intel/AMD processors.
- **`INSTRUMENT`**:
  - `ibis`: IBIS/ISGRI, PICsIT, and Compton analysis. Contains ISGRI background models, gain drift corrections, and reference catalogs (`gnrl_refr_cat`).
  - `jemx`: JEM-X 1 & 2 X-ray monitors. Contains gain history, anode grids, deadtime tables, and reference catalogs.
  - `omc`: Optical Monitoring Camera. Contains photometric calibration, flatfields, bad pixel maps, and the OMC reference catalog (`omc_refr_cat`).
  - `spi`: Spectrometer on INTEGRAL. Contains instrument response functions (IRFs), energy boundaries, background models, and reference catalogs.

### Available Instrument Tags

```text
cadarn/osa:11.2-ic202505-arm64-ibis
cadarn/osa:11.2-ic202505-arm64-jemx
cadarn/osa:11.2-ic202505-arm64-omc
cadarn/osa:11.2-ic202505-arm64-spi
```

### Available Base Tags

```text
cadarn/osa:11-native-arm64     # Lean native ARM64 base image (~1.0 GB)
cadarn/osa:11-modern-amd64     # Lean modern x86_64 base image (~3.3 GB)
cadarn/osa:latest-arm64        # Alias for 11-native-arm64
```

---

## 3. Quick Start Examples

### Dedicated Instrument Image (Zero Calibration Mount Required)

Run an IBIS/ISGRI science reduction with only your Science Windows and auxiliary attitude data mounted:

```bash
docker run --rm -it \
  -v /path/to/scw:/data/scw:ro \
  -v /path/to/aux:/data/aux:ro \
  -v $(pwd)/work:/data/work \
  cadarn/osa:11.2-ic202505-arm64-ibis \
  bash -c "
    cd /data/work && \
    og_create idxSwg=/data/scw/0060/006000020010.001/swg.fits instrument=IBIS && \
    cd obs/006000020010.001 && \
    ibis_science_analysis ogDOL='og_ibis.fits[1]' startLevel='COR' endLevel='IMA'
  "
```

### Base Image (External Calibration Mount)

If using a lean base image, mount your local IC directory:

```bash
docker run --rm -it \
  -v /path/to/ic_tree:/opt/osa/caldb:ro \
  -v /path/to/scw:/data/scw:ro \
  -v /path/to/aux:/data/aux:ro \
  -v $(pwd)/work:/data/work \
  cadarn/osa:11-native-arm64
```

---

## 4. Packaging Your Own Custom Calibration Images

You can package your own specialized container images with any calibration profile or tag using the [integral-cli](https://github.com/Cadarn/integral-osa-modern):

```bash
# Build latest calibration image for JEM-X
uv run integral docker package --instrument jemx --profile latest

# Build legacy calibration image with custom tag
uv run integral docker package --instrument ibis --profile esa-2022 --cal-tag esa2022

# Package and push all instruments in one command
uv run integral docker package --instrument all --push
```

---

## 5. References & Resources

- **ESA INTEGRAL Science Operations**: [https://www.cosmos.esa.int/web/integral](https://www.cosmos.esa.int/web/integral)
- **OSA 11.2 Software Manuals**: [https://www.cosmos.esa.int/web/integral/analysis-software](https://www.cosmos.esa.int/web/integral/analysis-software)
- **HEASARC High Energy Astrophysics Archive**: [https://heasarc.gsfc.nasa.gov/docs/integral/](https://heasarc.gsfc.nasa.gov/docs/integral/)
- **Repository Issues & Pull Requests**: [https://github.com/Cadarn/integral-osa-modern/issues](https://github.com/Cadarn/integral-osa-modern/issues)
