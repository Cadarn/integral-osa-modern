# Tutorial: Fetching and analysing multiple revolutions of IBIS/ISGRI data

This guide shows how to use the `integral` CLI to:

1. download IBIS/ISGRI Science Windows (ScWs) from **several revolutions**, plus the calibration
   and auxiliary data they need,
2. run a single IBIS/ISGRI reduction with a mosaic that spans all of those revolutions, and
3. compare download times from the three sources the CLI supports: **NASA HEASARC over HTTPS**,
   the same archive in **NASA's public AWS S3 bucket**, and **ESA's INTEGRAL Science Legacy
   Archive (ISLA)**.

Every command runs from the repository root with `uv run`. Run `uv sync` once to set up the
environment.

---

## 0. Before you start

```bash
uv run integral status              # shows the architecture, Docker image and data archive path
uv run integral data mirror --test  # checks that each archive mirror responds, and how fast
```

The data archive root is `REP_BASE_PROD`. By default it is `~/science/integral_data_archive`.
Set the `REP_BASE_PROD` environment variable to override it for a single shell. This
does not change your saved config. Section 4 uses this to keep each mirror's downloads separate.

To set up a fresh archive and save its path in `~/.integralrc.json`:

```bash
uv run integral data init --path ~/science/integral_data_archive
```

### Data sources

| `--mirror` | Archive | What it serves |
| --- | --- | --- |
| `heasarc` (default) | NASA HEASARC, `https://heasarc.gsfc.nasa.gov/FTP/integral/data` | The full archive tree: ScWs, aux, catalogs, IC |
| `s3` | NASA HEASARC's public AWS bucket `s3://nasa-heasarc/integral/data/` (us-east-1) | Same full archive tree as `heasarc`. No AWS account or credentials needed; the CLI uses plain HTTPS. Fastest and free when run inside AWS us-east-1. |
| `isla` | ESA ISLA, `https://isla.esac.esa.int/tap` | Science Window data only (one zip per ScW). Catalogs, IC, aux and revolution context still come from HEASARC automatically. |
| `https://...` | Any HEASARC-style mirror | Everything, from that URL |
| `s3://bucket/prefix` | Any public S3 bucket with the HEASARC layout | Everything, read anonymously |

> [!NOTE]
> The old `isdc` mirror (`https://isdc.unige.ch/ftp/arc/rev_3`) has been **retired** from the
> CLI because `isdc.unige.ch` no longer resolves. Passing `--mirror isdc` now stops with an
> explanation instead of hanging. ISDC's web page still documents anonymous rsync
> (`rsync -lrtv isdcarc.unige.ch::arc/rev_3/scw/RRRR/ ...`), but that host did not answer on
> rsync, HTTPS or ping when tested (Nov 2026), so the CLI does not use it. If ISDC publishes a
> working HTTP endpoint with the standard `scw/ aux/ cat/ ic/ idx/` layout, pass it with
> `--mirror https://<host>/<path>`.
>
> ISLA does not list every ScW as public. For example, it has `006000040010` onwards for
> revolution 0060 but not `006000010010`. The `revolution` command asks ISLA which ScWs it
> holds, so this is handled automatically.

---

## 1. Download the calibration data (once)

A reduction needs the full IBIS instrument-characteristics (IC) tree. This download is large and
opt-in. You only need it once, whichever revolutions you analyse:

```bash
uv run integral data download calibration --ic-trees --instruments ibis,sc,irem
```

This command also fetches the reference catalogs (`cat/hec`, `cat/omc`) and the IC index
(`idx/ic`).

---

## 2. Download several revolutions

The `revolution` subcommand accepts a single revolution, a range, or a comma-separated list.
For each revolution, it downloads the pointing ScWs (IDs ending in `0010`), the revolution's
`aux/adp/<rev>.001/` attitude and orbit files, and the `scw/<rev>/rev.001/` context tree.
`og_create` needs all of these. Everything goes into one run with one timing summary.

```bash
uv run integral data download revolution 0060-0062 --count 20 --instruments ibis,sc,irem -y

# Other forms
uv run integral data download revolution 0102                 # one whole revolution
uv run integral data download revolution 0060,0071,0102-0104  # list and ranges mixed
uv run integral data download revolution 0102 --mirror s3     # from NASA's S3 bucket
```

A revolution that has no data on the chosen source is skipped with a warning.

