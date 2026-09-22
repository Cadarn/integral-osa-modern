# Full-Mission INTEGRAL/OSA Cloud Economics & High-Throughput Processing Plan

## 1. Executive Summary

This document outlines the cloud infrastructure architecture, operational strategy, execution timeline, and cost model for reducing the entire 22-year archival science legacy of the **INTEGRAL** space observatory using the modernized **OSA 11.2** containerized analysis pipeline.

Using empirical benchmarks gathered from production AWS EC2 deployments (`c7i.xlarge` Intel Sapphire Rapids and `c7g.xlarge` AWS Graviton3), an ephemeral S3 rolling-buffer staging architecture, and verified AWS service quotas:
* **Total Archival Volume**: **2,886 revolutions** comprising approximately **131,000 Science Windows (ScWs)** (~7.73 TB compressed telemetry).
* **Wall-Clock Time for Full Archive (Single Energy Band)**: **8.1 to 12.9 hours** on modern x86_64 (or **11.0 hours** on Graviton3 ARM64) under a standard 96-vCPU Spot fleet.
* **Total Compute Cost (Single Energy Band)**: **$22.06 to $43.81** on EC2 Spot instances ($55.69 on On-Demand).
* **Ephemeral S3 Buffer & Request Overhead**: **$37.33** one-off.
* **Total End-to-End Mission Cost (Single Band)**: **~$59 to $81**.
* **Canonical 4-Band Spectral Survey (20–40, 40–100, 100–200, 200–600 keV)**: **~$125 to $210** completed in **~1.5 to 2 days**.

---

## 2. Archival Dimensions & Data Lifecycle

### 2.1 Mission Volume
* **Revolutions**: 2,886 revolutions (spanning 2002 to 2024).
* **Science Windows**: ~131,000 pointings (average of ~45.4 ScWs per revolution).
* **Raw Telemetry Footprint**:
  * Average raw compressed size per Science Window: $\approx 59\text{ MB}$.
  * Average raw data per revolution: $\approx 2.68\text{ GB}$.
  * Total mission raw data footprint: $\approx \mathbf{7.73\text{ TB}}$.

### 2.2 Ephemeral S3 Staging & Rolling Buffer Strategy
Storing the raw archival dataset statically in Amazon S3 indefinitely would incur substantial unnecessary costs:
$$\text{Static S3 Standard} = 7,730\text{ GB} \times \$0.023\text{/GB-month} = \$177.80\text{/month}$$

Instead, the modern pipeline implements an **ephemeral rolling-buffer model**:
1. **Dynamic Ingestion**: A dispatcher downloads raw science windows directly from NASA HEASARC / ISDC into a rolling S3 staging prefix in batches matching current worker capacity (~20 to 50 revolutions at a time, $\sim 300\text{--}500\text{ GB}$).
2. **Worker Streaming**: Parallel EC2 worker nodes stream their assigned revolution data from S3 across the AWS S3 Gateway Endpoint at up to 12.5 Gbps with **$0.00/GB data transfer charges**.
3. **Product Validation & Immediate Purge**: As soon as a worker validates the generated FITS products (mosaic sky maps, extracted source catalogs, lightcurves) and uploads them to the permanent S3 results prefix, the worker immediately deletes the raw revolution telemetry from S3 and local EBS storage.

### 2.3 S3 Storage & API Cost Accounting

| Component | Scope | Quantity / Duration | Unit Rate | Total Cost |
| :--- | :--- | :---: | :---: | :---: |
| **Ephemeral Raw Telemetry Buffer** | Active rolling staging buffer | $7.73\text{ TB}$ processed over 2 days | $\$0.023\text{/GB-mo} \times (2/30)$ | **$11.85** |
| **Calibration Tree (`caldb/`)** | Persistent IC master files | $4.55\text{ GB}$ (1 month) | $\$0.023\text{/GB-mo}$ | **$0.10** |
| **Permanent Science Output Archive** | Mosaics & catalogs for all 2,886 revs | $\approx 28.9\text{ GB}$ ($10\text{ MB/rev}$) | $\$0.023\text{/GB-mo}$ | **$0.66/month** |
| **S3 Ingestion PUT Requests** | Batch upload from HEASARC | $\approx 4,700,000\text{ files}$ | $\$0.005\text{ / 1,000 PUT}$ | **$23.50** |
| **Worker GET Requests** | Intra-region worker download | $\approx 4,700,000\text{ files}$ | $\$0.0004\text{ / 1,000 GET}$ | **$1.88** |
| **S3 DELETE Operations** | Purging raw data after completion | $\approx 4,700,000\text{ files}$ | $\$0.00$ | **$0.00** |
| **Total S3 Expense (One-Off Crunch)** | | | | **$37.33** |

