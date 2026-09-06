# Calibration Management & Historic Analysis Guide: Modifying `ic_master_file.fits`

## 1. Overview of the INTEGRAL Calibration Architecture

In the INTEGRAL Off-line Scientific Analysis (OSA) system, Instrument Characteristics (IC) calibration files are resolved hierarchically:

```
                  Pipeline Invocation (e.g., ibis_science_analysis)
                                         │
                    IC_Group = "ic_master_file.fits[1]"
                    IC_Alias = "OSA" (or "IC_11.0", "OSA9", etc.)
                                         │
                                         ▼
                            ic_master_file.fits [HDU 3]
                   (Maps Alias Name + Epoch to Group Version Numbers)
                                         │
                   ┌─────────────────────┼──────────────────────┐
                   ▼                     ▼                      ▼
           ISGR-EFFC-MOD-IDX     ISGR-BACK-BKG-IDX      JMX2-IMOD-GRP-IDX
                   │                     │                      │
       (Version, Date Window)  (Version, Date Window) (Version, Date Window)
                   │                     │                      │
                   ▼                     ▼                      ▼
           isgr_effc_mod_0003    isgr_back_bkg_0007     jmx2_imod_grp_0025.fits
```

### The Structure of `ic_master_file.fits`
`ic_master_file.fits` contains:
- **HDU 1 (PRIMARY)**: Header metadata.
- **HDU 2 (GROUPING)**: Table listing all registered instrument index files (e.g. `../../idx/ic/ISGR-EFFC-MOD-IDX.fits`).
- **HDU 3 (BINTABLE)**: The master alias and configuration lookup table. Each row corresponds to a specific calibration configuration alias (`MNEMONIC` column: `OSA`, `NRT`, `CONS`, `IC_11.0`, `IC_11.1_final`, `IC_11.2`, `OSA9`, `IC_10.2`, etc.).
  - The columns in HDU 3 specify the **group version number** for every instrument subsystem:
    - **IBIS/ISGRI**: `ISGR_EFFC_MOD`, `ISGR_MASK_MOD`, `ISGR_DECO_MOD`, `ISGR_GHOS_MOD`, `ISGR_GNRL_BTI`, `IBIS_VETO_MOD`, `ISGR_BACK_BKG`, `ISGR_OFFS_MOD`, etc.
    - **JEM-X**: `JMX1_IMOD_GRP`, `JMX2_IMOD_GRP`, `JMX1_BPL_GRP`, `JMX2_BPL_GRP`, etc.
    - **OMC**: `OMC_FLAT_CAL`, `OMC_PHOT_CAL`, `OMC_DARK_CAL`, etc.
    - **SPI**: `SPI_FLAT_GRP`, `SPI_IRF_GRP`, `SPI_COEF_CAL`, etc.

---

## 2. Why Calibration Discrepancies Occur in Historic/Legacy Tests

When running validation suites against historical ESA/ISDC reference datasets (such as test runs from 2018–2022):
1. **IC Tree Evolution**: Modern IC repositories (e.g. downloaded from HEASARC) update default group versions (for instance, pointing `ISGR_EFFC_MOD` to version 2 rather than version 1).
2. **Impact on Science Products**:
   - In IBIS/ISGRI, version 2 of `ISGR_EFFC_MOD` selects the modern 2023+ time-dependent efficiency files (`isgr_effc_mod_0310.fits`), which apply newer energy re-weighting and transmission corrections. Running test data with version 2 produces Crab flux $\approx 98.24\text{ cts/s}$ ($299.6\sigma$), whereas version 1 (`isgr_effc_mod_0003.fits`) produces Crab flux $\approx 141.77\text{ cts/s}$ ($314.4\sigma$), closely tracking the 2022 ESA reference ($145.39\text{ cts/s}$, $328.8\sigma$).
   - In JEM-X, early test datasets require alignment with the instrument model group (`JMX2_IMOD_GRP = 25` vs. modern `382`).
   - In OMC, historical flat-field calibrations (`omc_flat_cal_0002.fits`, `0003.fits`) are needed for early revolutions before modern indices were constructed.

---

## 3. Step-by-Step Guide to Modifying `ic_master_file.fits`

### Step 1: Backup the Existing Master File
Always make a copy before editing:
```bash
cp ../integral_data_archive/idx/ic/ic_master_file.fits \
   ../integral_data_archive/idx/ic/ic_master_file.fits.bak
```

