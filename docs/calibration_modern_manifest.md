# Modern Calibration Specification & Latest IC Baseline Manifest

## 1. Executive Summary

This document defines the **Modern Calibration Baseline** (Alias: `OSA`, Row 2 of `idx/ic/ic_master_file.fits`) used for contemporary INTEGRAL scientific analysis and next-generation benchmark datasets.

Unlike the historical 2022 ESA test package (which used frozen early mission calibrations from OSA 10/11.0), this modern baseline incorporates the latest instrument characteristic corrections released by the INTEGRAL Science Data Centre (ISDC) and HEASARC:
- **IBIS/ISGRI**: Transitioned to **Group Version 2** for efficiency, deadtime, and time-dependent response modeling, incorporating `isgr_effc_mod_0185.fits` and late-mission Gain-Offset maps (`ibis_isgr_gain_offset_0010.fits`).
- **JEM-X (1 & 2)**: Aligned with **Group Version 25** (`jmx1_imod_grp_0377.fits`, `jmx2_imod_grp_0375.fits`), incorporating updated background spatial templates and gain correction history.
- **OMC**: Full flat-field evolution up to **Group Version 5** (`omc_flat_cal_0051.fits`).
- **SPI**: Complete detector response (`spi_irf_rsp_0384.fits`), flat fields (`spi_flat_grp_0056.fits`), and Line-SCT profiles (`spi_line_sct_0011.fits`).

---

## 2. Complete Modern Subsystem Calibration Manifest

### A. IBIS / ISGRI (`ISGR` & `IBIS`)

