# INTEGRAL archive download timing: HEASARC HTTPS vs NASA S3 vs ESA ISLA

## Summary
For one full revolution of IBIS/ISGRI data (rev 0102), **NASA's public S3 bucket was the fastest
source, about 23% quicker than HEASARC over HTTPS**. ESA's ISLA was similar to HEASARC on
throughput but **did not supply 8 of the 38 pointings**. All three sources serve identical data.
The ISDC (Geneva) archive could not be tested because it is unreachable.

## What was tested
- **Data:** revolution 0102, all 38 pointing Science Windows (IDs ending `0010`) plus the files
  a reduction needs: catalogs, calibration index, aux attitude/orbit files and revolution
  context. That is about 3 GB and 4,028 files. The large per-instrument calibration trees were
  not included.
- **Method:**
  - One `integral data download revolution 0102 --mirror <source> -y` per source.
  - Each run started from an empty local archive.
  - Runs went one after another (S3, ISLA, HEASARC), not in parallel.
  - The CLI recorded wall-clock time, bytes and throughput for each step.
- **Settings:** default 16 parallel streams. ISLA is capped at 4 parallel requests out of
  courtesy to the service.
- **Where:** a UK university network, 22 Nov 2026, 00:53–02:28 UTC.

## Results

| Source | Science Window data | ScW rate | Whole run | Total time | Overall rate |
|---|---|---|---|---|---|
| **NASA S3** (`s3://nasa-heasarc`) | 2.78 GB, 38/38 ScWs, 21m47s | **2.13 MB/s** | 2.98 GB, 4,028 files | **25m13s** | **1.97 MB/s** |
| NASA HEASARC HTTPS | 2.78 GB, 38/38 ScWs, 24m45s | 1.87 MB/s | 2.98 GB, 4,028 files | 32m48s | 1.51 MB/s |
| ESA ISLA | 2.61 GB, **30/38** ScWs, 24m25s | 1.78 MB/s | 2.80 GB, 3,800 files | 30m19s | 1.54 MB/s |

ISLA serves only Science Window data, so its run fetched the catalogs, calibration index and aux
files from HEASARC HTTPS. MB = 10⁶ bytes; rates are bytes divided by wall-clock time.

### Per-step breakdown

| Step | S3 | HEASARC HTTPS | ISLA run |
|---|---|---|---|
| Revolution listing | 0.5 s | 3.1 s | 0.4 s |
| ScW directory listings | 3.0 s | 3.6 s | (not needed) |
| Science Windows | 21m47s · 2.13 MB/s | 24m45s · 1.87 MB/s | 24m25s · 1.78 MB/s |
| Catalogs (68.5 MB, 2 files) | 48.6 s | 3m42s | 1m36s (from HEASARC) |
| IC index (5.0 MB, 126 files) | 6.5 s | 11.7 s | 9.5 s (from HEASARC) |
| Aux reference + aux/adp (0.8 MB, 87 files) | 2.5 s | 2.4 s | 2.0 s (from HEASARC) |
| Revolution context (121 MB, 2,283 files) | 2m25s | 4m00s | 4m06s (from HEASARC) |
| Retried requests | 2 | 0 | 4 |

## Findings

1. **S3 was fastest overall, mainly on the supporting files.** For ScW data, S3 was about 14%
   faster than HEASARC HTTPS. The bigger difference was in the supporting files: the 68 MB
   reference catalog took 49 s from S3 against 3m42s over HTTPS, and the 2,283-file revolution
   context took 2m25s against 4m00s.
2. **ISLA has gaps.** Its catalogue lists all 38 ScWs, but it returned "No public products found"
   for 8:
   - Five of them have no ISGRI events, so an IBIS analysis would drop them anyway.
   - Three do have ISGRI data on HEASARC and S3: `010200080010`, `010200340010` and
     `010200360010`.

   So ISLA alone would have lost 3 usable pointings from this revolution.
3. **The data is identical across sources.**
   - S3 and HEASARC files were byte-identical, apart from `idx/ic/ic_master_file.fits`, which the
     CLI edits locally after every download.
   - ISLA's files were byte-identical to them after decompression; only the gzip compression
     differs.
4. **ISDC is not available.** The HTTP archive hostname `isdc.unige.ch` no longer resolves. ISDC's
   [archive page](https://www.astro.unige.ch/integral/archive/release) still documents anonymous
   rsync (`isdcarc.unige.ch::arc/rev_3/...`), but the host did not answer on rsync, HTTPS or ping.
   Other rsync servers were reachable from the same network.

## Recommendations
- **Default to S3** (`--mirror s3`) for bulk revolution downloads. It has the full archive tree,
  needs no AWS account, and was fastest even from a UK network. Inside AWS us-east-1 it should be
  much faster still, and the transfer is free. That makes it the obvious choice for the
  cloud/Kubernetes batch path.
- **Keep HEASARC HTTPS as the fallback.** It holds the same data as S3.
- **Use ISLA only as a secondary European source.** If you use it, check the "Not on source" list
  the CLI prints and fetch those ScWs from HEASARC.

## Caveats
- This is one run per source at one time of day, so treat it as indicative rather than a
  benchmark.
- Network conditions vary. Repeated, alternating runs comparing medians would give firmer
  numbers.
- ISLA ran with 4 parallel requests against 16 for the others, and downloaded less data. Compare it
  on MB/s, not total time.

## Reproducing
Each source must download into its own empty archive (`REP_BASE_PROD`), because
already-downloaded files are skipped.

```bash
REP_BASE_PROD=/tmp/test_s3 uv run integral data download revolution 0102 --mirror s3 -y --stats-file timings.jsonl
REP_BASE_PROD=/tmp/test_heasarc uv run integral data download revolution 0102 --mirror heasarc -y --stats-file timings.jsonl
REP_BASE_PROD=/tmp/test_isla uv run integral data download revolution 0102 --mirror isla -y --stats-file timings.jsonl
```

Each run prints a per-step timing table and appends a JSON record to `timings.jsonl`.
[tutorial_ibis_multi_revolution.md](tutorial_ibis_multi_revolution.md) has a script for repeated,
alternating runs that reports median throughput.
