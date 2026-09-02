# Technical Roadmap: Benchmarking, Cloud Scale-Out, and a GUI

**Status**: proposal for discussion — no code from this document has been implemented yet.
**Related reading**: `docs/status_report.md` (current state), `docs/technical_rebuild_arm64.md`
(the native ARM64 build and its existing single-run benchmark).

This document lays out three largely independent workstreams raised alongside the native ARM64
work: producing a benchmark rigorous enough to support an MNRAS methods/techniques paper, a
genuine path to scalable cloud computation, and whether (and how) to build a GUI now that the
legacy OSA interactive tools no longer run in the modernised containers. Each section presents
options with their technical and user-value trade-offs and a recommendation, followed by a
proposed sequencing across all three.

---

## A. MNRAS benchmark experimental plan

### Why it matters

The paper draft's headline numbers — 2.44× speedup, sub-0.07% numerical consistency — currently
rest on a single IBIS-only, single-run measurement (10 Science Windows, one architecture pair, no
repeats, no per-stage breakdown, no automated evidence trail). `docs/status_report.md` §1 now notes
a concrete illustration of the risk here: an earlier, separate single run of the same emulated
baseline recorded a total time about 7% higher than the figure now quoted in
`technical_rebuild_arm64.md`, purely from ordinary run-to-run variance. A referee will expect
repeated measurements with reported variance before accepting a headline speedup figure.

### Gaps in the current benchmark

- `integral_cli/benchmark.py` only exercises IBIS end-to-end. JEM-X (both units), OMC, and SPI —
  all of which have working `integral analyse` pipelines — have zero benchmark coverage.