---

## 3. Compute Concurrency & Multi-Process Node Packing

### 3.1 AWS Account Resource Quotas (Verified in `us-east-1`)
* **All Standard Spot Instance Requests (`L-34B43A08`)**: **96.0 vCPUs**
* **Running On-Demand Standard Instances (`L-1216C47A`)**: **64.0 vCPUs**

### 3.2 Threading & Memory Characteristics of OSA 11.2
* **Single-Threaded Architecture**: Legacy Fortran 90 and C pipeline binaries (`ibis_comp_energy`, `ibis_gti`, `ibis_dead`, `ibis_scw1_analysis`, `ii_skyimage`) are single-threaded processes.
* **Low Memory Footprint**: During peak execution to `IMA2`, a single revolution reduction requires only $\approx 1.2\text{--}1.8\text{ GB}$ of RAM.
* **Process Packing on Multi-Core Instances**:
  * On a `c7i.8xlarge` instance (32 vCPUs, 16 physical cores, 64 GB RAM), we can concurrently run **16 physical-core workers** (or up to 32 hyperthreaded workers) with zero memory starvation.
  * On AWS Graviton3 (`c7g.8xlarge`, 32 physical Neoverse V1 cores, 64 GB RAM), every vCPU is a dedicated physical core without hyperthreading, permitting **32 fully isolated concurrent workers** per instance.

---

## 4. Archival Execution Time Breakdown (Single Energy Band)

From empirical AWS EC2 100-Science Window benchmarks:
* **Modern x86_64 (`c7i.xlarge`)**: $14.59\text{ s}$ per ScW (Pipeline runtime: $1,459.5\text{ s}$ for 100 ScWs).
* **Native ARM64 (`c7g.xlarge`)**: $25.99\text{ s}$ per ScW (Pipeline runtime: $2,598.8\text{ s}$ for 100 ScWs).

### Workload Totals for 131,000 Science Windows
* **x86_64 Compute Core-Hours**: $131,000 \times 14.59\text{ s} = 1,911,290\text{ s} \approx \mathbf{530.9\text{ core-hours}}$.
* **Data Transfer & Decompression**: $\approx 110\text{ s}$ per 45-ScW revolution $\times 2,886\text{ revs} \approx 88.2\text{ worker-hours}$.
* **Total x86_64 Workload**: $\approx \mathbf{619.1\text{ worker-hours}}$.
* **Total ARM64 Workload**: $\approx \mathbf{1,055.7\text{ worker-hours}}$.

### Wall-Clock Time Under 96-vCPU Quota

| Fleet Configuration | Architecture | Topology & Concurrency | Active Workers | Wall-Clock Time to Process All 131,000 ScWs |
| :--- | :--- | :--- | :---: | :---: |
| **Physical-Core Packed** *(Optimal)* | Modern `x86_64` | 3 $\times$ `c7i.16xlarge` (or 6 $\times$ `c7i.8xlarge`), 1 worker per physical core | **48 workers** | **$\approx 12.9\text{ hours}$** |
| **Dense SMT Hyperthreaded** | Modern `x86_64` | 6 $\times$ `c7i.8xlarge`, 1 worker per vCPU (with ~25% contention) | **96 workers** | **$\approx 8.1\text{ hours}$** |
| **Independent 1-Node/Rev** *(Baseline)* | Modern `x86_64` | 24 $\times$ `c7i.xlarge` instances (4 vCPUs per node) | **24 workers** | **$\approx 25.8\text{ hours}$** (~1.1 days) |
| **Dense Graviton3 ARM64** | Native `ARM64` | 3 $\times$ `c7g.16xlarge` (or 24 $\times$ `c7g.xlarge`), 1 worker per vCPU | **96 workers** | **$\approx 11.0\text{ hours}$** |

---

## 5. Comprehensive Financial Cost Model

### 5.1 Single Energy Band Pass (e.g., 18–60 keV)