Useful options:

| Option | Effect |
| --- | --- |
| `--count N` / `-n N` | Download only the first N pointing ScWs of each revolution (leave it out to get all of them). |
| `--from <scwid> --to <scwid>` | Download an explicit ID range (single revolution only). This also includes non-pointing ScWs. |
| `--dry-run` | List the files that would be downloaded without downloading them. |
| `--mirror heasarc\|s3\|isla\|<url>` / `-m` | Choose the data source (default: the configured `archive_mirror`, normally `heasarc`). |
| `-j N` / `--concurrency N` | Number of parallel HTTP/2 streams (default 16). ISLA is capped at 4 parallel ScW requests. |
| `--stats-file PATH` | Append this run's timing record (JSON, one line per run) to `PATH`. |
| `--science-only` / `--calib-only` | Download only the ScW data, or only the catalogs, IC index and aux data. |
| `-y` | Skip the "Proceed with download?" confirmation, and never switch mirrors automatically. |

Downloads can be resumed. A file that already exists locally is skipped, so if a run fails,
run the same command again. Use `--force-refresh` to download everything again. Transient
errors (timeouts, HTTP 429/502/503/504) are retried up to 3 times. If any file still fails,
the command lists it and exits with status 1.

If you already have a list of ScW IDs, possibly from several revolutions, use the `file`
subcommand. It fetches aux and revolution context for every revolution that appears in the list:

```bash
uv run integral data download file my_scws.txt
```

Check what you have locally:

```bash
uv run integral data status
```

---

## 3. Run one IBIS/ISGRI reduction across all revolutions

The `rev:0060:10` shorthand covers only **one** revolution. For more than one, build a ScW list
file from the local archive and pass it to `analyse ibis`:

```bash
ARCHIVE=$(uv run python -c "from integral.core.config import config; print(config.rep_base_prod)")

for rev in 0060 0061 0062; do
  ls "$ARCHIVE/scw/$rev" | grep -E '^[0-9]{8}0010\.001$' | sed 's/\.001$//'
done > scws_multi_rev.txt

wc -l scws_multi_rev.txt
```

Run the imaging and mosaic pipeline:

```bash
uv run integral analyse ibis scws_multi_rev.txt \
    --bands 18-60 \
    --og obs_multi_rev \
    --workdir ./work \
    -y
```

* `--mosaic` is on by default, so the run ends at `IMA2` and produces a mosaic covering every
  revolution. Pass several bands if you need them, for example `--bands "20-40, 40-100"`.
* Before the run starts, ScWs without usable `isgri_events.fits` data are dropped and listed
  under "Data Integrity Guard". These are typically aborted or unobserved pointings.
* When the run finishes, the CLI prints the total wall-clock time and the time per ScW. Products
  are written to `work/obs/obs_multi_rev/`, and the OSA log is saved as `obs_multi_rev_run.log`
  in the same directory.

Inspect the results:

```bash
find work/obs/obs_multi_rev -name "*mosa*ima*.fits"
uv run integral view image  <mosaic-fits>
uv run integral view sources <source-list-fits>
```

---

## 4. Timing HEASARC, S3 and ISLA downloads

### What the downloader reports

Every download command ends with a summary table. Time is split into phases, and each phase
shows files downloaded, files already cached, failures, volume, time and throughput. Below is
real output for two ScWs fetched from each source:

```
$ uv run integral data download scw 006000040010,006000050010 --science-only --mirror isla -y
...
Transfer summary · source: isla (catalogs/IC/aux from HEASARC)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━┓
┃ Phase              ┃ New ┃ Cached ┃ Failed ┃ Downloaded ┃  Time ┃      Rate ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━┩
│ Science Windows    │  76 │      0 │      0 │  130.89 MB │ 1m13s │ 1.78 MB/s │
├────────────────────┼─────┼────────┼────────┼────────────┼───────┼───────────┤
│ Total (wall clock) │  76 │      0 │      0 │  130.89 MB │ 1m13s │ 1.78 MB/s │
└────────────────────┴─────┴────────┴────────┴────────────┴───────┴───────────┘
16 concurrent streams (4 for ScW requests) · mirror probe latency 0.31s

$ uv run integral data download scw 006000040010,006000050010 --science-only --mirror heasarc -y
...
Transfer summary · source: heasarc
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━┓
┃ Phase              ┃ New ┃ Cached ┃ Failed ┃ Downloaded ┃  Time ┃      Rate ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━┩
│ ScW listing        │   0 │      0 │      0 │          - │  1.0s │         - │
│ Science Windows    │  76 │      0 │      0 │  132.73 MB │ 1m35s │ 1.40 MB/s │
├────────────────────┼─────┼────────┼────────┼────────────┼───────┼───────────┤
│ Total (wall clock) │  76 │      0 │      0 │  132.73 MB │ 1m36s │ 1.38 MB/s │
└────────────────────┴─────┴────────┴────────┴────────────┴───────┴───────────┘
16 concurrent streams · mirror probe latency 0.63s
```

