#!/usr/bin/env python3
"""Probe production ARW/NEF samples in parallel.

The individual parsers already exist:

* Sony ARW6 CRAW HQ -> llvc3_bitstream_probe.py
* Nikon HE/HE* NEF -> probe_nikon_he_nef.py

This wrapper makes the sample-matrix run reproducible and CPU-friendly. It is
intentionally a probe runner, not a pixel decoder.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any

from llvc3_bitstream_probe import (
    derive_metrics,
    find_llvc_streams,
    find_raw_subifd,
    parse_directory,
    parse_packet,
)
from probe_nikon_he_nef import probe as probe_nikon_he


def safe_stem(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", path.stem).strip("_") or "sample"


def sony_probe_report(path: Path) -> dict[str, Any]:
    raw_info, strip = find_raw_subifd(path)
    streams = find_llvc_streams(strip)
    if not streams:
        raise ValueError(f"no LLVC3 stream found in {path}")
    primary = streams[0]
    stream = strip[primary.offset : primary.offset + primary.length]
    consumed, entries, groups = parse_directory(stream)
    packets = [parse_packet(stream, entry) for entry in entries]
    all_valid = all(all(p.validation.values()) for p in packets)
    type_counts: dict[str, int] = {}
    for packet in packets:
        type_counts[str(packet.type2)] = type_counts.get(str(packet.type2), 0) + 1
    return {
        "input": str(path),
        "raw_subifd": asdict(raw_info),
        "llvc_header": asdict(primary.header),
        "llvc_streams": [asdict(s) for s in streams],
        "stream_offset_inside_raw_strip": primary.offset,
        "strip_preamble_first_32": strip[:32].hex(" "),
        "directory": {
            "consumed_bytes_after_stream_header": consumed - 0x10,
            "packet_base_offset": 0x80,
            "groups": groups,
            "entries": [asdict(e) for e in entries],
        },
        "packets": [asdict(p) for p in packets],
        "summary": {
            "packet_count": len(packets),
            "packet_type_counts": type_counts,
            "all_packet_validations_pass": all_valid,
            "total_packet_bytes": sum(p.directory_length for p in packets),
            "payload_bytes_from_records": sum(p.payload_bytes_from_records for p in packets),
            "metrics": derive_metrics(raw_info, len(strip), sum(s.length for s in streams)),
        },
    }


def probe_one(task: tuple[str, str]) -> dict[str, Any]:
    input_s, output_s = task
    path = Path(input_s)
    out = Path(output_s)
    suffix = path.suffix.lower()
    if suffix == ".arw":
        report = sony_probe_report(path)
    elif suffix == ".nef":
        report = probe_nikon_he(path)
    else:
        raise ValueError(f"unsupported sample suffix: {path}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "input": str(path),
        "output": str(out),
        "suffix": suffix,
        "status": "ok",
        "bytes": path.stat().st_size,
    }


def discover_samples(sample_dirs: list[Path], extra_nef_dirs: list[Path]) -> list[tuple[Path, str]]:
    seen: set[Path] = set()
    out: list[tuple[Path, str]] = []
    for directory in sample_dirs:
        if not directory.exists():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in {".arw", ".nef"} or path in seen:
                continue
            seen.add(path)
            out.append((path, "root"))
    for directory in extra_nef_dirs:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.NEF")) + sorted(directory.glob("*.nef")):
            if path in seen:
                continue
            seen.add(path)
            out.append((path, "local_download_nef"))
    return out


def output_path(base: Path, sample: Path, bucket: str) -> Path:
    name = f"probe_{safe_stem(sample)}.json"
    return base / name if bucket == "root" else base / bucket / name


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample-dir", type=Path, action="append", default=[Path("samples/raw_pixls")])
    ap.add_argument("--extra-nef-dir", type=Path, action="append", default=[])
    ap.add_argument("--out-dir", type=Path, default=Path("out/production_fit_samples"))
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ns = ap.parse_args()

    samples = discover_samples(ns.sample_dir, ns.extra_nef_dir)
    if not samples:
        raise RuntimeError("no ARW/NEF samples found")

    max_windows_workers = 61 if os.name == "nt" else len(samples)
    jobs = max(1, min(ns.jobs, len(samples), max_windows_workers))
    tasks = [(str(path), str(output_path(ns.out_dir, path, bucket))) for path, bucket in samples]

    results: list[dict[str, Any]] = []
    if jobs == 1:
        for task in tasks:
            results.append(probe_one(task))
            print(f"probed {task[0]}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(probe_one, task): task[0] for task in tasks}
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                print(f"probed {result['input']}", flush=True)

    manifest = {
        "kind": "parallel production-fit sample probe",
        "jobs": jobs,
        "sample_count": len(samples),
        "outputs": sorted(results, key=lambda r: r["input"]),
    }
    manifest_path = ns.out_dir / "parallel_probe_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"jobs": jobs, "sample_count": len(samples), "manifest": str(manifest_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