| Master Column Name | Group Version | Resolved Index FITS File | Target Calibration File | Description |
| :--- | :---: | :--- | :--- | :--- |
| `IBIS_ALRT_LIM` | **27** | `IBIS-ALRT-LIM-IDX.fits` | `ibis_lim_alerts_0027.fits` | Alert limits & housekeeping thresholds |
| `IBIS_CONV_MOD` | **3** | `IBIS-CONV-MOD-IDX.fits` | `ibis_conv_mod_0015.fits` | Conversion model |
| `IBIS_DUMP_CFG` | **1** | `IBIS-DUMP-CFG-IDX.fits` | `ibis_dump_cfg_0005.fits` | Dump configuration table |
| `IBIS_GOOD_LIM` | **3** | `IBIS-GOOD-LIM-IDX.fits` | `ibis_lim_gti_0004.fits` | Good time interval limits |
| `IBIS_IREM_CAL` | **10** | `IBIS-IREM-CAL-IDX.fits` | `ibis_irem_cal_0010.fits` | IREM calibration parameters |
| `IBIS_SWIT_CAL` | **8** | `IBIS-SWIT-CAL-IDX.fits` | `ibis_swit_cal_0008.fits` | Switch calibration settings |
| `IBIS_VCTX_GRP` | **1** | `IBIS-VCTX-GRP-IDX.fits` | `ibis_vxtx_grp_0005.fits` | Veto context group |
| `IBIS_VETO_MOD` | **28** | `IBIS-VETO-MOD-IDX.fits` | `ibis_veto_mod_0028.fits` | Veto model parameters |
| `ISGR_EFFC_MOD` | **2** | `ISGR-EFFC-MOD-IDX.fits` | `isgr_effc_mod_0185.fits` | Detector efficiency model |
| `ISGR_L2RE_MOD` | **2** | `ISGR-L2RE-MOD-IDX.fits` | `isgr_l2re_mod_0185.fits` | Level 2 reconstruction model |
| `ISGR_MCEC_MOD` | **2** | `ISGR-MCEC-MOD-IDX.fits` | `isgr_mcec_mod_0185.fits` | Module calibration & correction |
| `ISGR_3DL2_MOD` | **1** | `ISGR-3DL2-MOD-IDX.fits` | `isgr_3dl2_mod_0001.fits` | 3D reconstruction model |
| `ISGR_ARF_RSP` | **18** | `ISGR-ARF.-RSP-IDX.fits` | `isgr_arf_rsp_0031.fits` | Ancillary response function |
| `ISGR_ATTN_MOD` | **3** | `ISGR-ATTN-MOD-IDX.fits` | `isgr_attn_mod_0010.fits` | Tungsten/lead collimator attenuation |
| `ISGR_BACK_BKG` | **7** | `ISGR-BACK-BKG-IDX.fits` | `isgr_back_bkg_0007.fits` | Background model map |
| `ISGR_COVR_MOD` | **2** | `ISGR-COVR-MOD-IDX.fits` | `isgr_covr_mod_0002.fits` | Dead area / coverage model |
| `ISGR_CTXT_GRP` | **3** | `ISGR-CTXT-GRP-IDX.fits` | `isgr_ctxt_grp_0007.fits` | Context group |
| `ISGR_DECO_MOD` | **2** | `ISGR-DECO-MOD-IDX.fits` | `isgr_deco_mod_0010.fits` | Deconvolution matrix |
| `ISGR_DROP_MOD` | **4** | `ISGR-DROP-MOD-IDX.fits` | `isgr_drop_mod_0004.fits` | Event drop correction model |
| `ISGR_EBDS_MOD` | **1** | `ISGR-EBDS-MOD-IDX.fits` | `isgr_ebds_mod_0001.fits` | Energy bounds table |
| `ISGR_EFFI_MOD` | **11** | `ISGR-EFFI-MOD-IDX.fits` | `isgr_effi_mod_0011.fits` | Efficiency correction map |
| `ISGR_ENGA_MOD` | **1** | `ISGR-ENGA-MOD-IDX.fits` | `isgr_enga_mod_0001.fits` | Energy gain model |
| `ISGR_ENOF_MOD` | **1** | `ISGR-ENOF-MOD-IDX.fits` | `isgr_enof_mod_0001.fits` | Energy offset model |
| `ISGR_GAIN_MOD` | **1** | `ISGR-GAIN-MOD-IDX.fits` | `isgr_gain_mod_0001.fits` | Gain drift model |
| `ISGR_GHOS_MOD` | **2** | `ISGR-GHOS-MOD-IDX.fits` | `isgr_ghos_mod_0003.fits` | Ghost buster mask model |
| `ISGR_GNRL_BTI` | **25** | `ISGR-GNRL-BTI-IDX.fits` | `isgr_gnrl_bti_0025.fits` | Bad Time Intervals (BTI) |
| `ISGR_LUT_GRP` | **1** | `ISGR-LUT.-GRP-IDX.fits` | `isgr_lut_grp_0001.fits` | Look-Up Table (LUT) group |
| `ISGR_MASK_MOD` | **2** | `ISGR-MASK-MOD-IDX.fits` | `isgr_mask_mod_0005.fits` | Coded aperture mask model |
| `ISGR_OFF2_MOD` | **1** | `ISGR-OFF2-MOD-IDX.fits` | `isgr_off2_mod_0001.fits` | Secondary offset model |
| `ISGR_OFFS_MOD` | **4** | `ISGR-OFFS-MOD-IDX.fits` | `ibis_isgr_gain_offset_0010.fits` | Pixel gain and offset table |
| `ISGR_RISE_MOD` | **5** | `ISGR-RISE-MOD-IDX.fits` | `isgr_rise_mod_0185.fits` | Rise-time correction model |
| `ISGR_RISE_PRO` | **1** | `ISGR-RISE-PRO-IDX.fits` | `isgr_rise_pro_0001.fits` | Rise-time probability profile |
| `ISGR_RMF_GRP` | **27** | `ISGR-RMF.-GRP-IDX.fits` | `isgr_rmf_grp_0027.fits` | Redistribution Matrix group |
| `ISGR_RMF_RSP` | **2** | `ISGR-RMF.-RSP-IDX.fits` | `isgr_rmf_rsp_0219.fits` | Detector response matrix |
| `ISGR_TEMP_MOD` | **1** | `ISGR-TEMP-MOD-IDX.fits` | `isgr_temp_mod_0001.fits` | Temperature compensation model |
| `ISGR_UNIF_BKG` | **2** | `ISGR-UNIF-BKG-IDX.fits` | `isgr_unif_bkg_0002.fits` | Uniformity background map |

---

### B. JEM-X (Joint European X-ray Monitor - Units 1 & 2)

