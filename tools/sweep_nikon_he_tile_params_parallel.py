#!/usr/bin/env python3
"""Parallel Nikon HE tile-parameter sweep.

Runs LibRaw decodes for a parameter matrix and scores the output with a
64-phase row-edge metric so seam shifts do not look like fixes.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np


SAMPLES = {
    "Z9_HE": r"C:\Users\bcm18\Music\4knewjiangtherapee\samples\nikon-he-star\Z9_HE.NEF",
    "Z8_HE": r"C:\Users\bcm18\Music\4knewjiangtherapee\samples\nikon-he-star\Z8_HE_low.NEF",
    "Z8_HEStar": r"C:\Users\bcm18\Music\4knewjiangtherapee\samples\nikon-he-star\Z8_HE_high.NEF",
    "Z6III_HEStar": r"C:\Users\bcm18\Music\4knewjiangtherapee\samples\nikon-he-star\Z6III_HE_Star_FX.NEF",
}


def hardlink_or_copy(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def read_pgm(path: Path) -> np.ndarray:
    with path.open("rb") as f:
        if f.readline().strip() != b"P5":
            raise ValueError(f"{path}: not PGM P5")
        line = f.readline()
        while line.startswith(b"#"):
            line = f.readline()
        width, height = map(int, line.split())
        maxv = int(f.readline())
        data = np.fromfile(f, dtype=">u2" if maxv > 255 else "u1")
    return data.reshape(height, width).astype(np.float32)


def rolling_median(x: np.ndarray, radius: int = 32) -> np.ndarray:
    padded = np.pad(x, (radius, radius), mode="edge")
    return np.array(
        [np.median(padded[i : i + 2 * radius + 1]) for i in range(x.size)],
        dtype=np.float32,
    )


def raw_phase_score(pgm_path: Path) -> dict[str, Any]:
    raw = read_pgm(pgm_path)
    # Same-color two-raw-row delta; column subsampling keeps sweep CPU-friendly.
    diff = np.abs(raw[2:, ::4] - raw[:-2, ::4])
    p95 = np.percentile(diff, 95, axis=1)
    dev = p95 - rolling_median(p95)
    edge_rows = np.arange(1, raw.shape[0] - 1)
    interior = (edge_rows >= 128) & (edge_rows < raw.shape[0] - 128)

    mod_scores: list[float] = []
    mod_counts_gt500: list[int] = []
    for mod in range(64):
        vals = dev[interior & ((edge_rows % 64) == mod)]
        vals = vals[vals > 0]
        top = np.sort(vals)[-12:] if vals.size else np.array([])
        mod_scores.append(float(top.mean()) if top.size else 0.0)
        mod_counts_gt500.append(int((vals > 500).sum()))

    top_idx = np.flatnonzero(interior)[np.argsort(dev[interior])[-12:]][::-1]
    return {
        "best_mod": int(np.argmax(mod_scores)),
        "best_mod_score": float(max(mod_scores)),
        "bad_mod_count_gt500": int(sum(mod_counts_gt500)),
        "top_mods": [
            {"mod": int(i), "score": float(mod_scores[int(i)])}
            for i in np.argsort(mod_scores)[-8:][::-1]
        ],
        "top_edges": [
            {
                "row": int(edge_rows[i]),
                "mod64": int(edge_rows[i] % 64),
                "dev": float(dev[i]),
            }
            for i in top_idx
        ],
    }


def work_id(sample_name: str, params: dict[str, int | str]) -> str:
    text = json.dumps([sample_name, params], sort_keys=True)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def run_one(task: dict[str, Any]) -> dict[str, Any]:
    exe = Path(task["exe"])
    out_root = Path(task["out_root"])
    sample_name = task["sample_name"]
    sample_path = Path(task["sample_path"])
    params = task["params"]
    run_dir = out_root / "_runs" / work_id(sample_name, params)
    run_dir.mkdir(parents=True, exist_ok=True)
    nef = run_dir / sample_path.name
    hardlink_or_copy(sample_path, nef)
    pgm = run_dir / f"{sample_path.name}.pgm"
    pgm.unlink(missing_ok=True)

    env = os.environ.copy()
    for name in [
        "LIBRAW_NIKON_HE_BOUNDARY_CARRY_STRIPES",
        "LIBRAW_NIKON_HE_OVERFLOW_SAVE_START_STRIPE",
        "LIBRAW_NIKON_HE_LATER_TILE_X1_OFFSET",
        "LIBRAW_NIKON_HE_MEMCPY_START_STRIPE",
        "LIBRAW_NIKON_HE_PASSA0_MODE",
    ]:
        env.pop(name, None)
    for name, value in params.items():
        if value != "":
            env[name] = str(value)

    proc = subprocess.run(
        [str(exe), "-q", str(nef)],
        cwd=str(run_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    result: dict[str, Any] = {
        "sample": sample_name,
        "params": params,
        "returncode": proc.returncode,
        "run_dir": str(run_dir),
    }
    if proc.returncode != 0 or not pgm.exists():
        result["error"] = proc.stderr.decode("utf-8", "ignore")[-500:]
        return result
    result.update(raw_phase_score(pgm))
    return result


def make_matrix(mode: str) -> list[dict[str, int | str]]:
    if mode == "focused":
        out: list[dict[str, int | str]] = []
        for carry in [2, 3, 4]:
            for save in [27, 28, 29, 30]:
                for later in [8, 12, 16]:
                    for mem in [0, 1, 2, 3, 4]:
                        out.append(
                            {
                                "LIBRAW_NIKON_HE_BOUNDARY_CARRY_STRIPES": carry,
                                "LIBRAW_NIKON_HE_OVERFLOW_SAVE_START_STRIPE": save,
                                "LIBRAW_NIKON_HE_LATER_TILE_X1_OFFSET": later,
                                "LIBRAW_NIKON_HE_MEMCPY_START_STRIPE": mem,
                            }
                        )
        out.append({})
        return out
    if mode == "quick":
        return [
            {},
            {"LIBRAW_NIKON_HE_MEMCPY_START_STRIPE": 4},
            {
                "LIBRAW_NIKON_HE_BOUNDARY_CARRY_STRIPES": 3,
                "LIBRAW_NIKON_HE_OVERFLOW_SAVE_START_STRIPE": 29,
                "LIBRAW_NIKON_HE_LATER_TILE_X1_OFFSET": 12,
                "LIBRAW_NIKON_HE_MEMCPY_START_STRIPE": 4,
            },
            {
                "LIBRAW_NIKON_HE_BOUNDARY_CARRY_STRIPES": 6,
                "LIBRAW_NIKON_HE_OVERFLOW_SAVE_START_STRIPE": 28,
                "LIBRAW_NIKON_HE_LATER_TILE_X1_OFFSET": 24,
            },
        ]
    raise ValueError(f"unknown matrix mode {mode}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exe", type=Path, required=True)
    ap.add_argument("--out-root", type=Path, required=True)
    ap.add_argument("--matrix", choices=["quick", "focused"], default="focused")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    ap.add_argument("--samples", nargs="*", default=list(SAMPLES))
    ns = ap.parse_args()

    params_list = make_matrix(ns.matrix)
    tasks = []
    for sample_name in ns.samples:
        sample_path = Path(SAMPLES[sample_name])
        for params in params_list:
            tasks.append(
                {
                    "exe": str(ns.exe),
                    "out_root": str(ns.out_root),
                    "sample_name": sample_name,
                    "sample_path": str(sample_path),
                    "params": params,
                }
            )

    ns.out_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with cf.ThreadPoolExecutor(max_workers=ns.jobs) as pool:
        for result in pool.map(run_one, tasks):
            results.append(result)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        grouped.setdefault(json.dumps(result["params"], sort_keys=True), []).append(result)

    ranked = []
    for params_json, rows in grouped.items():
        ok = [r for r in rows if "best_mod_score" in r]
        if not ok:
            ranked.append({"params": json.loads(params_json), "ok": 0, "mean": None, "max": None})
            continue
        ranked.append(
            {
                "params": json.loads(params_json),
                "ok": len(ok),
                "mean": float(np.mean([r["best_mod_score"] for r in ok])),
                "max": float(np.max([r["best_mod_score"] for r in ok])),
                "bad_gt500": int(sum(r["bad_mod_count_gt500"] for r in ok)),
                "samples": {
                    r["sample"]: {
                        "best_mod_score": r["best_mod_score"],
                        "best_mod": r["best_mod"],
                    }
                    for r in ok
                },
            }
        )
    ranked.sort(key=lambda r: (1 if r["mean"] is None else 0, r["mean"] or 1e30, r["max"] or 1e30))

    out = {"matrix": ns.matrix, "jobs": ns.jobs, "tasks": len(tasks), "results": results, "ranked": ranked}
    (ns.out_root / f"{ns.matrix}_raw_phase_sweep.json").write_text(
        json.dumps(out, indent=2),
        encoding="utf-8",
    )

    print(f"tasks={len(tasks)} jobs={ns.jobs} ok={sum('best_mod_score' in r for r in results)}")
    for row in ranked[:20]:
        print(
            f"mean={row['mean'] if row['mean'] is not None else 'ERR'} "
            f"max={row['max'] if row['max'] is not None else 'ERR'} "
            f"bad={row.get('bad_gt500')} params={row['params']}"
        )


if __name__ == "__main__":
    main()
