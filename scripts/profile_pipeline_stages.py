import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from rich.console import Console
from rich.table import Table

console = Console()

STAGE_MAPPING = {
    "og_create": "1. Setup & OG Init",
    "dal_create": "1. Setup & OG Init",
    "dal_attach": "1. Setup & OG Init",
    "dal_clean": "1. Setup & OG Init",
    "ibis_isgr_energy": "2. Energy Calibration & Tagging",
    "ibis_isgr_evts_tag": "2. Energy Calibration & Tagging",
    "ibis_correction": "2. Energy Calibration & Tagging",
    "gti_create": "3. GTI & Deadtime",
    "gti_attitude": "3. GTI & Deadtime",
    "gti_data_gaps": "3. GTI & Deadtime",
    "gti_merge": "3. GTI & Deadtime",
    "ibis_gti": "3. GTI & Deadtime",
    "ibis_isgr_deadtime": "3. GTI & Deadtime",
    "ibis_dead": "3. GTI & Deadtime",
    "ii_shadow_build": "4. Shadowgrams & Background",
    "ibis_binning": "4. Shadowgrams & Background",
    "ii_pif": "4. Shadowgrams & Background",
    "ii_map_rebin": "4. Shadowgrams & Background",
    "ii_shadow_ubc": "4. Shadowgrams & Background",
    "ibis_background_cor": "4. Shadowgrams & Background",
    "ghost_busters": "5. Deconvolution & Imaging",
    "ibis_scw1_analysis": "5. Deconvolution & Imaging",
    "ibis_scw2_analysis": "6. Mosaicking & Catalog",
}


@dataclass
class ToolExecution:
    tool: str
    stage: str
    start: datetime
    end: datetime
    duration: float


def parse_commonlog(log_path: Path) -> list[ToolExecution]:
    """Parse commonlog.txt to extract all executed tasks and their exact durations."""
    if not log_path.exists():
        raise FileNotFoundError(f"Log file not found: {log_path}")

    re_start = re.compile(
        r"Log_\d+\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\s+([a-zA-Z0-9_-]+)\s+[\d\.]+:\s+Task\s+([a-zA-Z0-9_-]+)\s+running"
    )
    re_end = re.compile(
        r"Log_\d+\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\s+([a-zA-Z0-9_-]+)\s+[\d\.]+:\s+Task\s+([a-zA-Z0-9_-]+)\s+terminating\s+with\s+status\s+(\d+)"
    )

    open_tasks: dict[str, list[datetime]] = defaultdict(list)
    executions: list[ToolExecution] = []

    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m_start = re_start.search(line)
            if m_start:
                ts_str, _, tool = m_start.groups()
                ts = datetime.fromisoformat(ts_str)
                open_tasks[tool].append(ts)
                continue

            m_end = re_end.search(line)
            if m_end:
                ts_str, _, tool, _status = m_end.groups()
                ts = datetime.fromisoformat(ts_str)
                if open_tasks[tool]:
                    start_ts = open_tasks[tool].pop()
                    duration = max(0.0, (ts - start_ts).total_seconds())

                    if tool == "ii_skyimage":
                        stage = (
                            "6. Mosaicking & Catalog"
                            if duration > 15.0
                            else "5. Deconvolution & Imaging"
                        )
                    else:
                        stage = STAGE_MAPPING.get(tool, "7. Other / Scripts")

                    executions.append(
                        ToolExecution(
                            tool=tool,
                            stage=stage,
                            start=start_ts,
                            end=ts,
                            duration=duration,
                        )
                    )

    return executions


def compute_distribution_stats(durations: list[float]) -> dict[str, float]:
    """Calculate descriptive statistics and distribution metrics."""
    if not durations:
        return {
            "count": 0,
            "total": 0.0,
            "mean": 0.0,
            "std": 0.0,
            "median": 0.0,
            "q25": 0.0,
            "q75": 0.0,
            "min": 0.0,
            "max": 0.0,
        }

    arr = np.array(durations, dtype=float)
    return {
        "count": len(arr),
        "total": float(arr.sum()),
        "mean": float(arr.mean()),
        "std": float(arr.std(ddof=1) if len(arr) > 1 else 0.0),
        "median": float(np.median(arr)),
        "q25": float(np.percentile(arr, 25)),
        "q75": float(np.percentile(arr, 75)),
        "min": float(arr.min()),
        "max": float(arr.max()),
    }