| Infrastructure Component | Instance / Resource | Price Rate | Consumption | Total Spend |
| :--- | :--- | :---: | :---: | :---: |
| **EC2 Compute (x86_64 Spot Packed)** | `c7i` fleet (96 vCPUs) | $\$0.01768\text{ / vCPU-hr}$ | $1,248\text{ vCPU-hrs}$ | **$22.06** |
| **EC2 Compute (x86_64 Spot 1-Node/Rev)** | 24 $\times$ `c7i.xlarge` | $\$0.0707\text{ / hr}$ | $24 \times 25.8\text{ hrs}$ | **$43.81** |
| **EC2 Compute (ARM64 Spot Packed)** | `c7g` fleet (96 vCPUs) | $\$0.01565\text{ / vCPU-hr}$ | $1,056\text{ vCPU-hrs}$ | **$16.53** |
| **EC2 Compute (x86_64 On-Demand)** | `c7i` fleet (96 vCPUs) | $\$0.04462\text{ / vCPU-hr}$ | $1,248\text{ vCPU-hrs}$ | **$55.69** |
| **EBS Local Scratch Volume** | $40\text{ GB}$ gp3 per worker | $\$0.0001096\text{ / GB-hr}$ | Ephemeral per job | **$2.72** |
| **Ephemeral S3 Staging & API Requests** | S3 Standard + GET/PUT | Standard rates | 2-day rolling buffer | **$37.33** |
| **VPC Data Transfer** | Intra-region S3 Gateway | $\$0.00\text{ / GB}$ | $\approx 8\text{ TB}$ | **$0.00** |
| **Total Cost (Spot x86_64 + S3 Buffer)** | | | | **$\approx \$62.11$** |

---

## 6. Multi-Energy Band Survey Scaling

In scientific workflows, observers often require energy-resolved mosaics and spectra across standard ISGRI bands:
* **Canonical 2-Band Survey**: 20–40 keV and 40–100 keV.
* **Canonical 4-Band Survey**: 20–40, 40–100, 100–200, and 200–600 keV.
* **Fine 8-Band Spectral Survey**: Octave-spaced bins spanning 18 to 1,000 keV.

Because raw event calibration (`ibis_comp_energy`) and GTI screening (`ibis_gti`) are computed once, subsequent energy bands only execute the deconvolution and mosaicking stages (`ibis_scw1_analysis` and `ii_skyimage`). Even under the conservative assumption of re-running the complete pipeline independently for each band:

| Scenario | Number of Bands | Total Wall-Clock Time (96 vCPUs) | EC2 Spot Compute | S3 Staging & Storage | Total Project Spend |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Single Broad Band** | 1 | $\approx 8\text{--}13\text{ hours}$ | $\$22\text{--}\$44$ | $\$37.33$ | **$\approx \$59\text{--}\$81$** |
| **Canonical 2-Band Survey** | 2 | $\approx 16\text{--}26\text{ hours}$ | $\$44\text{--}\$88$ | $\$37.33$ | **$\approx \$81\text{--}\$125$** |
| **Canonical 4-Band Survey** | 4 | $\approx 32\text{--}52\text{ hours}$ (~1.5–2 days) | $\$88\text{--}\$176$ | $\$37.33$ | **$\approx \$125\text{--}\$213$** |
| **Fine 8-Band Spectral Survey** | 8 | $\approx 64\text{--}104\text{ hours}$ (~3–4 days) | $\$176\text{--}\$352$ | $\$37.33$ | **$\approx \$213\text{--}\$389$** |

---

## 7. Multi-Architecture Spot Resilience & Operational Failover

A critical advantage of our dual containerization strategy (`cadarn/osa:11-modern-amd64` and `cadarn/osa:11-native-arm64`) is resilience against cloud spot preemption:
1. **Spot Deficit Mitigation**: If Intel Sapphire Rapids (`c7i`) spot pools in `us-east-1` experience capacity exhaustion or price spikes, the orchestrator automatically redirects pending revolutions to Graviton3 (`c7g`) instances.
2. **Deterministic Parity**: Because astrometric positions and reconstructed fluxes agree to within **99.95% numerical precision** across architectures, hybrid batches consisting of mixed x86_64 and ARM64 workers produce scientifically uniform survey catalogs.
3. **Zero Idle Overhead**: Every node executes an automated shutdown routine (`shutdown -h now`) upon pushing verified FITS products to S3, ensuring zero billable leakage during fleet scale-down.