- No repeats, so no variance or confidence interval on the reported speedup.
- Only total wall-clock is captured. Each run already produces `commonlog.txt`
  (`COMMONLOGFILE`, set in every `analyse` command's bash heredoc) which likely carries per-stage
  timestamps; mining it would let the paper show *where* the ARM64 gain comes from — is it uniform
  across pipeline stages, or concentrated in specific compute-bound steps such as `ii_skyimage` or
  `ibis_isgr_energy`?
- Only one ScW count (10) has been measured, despite a 10/25/50 scaling comparison being flagged as
  planned work as far back as the original status report.
- No host telemetry (chip model, free core count, thermal state) is recorded alongside timings.
- `scripts/validate_science_products.py` (the FITS numerical-consistency checker) is never
  actually invoked by the benchmark or by CI — the "sub-0.07%" figure is produced by a one-off
  manual comparison, not a reproducible, checked-in run.

### Proposed experimental design

A factorial benchmark matrix:

- **Instrument**: IBIS, JEM-X unit 1, JEM-X unit 2, OMC, SPI.
- **Architecture**: native ARM64, modern-amd64-under-emulation on Apple Silicon (the existing
  Rosetta/QEMU baseline), and optionally modern-amd64-on-real-x86 (a genuine cloud x86 instance) —
  the third arm separates "Apple Silicon is fast" from "ARM64 the instruction set is fast", which
  strengthens the paper's generality claim.
- **ScW count**: 10, 25, 50, and optionally a full revolution (214 ScWs for Rev 0060).
- **Repeats**: at least 3 per cell, so mean ± standard deviation (and a simple effect-size/t-test)
  can be reported rather than a single point estimate.

Recorded per run: total wall-clock, per-stage wall-clock (parsed from `commonlog.txt`), peak
memory, and a `validate_science_products.py compare` pass/fail plus maximum relative deviation
against a fixed x86_64 reference tree.

**Automation**: extend `integral_cli/benchmark.py` from its current single-tier IBIS-only
implementation into a `benchmark matrix` command that loops over instrument × architecture ×
ScW-count × repeat, invoking each `analyse <instrument>` command (reusing the `_resolve_scw_ids`
helper), parsing `commonlog.txt`, and running the validator — appending one JSON row per run to a
checked-in `benchmark_results.jsonl`. That file becomes the paper's actual evidence trail, rather
than numbers quoted only in prose. A small companion script (or an addition to `viewer.py`) reads
that file back and emits the summary table(s) and a speedup-vs-ScW-count figure for direct reuse in
`technical_rebuild_arm64.md`.

### Options

1. **Minimal** — extend to all four instruments, one repeat each, keep the 10-ScW size. Cheapest,
   but no variance reporting and no stage-level breakdown; the weakest option for peer review.
2. **Recommended** — the full matrix above (4 instruments × up to 3 architectures × 2–3 ScW counts
   × 3 repeats). A realistic unattended-overnight compute budget: the existing 10-ScW IBIS run
   already completes in a few minutes, so even a few dozen matrix cells is a manageable batch job.
3. **Extended** — additionally include a real cloud x86_64 instance and a Graviton ARM64 instance.
   Strengthens the generality claim further but introduces cloud cost/access as a new dependency,
   and overlaps with the cloud-scalability track below.

### User value

Primarily research-output value: a referee-proof dataset for the paper. As a side effect, the
project gains a proper regression benchmark it currently lacks entirely, useful for catching future
performance regressions in either the OSA build or the CLI itself.

---

## B. Cloud scalability pathway

### Current state

`k8s/job-template.yaml` and `k8s/node-pool-spot.yaml` (Karpenter/GKE spot node pools with
scale-to-zero) exist alongside `pipeline/scw_distributor.py` (partitions a ScW list into JSON batch
manifests) and `pipeline/runner_scw.sh` (the in-pod worker script), but none of it is wired to the
CLI — using it today means hand-editing manifests and running `kubectl apply` directly. Beyond
that:

- `runner_scw.sh` uses `og_create idxLevel=0 ... scwList="scw.list"` and
  `IC_Group="/data/ic/ic_master_file.fits[1]"`/`IC_Alias="OSA11"`, which differ from
  `analysis.py`'s `og_create idxSwg="scw.list" ...` and
  `IC_Group="/data/idx/ic/ic_master_file.fits[1]"`/`IC_Alias="OSA"` — these conventions have never
  been reconciled or tested against each other.
- `runner_scw.sh` only has branches for IBIS and JEM-X; OMC and SPI, both supported locally by the
  CLI, have no cloud equivalent.
- `k8s/job-template.yaml` sets `S3_BUCKET`/`GCS_BUCKET` environment variables, but nothing in
  `runner_scw.sh` reads them — there's no actual data staging in or results upload out. Output
  currently lands under `/output/<scw>` inside the pod's own ephemeral filesystem and would be lost
  when the pod is reaped.

### Options

1. **Recommended (near-term)** — mature the existing Kubernetes path: reconcile `runner_scw.sh`
   with `analysis.py`'s conventions, add OMC/SPI branches, add S3/GCS staging in and results
   upload out, and add an `integral cloud submit` / `integral cloud status` CLI pair that
   partitions ScWs (reusing `scw_distributor.py`) and applies/polls the Job via the Kubernetes
   Python client. This reuses everything already written and keeps the already-designed
   spot-to-zero cost model (70–90% savings, per `k8s/node-pool-spot.yaml`'s own comment).
2. **Serverless alternative** — AWS Batch or GCP Cloud Run Jobs instead of Kubernetes. Same
   spot-priced, scale-to-zero properties, without operating a live cluster control plane — likely
   a better operational fit for a bursty "run a big reduction occasionally, then sit idle for
   weeks" workload than maintaining Kubernetes. `Dockerfile.batch` and `runner_scw.sh` remain
   directly reusable; the `k8s/*.yaml` manifests would not be.
3. **Hybrid, queue-based** — decouple the CLI from `kubectl` specifics via a message queue
   (SQS/PubSub) that workers poll. Most flexible and scalable long-term, but the most upfront
   engineering effort, and likely disproportionate unless usage grows well past occasional
   large reductions.

### User value

Turns "can theoretically scale" into "can actually submit a multi-revolution reduction and get
results back" — directly useful both for running the benchmark matrix in section A faster, and for
any future large-scale survey reprocessing.

---

## C. GUI / launch-and-configure tooling

### The problem

The legacy OSA tools include interactive display tooling that depends on X11 forwarding — visible
in the codebase as `docker_mgr.py`'s `--gui` flag and the archived `docker/legacy/run.sh` /
`osa-container.sh` scripts, which mount `/tmp/.X11-unix` and forward `$DISPLAY`. The modernised
slim containers (`Dockerfile.modern`, `Dockerfile.native-arm64`, `Dockerfile.batch`) strip
X11/GUI libraries entirely by design, and every `analyse` command's bash heredoc explicitly sets
`export DISPLAY=""` during the actual pipeline run. The legacy interactive tools are therefore
architecturally incompatible with the modernised containers, and today the only interface is the
Typer CLI.

### Options

The project is already entirely Python (`uv`/Typer/Rich); the options below stay Python-first,
since introducing Rust would add a second toolchain, a second dependency-locking story, and a
second thing to cross-compile for ARM64/x86 — for a workload (waiting on Docker and OSA binaries)
that's I/O-bound rather than UI-render-bound, so Rust's performance advantage buys little.

1. **Recommended for v1: a Textual TUI.** [`Textual`](https://github.com/Textualize/textual) is
   from the same team as `rich` (already a dependency) and composes naturally with it. A form-based
   interface for picking instrument, ScW range, energy band, and working directory, launching
   through the *existing* `docker_mgr`/`analysis`/`data_mgr` functions directly — no new execution
   engine — with a live log tail from the running container. Works identically over SSH to a
   remote Docker host or cloud VM, with no new ports to open. Smallest new-dependency footprint,
   fastest to build a genuinely useful v1, and it's an additive UI shell rather than a rewrite.
2. **Local web dashboard** (FastAPI + HTMX, `NiceGUI`, or `Streamlit`). Enables real visuals —
   inline FITS mosaic/image previews, a job history table, live progress across several concurrent
   jobs — and is reachable from other machines on a LAN. Heavier dependency footprint and a second
   process model (server + browser) than a TUI; raises an authentication question if ever exposed
   beyond localhost.
3. **Native desktop GUI** (PySide6/PyQt). The most "app-like" feel and native file pickers, but the
   heaviest packaging/distribution burden (per-OS binaries, no natural `uv`/PyPI distribution the
   way a CLI/TUI has), for an audience — the CLI's actual current users — who are already
   terminal-literate.
4. **Rust TUI** (`ratatui`). Technically excellent, but introduces a second language purely for UI
   polish on a workload with no real UI performance bottleneck. Not recommended.

### Recommendation

Build a Textual TUI now (e.g. as `integral tui`), wrapping the existing `analysis_app`/
`docker_mgr`/`data_mgr` functions rather than replacing them. Revisit a browser dashboard later,
specifically if live FITS-image preview or watching many concurrent cloud jobs at once (from
track B) becomes a real need — a single-user TUI suits local/occasional use well; a browser
dashboard suits multi-job/remote monitoring better.

### User value

Lowers the barrier to configuring and launching a run (fewer flags to memorise), and gives live
feedback during long reductions without reading raw container logs.

---

## Prioritised sequencing

Given how different these three workstreams are, a proposed order:

1. **Now** — the hygiene pass (CLI rename, British English, dead-file archiving, low-risk
   refactors) already completed alongside this document.
2. **Near-term, paper-critical** — track A (benchmark matrix + automation). This has an implicit
   external deadline (the submission) and produces the evidence everything else in this roadmap
   ultimately sits on top of.
3. **Near/medium-term, usability** — track C option 1 (Textual TUI). Comparatively small effort
   since it wraps existing functions, and it directly makes running track A's benchmark matrix
   itself easier.
4. **Medium-term, infrastructure** — track B (cloud maturation). The highest effort and
   operational risk (it needs a real cluster to validate against), and its main current
   justification — large-scale survey reprocessing — isn't a precondition for the paper, so it's
   sequenced last to avoid competing with the publication deadline.

This ordering is a starting point for discussion, not a fixed commitment. In particular, if the
full benchmark matrix in track A turns out to need more compute than a single laptop can run
overnight, a minimal slice of track B (just enough to fan the matrix out across a few cloud
workers) may need to be pulled forward rather than strictly following after the paper work.