def aggregate_with_distributions(
    executions: list[ToolExecution],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Aggregate execution times by stage and by tool with statistical distributions."""
    stage_durations: dict[str, list[float]] = defaultdict(list)
    tool_durations: dict[str, list[float]] = defaultdict(list)
    tool_stages: dict[str, str] = {}

    for ex in executions:
        stage_durations[ex.stage].append(ex.duration)
        tool_durations[ex.tool].append(ex.duration)
        tool_stages[ex.tool] = ex.stage

    stage_stats: dict[str, dict[str, Any]] = {}
    for stage, durs in stage_durations.items():
        stage_stats[stage] = compute_distribution_stats(durs)

    tool_stats: dict[str, dict[str, Any]] = {}
    for tool, durs in tool_durations.items():
        stats = compute_distribution_stats(durs)
        stats["stage"] = tool_stages.get(tool, "")
        tool_stats[tool] = stats

    return stage_stats, tool_stats


def compare_profiles(
    arm_log: Path,
    x86_log: Path,
    label: str = "Benchmark Comparison",
) -> dict[str, Any]:
    """Compare stage and tool profiles between ARM64 and x86 logs."""
    console.print(f"\n[bold cyan]═══ Profiling: {label} ═══[/bold cyan]")
    console.print(f"• ARM64 Log: {arm_log}")
    console.print(f"• x86 Log:   {x86_log}")

    arm_execs = parse_commonlog(arm_log)
    x86_execs = parse_commonlog(x86_log)

    arm_stages, arm_tools = aggregate_with_distributions(arm_execs)
    x86_stages, x86_tools = aggregate_with_distributions(x86_execs)

    all_stages = sorted(set(arm_stages.keys()) | set(x86_stages.keys()))

    # Table 1: Stage Comparison
    table = Table(
        title=f"Processing Stage Performance Breakdown ({label})",
        header_style="bold magenta",
    )
    table.add_column("Pipeline Stage", style="cyan", no_wrap=True)
    table.add_column("Native ARM64 Total", justify="right")
    table.add_column("ARM64 Mean ± Std", justify="right")
    table.add_column("Emulated x86 Total", justify="right")
    table.add_column("x86 Mean ± Std", justify="right")
    table.add_column("Speedup", justify="right", style="bold green")

    total_arm = sum(s["total"] for s in arm_stages.values())
    total_x86 = sum(s["total"] for s in x86_stages.values())

    report_data = {
        "label": label,
        "total_arm_seconds": total_arm,
        "total_x86_seconds": total_x86,
        "stages": {},
        "tools": {},
    }

    for stage in all_stages:
        st_arm = arm_stages.get(stage, compute_distribution_stats([]))
        st_x86 = x86_stages.get(stage, compute_distribution_stats([]))

        t_arm = st_arm["total"]
        t_x86 = st_x86["total"]
        speedup = (t_x86 / t_arm) if t_arm > 0 else 0.0

        report_data["stages"][stage] = {
            "arm": st_arm,
            "x86": st_x86,
            "speedup": speedup,
        }

        table.add_row(
            stage,
            f"{t_arm:7.1f} s",
            f"{st_arm['mean']:5.2f} ± {st_arm['std']:4.2f}s",
            f"{t_x86:7.1f} s",
            f"{st_x86['mean']:5.2f} ± {st_x86['std']:4.2f}s",
            f"{speedup:5.2f}×",
        )

    tot_speedup = (total_x86 / total_arm) if total_arm > 0 else 0.0
    table.add_section()
    table.add_row(
        "[bold]Total Instrumented Time[/bold]",
        f"[bold]{total_arm:7.1f} s[/bold]",
        "-",
        f"[bold]{total_x86:7.1f} s[/bold]",
        "-",
        f"[bold green]{tot_speedup:5.2f}×[/bold green]",
    )

    console.print(table)

    # Table 2: Top Tools Breakdown with Per-Invocation Distributions
    tool_table = Table(
        title=f"Per-Invocation Tool Statistical Distributions ({label})",
        header_style="bold blue",
    )
    tool_table.add_column("Executable Tool", style="yellow")
    tool_table.add_column("Stage", style="dim")
    tool_table.add_column("Calls", justify="right")
    tool_table.add_column("ARM64 Mean ± Std", justify="right")
    tool_table.add_column("ARM64 Median (IQR)", justify="right")
    tool_table.add_column("x86 Mean ± Std", justify="right")
    tool_table.add_column("x86 Median (IQR)", justify="right")
    tool_table.add_column("Speedup", justify="right", style="bold green")

    all_tools = sorted(
        set(arm_tools.keys()) | set(x86_tools.keys()),
        key=lambda k: x86_tools.get(k, {}).get("total", 0.0),
        reverse=True,
    )

    for tool in all_tools[:12]:
        stats_arm = arm_tools.get(tool, compute_distribution_stats([]))
        stats_x86 = x86_tools.get(tool, compute_distribution_stats([]))

        t_arm = stats_arm["total"]
        t_x86 = stats_x86["total"]
        calls = stats_arm["count"] or stats_x86["count"]
        st = stats_arm.get("stage") or stats_x86.get("stage", "")
        speedup = (t_x86 / t_arm) if t_arm > 0 else 0.0

        iqr_arm = stats_arm["q75"] - stats_arm["q25"]
        iqr_x86 = stats_x86["q75"] - stats_x86["q25"]

        report_data["tools"][tool] = {
            "calls": calls,
            "stage": st,
            "arm": stats_arm,
            "x86": stats_x86,
            "speedup": speedup,
        }

        tool_table.add_row(
            tool,
            st,
            str(calls),
            f"{stats_arm['mean']:5.2f} ± {stats_arm['std']:4.2f}s",
            f"{stats_arm['median']:4.1f}s ({iqr_arm:3.1f}s)",
            f"{stats_x86['mean']:5.2f} ± {stats_x86['std']:4.2f}s",
            f"{stats_x86['median']:4.1f}s ({iqr_x86:3.1f}s)",
            f"{speedup:5.2f}×",
        )

    console.print(tool_table)
    return report_data


def main():
    parser = argparse.ArgumentParser(
        description="Profile INTEGRAL/OSA pipeline execution stages and distributions from commonlog.txt"
    )
    parser.add_argument("--arm-log", type=Path, help="Path to Native ARM64 commonlog.txt")
    parser.add_argument("--x86-log", type=Path, help="Path to Emulated x86 commonlog.txt")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Profile all available Phase B benchmark runs",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmark_runs/phase_b/stage_profiling_results.json"),
        help="Output JSON summary path",
    )

    args = parser.parse_args()

    results: dict[str, Any] = {}

    if args.all:
        benchmarks = [
            (
                "10 ScWs (Repeat 1)",
                Path("benchmark_runs/phase_b/run_scw10_arm64/rep_1/commonlog.txt"),
                Path("benchmark_runs/phase_b/run_scw10_x86/rep_1/commonlog.txt"),
            ),
            (
                "25 ScWs (Repeat 1)",
                Path("benchmark_runs/phase_b/run_scw25_arm64/rep_1/commonlog.txt"),
                Path("benchmark_runs/phase_b/run_scw25_x86/rep_1/commonlog.txt"),
            ),
            (
                "Full Rev (100 vs 104 ScWs)",
                Path("benchmark_runs/phase_b/run_scw100_arm64/rep_1/commonlog.txt"),
                Path("benchmark_runs/phase_b/run_scw104_x86/rep_1/commonlog.txt"),
            ),
        ]

        for label, arm_p, x86_p in benchmarks:
            if arm_p.exists() and x86_p.exists():
                results[label] = compare_profiles(arm_p, x86_p, label=label)
            else:
                console.print(
                    f"[yellow]Skipping {label}: one or both log files not found.[/yellow]"
                )
    elif args.arm_log and args.x86_log:
        results["Custom"] = compare_profiles(args.arm_log, args.x86_log, label="Custom Comparison")
    else:
        def_arm = Path("benchmark_runs/phase_b/run_scw10_arm64/rep_1/commonlog.txt")
        def_x86 = Path("benchmark_runs/phase_b/run_scw10_x86/rep_1/commonlog.txt")
        if def_arm.exists() and def_x86.exists():
            results["10 ScWs"] = compare_profiles(def_arm, def_x86, label="10 ScWs (Repeat 1)")
        else:
            parser.print_help()
            return

    if results:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        console.print(
            f"\n[green]✓ Saved structured stage profiling results with statistical distributions to {args.output}[/green]"
        )


if __name__ == "__main__":
    main()