| Master Column Name | Group Version | Resolved Index FITS File | Target Calibration File | Description |
| :--- | :---: | :--- | :--- | :--- |
| `JMX1_IMOD_GRP` | **25** | `JMX1-IMOD-GRP-IDX.fits` | `jmx1_imod_grp_0377.fits` | JEM-X 1 Instrument model group |
| `JMX2_IMOD_GRP` | **25** | `JMX2-IMOD-GRP-IDX.fits` | `jmx2_imod_grp_0375.fits` | JEM-X 2 Instrument model group |
| `JMX1_BPL_GRP` | **7** | `JMX1-BPL.-GRP-IDX.fits` | `jmx1_bpl_grp_0007.fits` | JEM-X 1 Back-projection library |
| `JMX2_BPL_GRP` | **7** | `JMX2-BPL.-GRP-IDX.fits` | `jmx2_bpl_grp_0007.fits` | JEM-X 2 Back-projection library |
| `JMX1_GNRL_BTI` | **43** | `JMX1-GNRL-BTI-IDX.fits` | `jmx1_gnrl_bti_0043.fits` | JEM-X 1 Bad time intervals |
| `JMX2_GNRL_BTI` | **47** | `JMX2-GNRL-BTI-IDX.fits` | `jmx2_gnrl_bti_0047.fits` | JEM-X 2 Bad time intervals |
| `JMX1_GAIN_OCL` | **1** | `JMX1-GAIN-OCL-IDX.fits` | `jmx1_gain_ocl_0008.fits` | JEM-X 1 Gain offline calibration |
| `JMX2_GAIN_OCL` | **2** | `JMX2-GAIN-OCL-IDX.fits` | `jmx2_gain_ocl_0011.fits` | JEM-X 2 Gain offline calibration |
| `JMX1_SPAT_BKG` | **3** | `JMX1-SPAT-BKG-IDX.fits` | `jmx1_spat_bkg_0008.fits` | JEM-X 1 Spatial background map |
| `JMX2_SPAT_BKG` | **6** | `JMX2-SPAT-BKG-IDX.fits` | `jmx2_spat_bkg_0009.fits` | JEM-X 2 Spatial background map |
| `JMX1_RMF_GRP` | **13** | `JMX1-RMF.-GRP-IDX.fits` | `jmx1_rmf_grp_0046.fits` | JEM-X 1 Response matrix group |
| `JMX2_RMF_GRP` | **13** | `JMX2-RMF.-GRP-IDX.fits` | `jmx2_rmf_grp_0046.fits` | JEM-X 2 Response matrix group |

---

### C. OMC (Optical Monitor Camera)

| Master Column Name | Group Version | Resolved Index FITS File | Target Calibration File | Description |
| :--- | :---: | :--- | :--- | :--- |
| `OMC_FLAT_CAL` | **5** | `OMC.-FLAT-CAL-IDX.fits` | `omc_flat_cal_0051.fits` | CCD Flat-field response matrix |
| `OMC_PHOT_CAL` | **5** | `OMC.-PHOT-CAL-IDX.fits` | `omc_phot_cal_0051.fits` | Photometric zero-point & extinction |
| `OMC_DARK_CAL` | **2** | `OMC.-DARK-CAL-IDX.fits` | `omc_dark_cal_0003.fits` | Dark current correction map |
| `OMC_BDPX_CAL` | **2** | `OMC.-BDPX-CAL-IDX.fits` | `omc_bdpx_cal_0003.fits` | Bad pixel map |
| `OMC_CONV_MOD` | **1** | `OMC.-CONV-MOD-IDX.fits` | `omc._conv_mod_0007.fits` | Coordinate conversion model |
| `OMC_GOOD_LIM` | **8** | `OMC.-GOOD-LIM-IDX.fits` | `omc_lim_gti_0010.fits` | Good time limits |
| `OMC_ALRT_LIM` | **7** | `OMC.-ALRT-LIM-IDX.fits` | `omc_lim_alerts_0010.fits` | Alert & telemetry limits |

---

### D. SPI (Spectrometer on INTEGRAL)

| Master Column Name | Group Version | Resolved Index FITS File | Target Calibration File | Description |
| :--- | :---: | :--- | :--- | :--- |
| `SPI_FLAT_GRP` | **6** | `SPI.-FLAT-GRP-IDX.fits` | `spi_flat_grp_0056.fits` | Germanium detector flat-field group |
| `SPI_FLPE_GRP` | **6** | `SPI.-FLPE-GRP-IDX.fits` | `spi_flpe_grp_0056.fits` | Photopeak efficiency group |
| `SPI_IRF_GRP` | **7** | `SPI.-IRF.-GRP-IDX.fits` | `spi_irf_grp_0021.fits` | Instrumental response function group |
| `SPI_IRF_RSP` | **6** | `SPI.-IRF.-RSP-IDX.fits` | `spi_irf_rsp_0384.fits` | Instrumental response matrix |
| `SPI_COEF_CAL` | **5** | `SPI.-COEF-CAL-IDX.fits` | `spi_coef_cal_0008.fits` | Energy calibration coefficients |
| `SPI_LINE_SCT` | **5** | `SPI.-LINE-SCT-IDX.fits` | `spi_line_sct_0011.fits` | Line scatter profiles |
| `SPI_BVAR_MOD` | **7** | `SPI.-BVAR-MOD-IDX.fits` | `spi_bvar_mod_0007.fits` | Background variability model |
| `SPI_ALRT_LIM` | **29** | `SPI.-ALRT-LIM-IDX.fits` | `spi_lim_alerts_0029.fits` | Telemetry alert limits |
| `SPI_GNRL_BTI` | **9** | `SPI.-GNRL-BTI-IDX.fits` | `spi_gnrl_bti_0009.fits` | Bad time intervals |