This was one run from one UK network in November 2026. It shows what the output looks like and
is **not** a benchmark. Both archives produced byte-identical FITS contents after decompression;
the `.gz` files differ slightly in size because each archive compressed them separately.

* **Time** is wall-clock time for the phase. **Rate** is megabytes per second
  (1 MB = 10⁶ bytes) over that wall-clock time, so it reflects the parallel streams together.
* **Total** is the whole command from first request to last, including listings and the
  ISLA server building each zip. It does not include the connectivity check before the start
  (its latency is printed under the table) or time spent at the confirmation prompt.
* Phases: `Revolution listing`, `ScW listing` (HEASARC only: one directory listing per ScW),
  `Science Windows`, `Catalogs`, `IC index`, `IC tree (<inst>)`, `Aux reference`,
  `Aux (adp)` and `Revolution context`.
* If any file fails after 3 attempts, the failures are listed under the table and the command
  exits with status 1.

With `--stats-file timings.jsonl`, each run appends one JSON record containing the same figures:
source, URLs, concurrency, probe latency, wall seconds, bytes, MB/s, file counts, retries and
per-phase breakdown. Repeated runs collect in one file for comparison.

### Example: a whole revolution (0102) from each source

These are results from `integral data download revolution 0102 --mirror <source> -y`, run once per source into an empty
archive. The sources ran one after another (S3, ISLA, HEASARC) from a UK university network
on 22 Nov 2026, 00:53–02:28 UTC. Each run fetched 38 pointing ScWs plus catalogs, IC index,
aux and revolution context, at the default `-j 16`. ISLA was capped at 4 ScW requests.

| Source | ScW data | ScW time | ScW rate | Whole run | Total time | Overall rate |
| --- | --- | --- | --- | --- | --- | --- |
| `s3` | 2.78 GB (38 ScWs) | 21m47s | **2.13 MB/s** | 2.98 GB, 4,028 files | **25m13s** | 1.97 MB/s |
| `heasarc` | 2.78 GB (38 ScWs) | 24m45s | 1.87 MB/s | 2.98 GB, 4,028 files | 32m48s | 1.51 MB/s |
| `isla` | 2.61 GB (**30 of 38** ScWs) | 24m25s | 1.78 MB/s | 2.80 GB, 3,800 files | 30m19s | 1.54 MB/s |

What this showed:

* **S3 was the fastest source** and HEASARC HTTPS the slowest. Most of HEASARC's extra time
  was in the support files, not the ScWs. The 68 MB reference catalog took 3m42s over HTTPS
  against 49s from S3, and the 2,283-file revolution context took 4m00s against 2m25s. With
  ISLA, those support files came from HEASARC HTTPS, so ISLA's total includes the same slow
  steps.
* **ISLA does not have every ScW.** For 8 of the 38 pointings it answered "No public products
  found", although its catalogue lists them. Five of those have no ISGRI events, so the IBIS
  pipeline would drop them anyway. Three (`010200080010`, `010200340010`, `010200360010`)
  *do* have ISGRI data on HEASARC and S3. The CLI reports such ScWs in a separate
  "Not on source" column, not as failures, and prints the command to fetch them from HEASARC.
  Compare ISLA on throughput (MB/s), not total time, because it downloaded less data.
* **The content is identical across sources.** S3 and HEASARC files were byte-identical. ISLA's
  files matched them exactly after decompression.
* This was a single run per source, so treat it as indicative. Use the timing script below for
  repeated, alternating runs.

The full write-up, with a per-step breakdown, is in
[archive_download_timing_rev0102.md](archive_download_timing_rev0102.md).

### Making the comparison fair