### Step 2: Inspect Available Aliases and Subsystem Columns
Run Python with `uv`:
```bash
uv run python -c "
from astropy.io import fits
h = fits.open('../integral_data_archive/idx/ic/ic_master_file.fits')
d = h[3].data
for idx, row in enumerate(d):
    print(f'Row {idx:2d}: {row[\"MNEMONIC\"]:15s} | Dates: {row[\"DATE_START\"]} to {row[\"DATE_STOP\"]}')
"
```
Row 2 is typically the active alias `OSA`. Other rows hold specific frozen calibration epochs (`IC_11.0`, `IC_10.2_final`, `OSA9`, etc.).

### Step 3: Inspect the Current Group Versions for your Instrument
```bash
uv run python -c "
from astropy.io import fits
h = fits.open('../integral_data_archive/idx/ic/ic_master_file.fits')
d = h[3].data
subsystem_cols = [c for c in d.names if 'ISGR' in c or 'IBIS' in c]
for c in subsystem_cols:
    print(f'{c:20s}: OSA(row 2)={d[c][2]:4d} | IC_11.0(row 9)={d[c][9]:4d} | IC_11.1(row 10)={d[c][10]:4d}')
"
```

### Step 4: Update the Master File with Legacy Calibration Groups
To switch the default `OSA` alias (row 2) to reproduce historic 2022/legacy test datasets:

```bash
uv run python -c "
from astropy.io import fits

with fits.open('../integral_data_archive/idx/ic/ic_master_file.fits', mode='update') as h:
    d = h[3].data
    
    # Target Row 2: Default alias 'OSA'
    # 1. IBIS / ISGRI Legacy Alignment (matching IC_11.1 / ESA test data)
    d['ISGR_EFFC_MOD'][2] = 1   # Points to isgr_effc_mod_0003.fits
    d['ISGR_MASK_MOD'][2] = 1   # Points to isgr_mask_mod_0003.fits
    d['ISGR_DECO_MOD'][2] = 1   # Points to isgr_deco_mod_0001.fits
    d['ISGR_GHOS_MOD'][2] = 1   # Points to isgr_ghos_mod_0001.fits
    d['ISGR_GNRL_BTI'][2] = 22  # Points to isgr_gnrl_bti_0022.fits
    d['IBIS_VETO_MOD'][2] = 25  # Points to ibis_veto_mod_0025.fits
    
    # 2. JEM-X Legacy Alignment (if testing early Crab observations, e.g. Rev 0102)
    d['JMX1_IMOD_GRP'][2] = 25
    d['JMX2_IMOD_GRP'][2] = 25
    
    h.flush()
print('Successfully configured ic_master_file.fits for legacy benchmark testing.')
"
```

### Step 5: Verify the Sub-Index Resolutions
Ensure that the indexed calibration files actually exist in your repository:
```bash
uv run python -c "
from astropy.io import fits
import os

h_effc = fits.open('../integral_data_archive/idx/ic/ISGR-EFFC-MOD-IDX.fits')
matches = [r for r in h_effc[1].data if r['VERSION'] == 1]
for m in matches:
    rel_path = m['MEMBER_LOCATION'].replace('../../', '../integral_data_archive/')
    print(f'Group 1 resolves to {rel_path} (exists: {os.path.exists(rel_path)})')
"
```

---

## 4. Alternative Method: Using `IC_Alias` Parameter Without Modifying the Master File

Instead of modifying `ic_master_file.fits`, you can specify the desired alias directly in pipeline calls if the alias exists in HDU 3:

```bash
ibis_science_analysis \
    ogDOL="og_ibis.fits" \
    startLevel="COR" \
    endLevel="IMA2" \
    IC_Group="/data/idx/ic/ic_master_file.fits[1]" \
    IC_Alias="IC_11.0"
```
Or for JEM-X:
```bash
jemx_science_analysis \
    ogDOL="og_jmx2.fits" \
    startLevel="COR" \
    endLevel="IMA" \
    IC_Group="/data/idx/ic/ic_master_file.fits[1]" \
    IC_Alias="IC_11.0"
```

> [!NOTE]
> For the alias method to work, `ic_master_file.fits` must contain the requested alias in HDU 3 column `MNEMONIC`, and the corresponding group versions must not contain `-999` (uninitialized) values.

---

## 5. CLI Data Manager Analysis & Recommended Amendments

### Current State of the CLI Automated Builder
The automated IC index builder and pruner lives in `src/integral_cli/data_mgr.py`:
- `clean_ic_master_file(dest_base: Path)`:
  - Scans HDU 2 (`GROUPING`) of `ic_master_file.fits`.
  - Checks if member indices exist in `idx/ic/`.
  - Removes index entries pointing to missing files to prevent DAL status `-2004` errors.

