#!/usr/bin/env python3
"""Validate strict #824/#826 metric code against reference formulas.

The strict math evaluator keeps SSIM/MS-SSIM/GMSD self-contained so the large
batch does not depend on optional IQA packages.  This gate uses deterministic
test vectors, independent scalar oracles, and the evaluator's alternate
SciPy-free SSIM path to make that choice auditable.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import math
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

import strict_824_826_math_eval as strict


DEFAULT_OUT = Path("out/strict_824_826_metric_validation/metric_reference_validation.json")

REFERENCES = [
    {
        "metric": "PSNR/MSE/MAE/MAX",
        "role": "closed-form sample-domain error identity",
        "source": "The evaluator uses the standard definitions directly; PSNR peak is RAW white-black range.",
        "url": "https://scikit-image.org/docs/stable/api/skimage.metrics.html#skimage.metrics.peak_signal_noise_ratio",
    },
    {
        "metric": "SSIM",
        "role": "primary formula source",
        "source": "Wang, Bovik, Sheikh, Simoncelli, Image quality assessment: From error visibility to structural similarity.",
        "url": "https://ece.uwaterloo.ca/~z70wang/publications/ssim.pdf",
    },
    {
        "metric": "MS-SSIM",
        "role": "primary formula source",
        "source": "Wang, Simoncelli, Bovik, Multi-scale structural similarity for image quality assessment.",
        "url": "https://ece.uwaterloo.ca/~z70wang/publications/msssim.pdf",
    },
    {
        "metric": "GMSD",
        "role": "primary formula source",
        "source": "Xue, Zhang, Mou, Bovik, Gradient magnitude similarity deviation.",
        "url": "https://live.ece.utexas.edu/publications/2014/GMSD.pdf",
    },
    {
        "metric": "BD-rate",
        "role": "rate-distortion integration convention used by compute_bd_rate.py",
        "source": "Bjontegaard, Calculation of average PSNR differences between RD-curves, VCEG-M33.",
        "url": "https://eclass.uoa.gr/modules/document/file.php/D221/%CE%A3%CE%B7%CE%BC%CE%B5%CE%B9%CF%8E%CF%83%CE%B5%CE%B9%CF%82/VCEG-M33%20%28Bjontegaard%20Delta%29.pdf",
    },
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scalar_error_oracle(src: np.ndarray, rec: np.ndarray) -> dict[str, float]:
    diff = rec.astype(np.float64) - src.astype(np.float64)
    mse = float(np.mean(diff * diff))
    mae = float(np.mean(np.abs(diff)))
    max_abs = float(np.max(np.abs(diff))) if diff.size else 0.0
    psnr = float("inf") if mse == 0 else 10.0 * math.log10((strict.RANGE * strict.RANGE) / mse)
    return {"MSE": mse, "MAE": mae, "MAX": max_abs, "PSNR_raw": psnr}


def prewitt_oracle(x: np.ndarray) -> np.ndarray:
    arr = x.astype(np.float64)
    p = np.pad(arr, ((1, 1), (1, 1)), mode="edge")
    gx = (
        p[:-2, 2:]
        + p[1:-1, 2:]
        + p[2:, 2:]
        - p[:-2, :-2]
        - p[1:-1, :-2]
        - p[2:, :-2]
    ) / 3.0
    gy = (
        p[2:, :-2]
        + p[2:, 1:-1]
        + p[2:, 2:]
        - p[:-2, :-2]
        - p[:-2, 1:-1]
        - p[:-2, 2:]
    ) / 3.0
    return np.sqrt(gx * gx + gy * gy)


def gmsd_oracle(src: np.ndarray, rec: np.ndarray) -> float:
    gs = prewitt_oracle(src)
    gr = prewitt_oracle(rec)
    c = 0.0026 * strict.RANGE * strict.RANGE
    gms = (2.0 * gs * gr + c) / (gs * gs + gr * gr + c)
    return float(np.std(gms))


@contextlib.contextmanager
def strict_without_scipy_filter() -> Any:
    old = strict.scipy_ndimage
    strict.scipy_ndimage = None
    try:
        yield
    finally:
        strict.scipy_ndimage = old


def finite_abs_delta(a: float, b: float) -> float:
    if math.isinf(a) and math.isinf(b):
        return 0.0
    return abs(float(a) - float(b))


def add_check(checks: list[dict[str, Any]], name: str, actual: float, expected: float, tolerance: float) -> None:
    delta = finite_abs_delta(actual, expected)
    checks.append(
        {
            "name": name,
            "actual": actual if math.isfinite(actual) else "inf",
            "expected": expected if math.isfinite(expected) else "inf",
            "abs_delta": delta,
            "tolerance": tolerance,
            "passed": delta <= tolerance,
        }
    )


def deterministic_vectors() -> list[tuple[str, np.ndarray, np.ndarray]]:
    rng = np.random.default_rng(20260603)
    y, x = np.mgrid[0:64, 0:64]
    base = strict.BLACK + strict.RANGE * (0.12 + 0.53 * x / 63.0 + 0.21 * y / 63.0)
    wave = 250.0 * np.sin(2 * np.pi * x / 9.0) + 180.0 * np.cos(2 * np.pi * y / 11.0)
    texture = 80.0 * ((x + 2 * y) % 5)
    src = np.clip(base + wave + texture, strict.BLACK, strict.WHITE)
    rec = np.clip(src + rng.normal(0, 23.0, src.shape) + 35.0 * np.sin(2 * np.pi * (x + y) / 17.0), strict.BLACK, strict.WHITE)
    return [
        ("identical_constant", np.full((64, 64), 8192.0), np.full((64, 64), 8192.0)),
        ("constant_offset", np.full((64, 64), 4096.0), np.full((64, 64), 4112.0)),
        ("gradient_noise_texture", src.astype(np.float64), rec.astype(np.float64)),
    ]


def optional_skimage_checks(src: np.ndarray, rec: np.ndarray) -> dict[str, Any]:
    if importlib.util.find_spec("skimage") is None:
        return {"available": False, "reason": "skimage is not installed in this workspace Python environment"}
    try:
        from skimage.metrics import peak_signal_noise_ratio, structural_similarity

        psnr = float(peak_signal_noise_ratio(src, rec, data_range=strict.RANGE))
        ssim = float(
            structural_similarity(
                src,
                rec,
                data_range=strict.RANGE,
                gaussian_weights=True,
                sigma=1.5,
                use_sample_covariance=False,
            )
        )
        return {"available": True, "psnr": psnr, "ssim": ssim}
    except Exception as exc:  # pragma: no cover - depends on optional package
        return {"available": False, "reason": f"skimage import or call failed: {exc}"}


def validate_case(case_id: str, src: np.ndarray, rec: np.ndarray) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    strict_scalar = strict.metric_summary(src, rec)
    oracle_scalar = scalar_error_oracle(src, rec)
    for metric, expected in oracle_scalar.items():
        add_check(checks, f"scalar_formula.{metric}", strict_scalar[metric], expected, 1e-12)

    gmsd_expected = gmsd_oracle(src, rec)
    add_check(checks, "gmsd_formula.gmsd_mean", strict.gmsd_index(src, rec), gmsd_expected, 1e-12)

    strict_ssim = strict.ssim_index(src, rec)
    strict_ms_ssim = strict.ms_ssim_index(src, rec)
    with strict_without_scipy_filter():
        fallback_ssim = strict.ssim_index(src, rec)
        fallback_ms_ssim = strict.ms_ssim_index(src, rec)
    add_check(checks, "ssim_scipy_vs_builtin_fallback", strict_ssim, fallback_ssim, 2e-4)
    add_check(checks, "ms_ssim_scipy_vs_builtin_fallback", strict_ms_ssim, fallback_ms_ssim, 2e-4)

    if case_id == "identical_constant":
        add_check(checks, "identity.ssim_is_one", strict_ssim, 1.0, 1e-12)
        add_check(checks, "identity.ms_ssim_is_one", strict_ms_ssim, 1.0, 1e-12)
        add_check(checks, "identity.gmsd_is_zero", strict.gmsd_index(src, rec), 0.0, 1e-12)

    skimage = optional_skimage_checks(src, rec)
    if skimage.get("available"):
        add_check(checks, "skimage.psnr", strict_scalar["PSNR_raw"], float(skimage["psnr"]), 1e-9)
        add_check(checks, "skimage.ssim", strict_ssim, float(skimage["ssim"]), 5e-4)

    return {
        "case_id": case_id,
        "shape": list(src.shape),
        "checks": checks,
        "optional_reference_library": {"skimage": skimage},
        "passed": all(bool(c["passed"]) for c in checks),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ns = ap.parse_args()

    cases = [validate_case(case_id, src, rec) for case_id, src, rec in deterministic_vectors()]
    all_checks = [check for case in cases for check in case["checks"]]
    max_abs_delta = max((float(c["abs_delta"]) for c in all_checks), default=0.0)
    errors = [f"{case['case_id']}:{check['name']}" for case in cases for check in case["checks"] if not check["passed"]]
    strict_path = Path(strict.__file__).resolve()
    result = {
        "kind": "strict #824/#826 metric reference validation",
        "generated_unix": int(time.time()),
        "strict_metric_module": str(strict_path),
        "strict_metric_module_sha256": sha256_file(strict_path),
        "metric_peak_definition": {
            "black": strict.BLACK,
            "white": strict.WHITE,
            "range": strict.RANGE,
        },
        "references": REFERENCES,
        "validation_methods": [
            "closed-form scalar PSNR/MSE/MAE/MAX oracle",
            "independent Prewitt/GMSD formula oracle",
            "SSIM/MS-SSIM SciPy convolution path cross-checked against built-in no-SciPy fallback",
            "optional skimage PSNR/SSIM comparison when installed",
        ],
        "cases": cases,
        "summary": {
            "case_count": len(cases),
            "check_count": len(all_checks),
            "max_abs_delta": max_abs_delta,
            "all_passed": not errors,
        },
        "errors": errors,
    }
    ns.out.parent.mkdir(parents=True, exist_ok=True)
    ns.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