1. **Cold cache.** Files that already exist are skipped. Give each source its own empty
   `REP_BASE_PROD`, or pass `--force-refresh`.
2. **Same ScWs.** ISLA does not publish every ScW that HEASARC lists. Build one list of ScW IDs
   and fetch it from every source with `data download file`. HEASARC HTTPS and S3 hold the
   same files.
3. **Science data only.** With `--mirror isla`, catalogs, IC and aux still come from HEASARC.
   Use `--science-only` so you compare only what each source actually serves.
4. **Same parallelism.** ISLA is capped at 4 parallel ScW requests. Run HEASARC and S3 at
   `-j 4` too for a like-for-like comparison, and optionally at the default `-j 16` to see
   their best case.
5. **Different transfer models.** HEASARC HTTPS and S3 download about 30–40 individual files per
   ScW after listing its directory. ISLA returns one zip per ScW. The CLI unpacks each zip into
   the same `scw/<rev>/<scwid>.001/` layout. Both approaches are part of what you are measuring.
6. **Where you run it.** S3 is in AWS us-east-1. From a laptop it behaves like any other
   transatlantic HTTPS server. From an EC2 instance in us-east-1 it is much faster, and the
   transfer is free.
7. **Noise.** Alternate the sources, repeat each several times, and compare medians.
8. **No silent switching.** With `-y`, or when stdin is not a terminal, the CLI never switches
   to the other mirror if the one you asked for is down. It reports the failure instead.

### Timing script

Save this script outside the repository, for example as `~/bin/time_mirrors.sh`:

```bash
#!/usr/bin/env bash
# Compare HEASARC HTTPS, NASA S3 and ESA ISLA download throughput for the same Science Windows.
set -uo pipefail

REPO=${REPO:-$HOME/science/integral-cloud-analysis}
OUT=${OUT:-$HOME/integral-mirror-timing}
SCWS=${SCWS:-006000040010,006000050010,006000060010,006000070010}   # IDs that both sources hold
JOBS=${JOBS:-4}
REPEATS=${REPEATS:-3}

mkdir -p "$OUT"
echo "$SCWS" | tr ',' '\n' > "$OUT/scws.txt"

for r in $(seq 1 "$REPEATS"); do
  for m in heasarc s3 isla; do
    export REP_BASE_PROD="$OUT/archive_$m"
    rm -rf "$REP_BASE_PROD"                      # cold cache for every run
    (cd "$REPO" && uv run integral data download file "$OUT/scws.txt" \
        --science-only --mirror "$m" -j "$JOBS" -y --stats-file "$OUT/timings.jsonl") \
      || echo "run $r on $m reported failures - see output above"
  done
done

# Median throughput of the Science Windows phase per source (failed/incomplete runs skipped)
python3 - "$OUT/timings.jsonl" <<'PY'
import json, statistics, sys
from collections import defaultdict
rows = defaultdict(list)
for line in open(sys.argv[1]):
    rec = json.loads(line)
    sci = next((p for p in rec["phases"] if p["name"] == "Science Windows"), None)
    if rec["completed"] and sci and not rec["files_failed"]:
        rows[(rec["source"], rec.get("science_streams", rec["concurrency"]))].append(
            (rec["wall_seconds"], sci["mb_per_s"], rec["bytes"]))
print(f"{'source':10} {'streams':>7} {'runs':>4} {'median wall s':>14} {'median MB/s':>12} {'MB':>8}")
for (src, j), v in sorted(rows.items()):
    print(f"{src:10} {j:>7} {len(v):>4} {statistics.median(x[0] for x in v):>14.1f} "
          f"{statistics.median(x[1] for x in v):>12.2f} {v[0][2] / 1e6:>8.1f}")
PY
```

Run it with `bash ~/bin/time_mirrors.sh`. Change `SCWS`, `JOBS` and `REPEATS` with
environment variables, for example `JOBS=16 REPEATS=5 bash ~/bin/time_mirrors.sh`. Every raw record
stays in `~/integral-mirror-timing/timings.jsonl`.

**Large-file throughput (HEASARC only).** IC tree files are much larger than ScW files.
Only HEASARC serves them, so this measures HEASARC alone:

```bash
REP_BASE_PROD=$HOME/integral-mirror-timing/ic_test uv run integral data download calibration \
  --ic-trees --instruments ibis -y --stats-file ~/integral-mirror-timing/timings.jsonl
```