### Deficiencies Identified:
1. **HDU 3 Subsystem Verification Not Performed**:
   - `clean_ic_master_file` only validates HDU 2 (the list of index FITS files). It does **not** check whether the version numbers referenced in HDU 3 (the alias definitions) actually resolve to files present in `ic/`.
   - If a modern HEASARC IC sync updates HDU 3 to point to modern version numbers (e.g. `ISGR_EFFC_MOD = 2`) but only legacy files (`isgr_effc_mod_0003.fits`) are staged locally, the pipeline can fail at runtime.
2. **Missing `IC_Alias` CLI Configuration**:
   - In `src/integral_cli/analysis.py`, `IC_Alias="OSA"` is currently hardcoded for IBIS, JEM-X, and SPI reductions.
   - Users cannot currently pass `--ic-alias IC_11.0` or `--ic-alias OSA9` from the command line without editing source files.

### Recommended CLI Amendments:
1. **Expose `--ic-alias` Option**:
   Add `--ic-alias` as an optional flag in `integral analyse ibis`, `jemx`, `spi`, and `omc` with default `"OSA"`.
2. **Add Calibration Profile Management Subcommand**:
   Add `integral data calibration set-alias <ALIAS>` or `integral data calibration legacy-align` to automatically configure `ic_master_file.fits` for legacy test validation or modern science analysis.
3. **Enhance `clean_ic_master_file`**:
   Extend `clean_ic_master_file` to inspect HDU 3 and ensure the active alias points to available files in the `ic/` subdirectories.

---

## 6. Glossary of Calibration Terms

This glossary defines the Instrument Characteristics (IC) files and subsystems referenced in this guide. These files are essential for correcting raw telemetry data and are resolved by the OSA pipeline via the `ic_master_file.fits` lookup table.

### IBIS/ISGRI
- **`ISGR_EFFC_MOD` (Efficiency Model)**: Contains the energy-dependent efficiency of the ISGRI detector. Used during the binning step to create efficiency shadowgrams and recover the true source flux.
- **`ISGR_MASK_MOD` (Mask Pattern)**: Defines the geometry of the coded mask (the pattern of open and opaque tungsten elements).
- **`ISGR_DECO_MOD` (Decoding Pattern)**: The projected decoding array used to deconvolve the detector shadowgrams into sky images.
- **`ISGR_GHOS_MOD` (Ghost Buster Model)**: Defines specific regions of the mask (e.g., areas with glue deposits) that are ignored to prevent the creation of "ghost" artifacts in deep images.
- **`ISGR_GNRL_BTI` (General Bad Time Intervals)**: A table listing periods of anomalous instrument behavior (e.g., solar flares, VETO problems) that should be excluded from the analysis.
- **`IBIS_VETO_MOD` (VETO Shield Model)**: Calibration data for the BGO anticoincidence shield, used to correct for VETO swapping and define anticoincidence logic.
- **`ISGR_BACK_BKG` (Background Model)**: The instrument background array used to subtract the internal detector background from the observed counts.
- **`ISGR_OFFS_MOD` (Offset/Gain Model)**: Contains linear gain and offset parameters for every pixel, essential for converting pulse height (PHA) to energy (keV).

### JEM-X
- **`JMX_IMOD_GRP` (Instrument Model Group)**: Contains the instrumental response and geometry models for the JEM-X detectors.
- **`JMX_BPL_GRP` (Baseline Parameter Group)**: Contains the baseline calibration parameters used for energy reconstruction and charge collection corrections.

### OMC
- **`OMC_FLAT_CAL` (Flat-Field Calibration)**: A normalized image used to correct for the non-uniform response of the CCD pixels across the field of view.
- **`OMC_PHOT_CAL` (Photometric Calibration)**: The calibration curve used to convert measured electron fluxes into standard Johnson V magnitudes.
- **`OMC_DARK_CAL` (Dark Current Calibration)**: Contains the dark current, slope, and bias values used to remove electronic noise and thermal current.

### SPI
- **`SPI_FLAT_GRP` (Flat-Field Group)**: A set of pre-defined flat-field spectra (derived from empty-field observations) used to model the background of the SPI detectors.
- **`SPI_IRF_GRP` (Instrument Response Function Group)**: Contains the RMFs (Redistribution Matrix Files) and ARFs (Ancillary Response Files) describing the instrument response.
- **`SPI_COEF_CAL` (Calibration Coefficients)**: General calibration coefficients used for energy correction and gain stabilization of the Germanium detectors.
