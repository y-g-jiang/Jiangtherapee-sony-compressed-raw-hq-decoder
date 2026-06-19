#!/usr/bin/env python3
"""Virtual same-source four-plane RGGB codec benchmark.

This is an L2 proxy experiment, not a Nikon or Sony production encoder. It
compares two mathematical codec families on the same four-plane RGGB input:

* nikon_he_like_53_step2_gcli_proxy: Nikon #826-inspired step2 Bayer
  reconstruction reverse path, Nikon IQX/IQP tone-LUT code domain,
  reversible CDF 5/3 wavelet, and entropy-estimated bit-plane-like
  quantization.
* sony_like_green_residual_wavelet: LLVC3 code-domain green backbone plus R/B
  residual model, Sony 4096-entry LUT domain, CDF 5/3 lowpass/residual
  synthesis, and row-differenced final-green phase detail.

The output metrics are suitable for same-source, same-target-bpp mathematical
comparison. They are not evidence about the private camera encoders unless a
future implementation proves these proxies match those encoders.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

try:
    from scipy import ndimage as scipy_ndimage
except Exception:  # pragma: no cover - optional speed path
    scipy_ndimage = None


BLACK = 512.0
WHITE = 16383.0
RANGE = WHITE - BLACK
NIKON_CODEC_NAME = "nikon_he_like_53_step2_gcli_proxy"
SONY_CODEC_NAME = "sony_like_green_residual_wavelet"
SONY_CODE_BIAS = 2048.0
NIKON_CODE_BIAS = float(1 << (2 + 14 - 1))
SONY_LUT_SIZE = 4096
NIKON_LUT_SIZE = 81792
TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_SONY_LUT = TOOLS_DIR / "data" / "sony_llvc3_static_lut4096.tsv"
DEFAULT_NIKON_LUT = TOOLS_DIR / "data" / "nikon_he_iqx_iqp_lut81792_sample14_u16.bin"
_LUT_CACHE: dict[tuple[str, int], np.ndarray] = {}


@dataclass(frozen=True)
class ProxyCodec:
    name: str
    forward: Callable[[dict[str, np.ndarray]], dict[str, np.ndarray]]
    inverse: Callable[[dict[str, np.ndarray]], dict[str, np.ndarray]]
    component_weights: dict[str, float]
    transform: str = "haar"
    component_transforms: dict[str, str] | None = None
    roundtrip_projection_tolerance: float = 0.0


@dataclass
class EncodeResult:
    codec: str
    source_id: str
    target_bpp: float
    actual_bpp: float
    base_step: float
    encode_ms: float
    recon: dict[str, np.ndarray]


@dataclass(frozen=True)
class RoundtripAudit:
    codec: str
    source_id: str
    max_abs_error: float
    mae: float
    mse: float
    psnr_raw: float


def clip_raw(x: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(x), BLACK, WHITE).astype(np.float64)


def load_code_lut(path: Path, size: int, value_column: int = 1) -> np.ndarray:
    key = (str(path), size)
    cached = _LUT_CACHE.get(key)
    if cached is not None:
        return cached
    if path.exists() and path.suffix.lower() in {".bin", ".raw", ".lut"}:
        data = np.fromfile(path, dtype="<u2")
        if data.size >= size:
            lut = data[:size].astype(np.float64)
            _LUT_CACHE[key] = lut
            return lut
    rows: list[float] = []
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.replace(",", "\t").split()
                if not parts or parts[0].lower() in {"code", "input", "src"}:
                    continue
                col = min(value_column, len(parts) - 1)
                if col >= 0:
                    try:
                        rows.append(float(parts[col]))
                    except ValueError:
                        continue
    if len(rows) < size:
        raise ValueError(f"could not load {size} LUT entries from {path}")
    lut = np.asarray(rows[:size], dtype=np.float64)
    _LUT_CACHE[key] = lut
    return lut


def inverse_lut_nearest(samples: np.ndarray, lut: np.ndarray) -> np.ndarray:
    table = np.asarray(lut, dtype=np.float64)
    x = np.asarray(samples, dtype=np.float64)
    idx = np.searchsorted(table, x, side="left")
    idx = np.clip(idx, 0, table.size - 1)
    prev = np.clip(idx - 1, 0, table.size - 1)
    choose_prev = np.abs(table[prev] - x) <= np.abs(table[idx] - x)
    return np.where(choose_prev, prev, idx).astype(np.float64)


def sony_sample_to_signed_code(samples: np.ndarray) -> np.ndarray:
    lut = load_code_lut(DEFAULT_SONY_LUT, SONY_LUT_SIZE, value_column=1)
    code = inverse_lut_nearest(samples, lut)
    return np.clip(code, 0, SONY_LUT_SIZE - 1) - SONY_CODE_BIAS


def sony_signed_code_to_sample(code: np.ndarray) -> np.ndarray:
    lut = load_code_lut(DEFAULT_SONY_LUT, SONY_LUT_SIZE, value_column=1)
    idx = np.clip(np.rint(np.asarray(code, dtype=np.float64) + SONY_CODE_BIAS), 0, SONY_LUT_SIZE - 1).astype(np.int64)
    return lut[idx].astype(np.float64)


def nikon_sample_to_decoder_code(samples: np.ndarray) -> np.ndarray:
    lut = load_code_lut(DEFAULT_NIKON_LUT, NIKON_LUT_SIZE, value_column=1)
    code = inverse_lut_nearest(samples, lut)
    return np.clip(code, 0, NIKON_LUT_SIZE - 1) - NIKON_CODE_BIAS


def nikon_decoder_code_to_sample(code: np.ndarray) -> np.ndarray:
    lut = load_code_lut(DEFAULT_NIKON_LUT, NIKON_LUT_SIZE, value_column=1)
    idx = np.clip(np.rint(np.asarray(code, dtype=np.float64) + NIKON_CODE_BIAS), 0, NIKON_LUT_SIZE - 1).astype(np.int64)
    return lut[idx].astype(np.float64)


def generate_scene(scene: str, plane_h: int, plane_w: int, rng: np.random.Generator) -> dict[str, np.ndarray]:
    y, x = np.mgrid[0:plane_h, 0:plane_w]
    xf = x / max(1, plane_w - 1)
    yf = y / max(1, plane_h - 1)
    base = BLACK + RANGE * (0.12 + 0.55 * xf + 0.18 * yf)

    if scene == "smooth_gradient":
        lum = base + 180.0 * np.sin(2 * np.pi * xf)
        chroma_r = 180.0 * np.sin(2 * np.pi * yf)
        chroma_b = -140.0 * np.cos(2 * np.pi * xf)
        noise = rng.normal(0, 3, size=base.shape)
    elif scene == "fine_texture":
        texture = 900.0 * np.sin(2 * np.pi * 17 * xf) * np.sin(2 * np.pi * 11 * yf)
        texture += 260.0 * ((x + y) % 2) - 130.0
        lum = base + texture
        chroma_r = 380.0 * np.sin(2 * np.pi * 7 * xf)
        chroma_b = -350.0 * np.sin(2 * np.pi * 9 * yf)
        noise = rng.normal(0, 12, size=base.shape)
    elif scene == "color_edges":
        stripes = ((x // 16) % 3).astype(np.float64)
        lum = BLACK + RANGE * (0.25 + 0.40 * yf)
        chroma_r = np.where(stripes == 0, 2100.0, np.where(stripes == 1, -900.0, 200.0))
        chroma_b = np.where(stripes == 2, 2100.0, np.where(stripes == 1, -800.0, 100.0))
        noise = rng.normal(0, 6, size=base.shape)
    elif scene == "highlight_rolloff":
        cx, cy = 0.62, 0.36
        radius = (xf - cx) ** 2 + (yf - cy) ** 2
        spot = RANGE * 0.72 * np.exp(-radius / 0.012)
        lum = base + spot
        chroma_r = 260.0 + 850.0 * np.exp(-radius / 0.02)
        chroma_b = -160.0 + 260.0 * np.exp(-radius / 0.05)
        noise = rng.normal(0, 5, size=base.shape)
    elif scene == "shadow_noise":
        lum = BLACK + RANGE * (0.035 + 0.075 * xf + 0.045 * yf)
        chroma_r = 80.0 * np.sin(2 * np.pi * 5 * xf)
        chroma_b = -90.0 * np.cos(2 * np.pi * 4 * yf)
        noise = rng.normal(0, 38, size=base.shape)
    elif scene == "green_phase_alias":
        diagonal = ((x + 2 * y) % 13 < 6).astype(np.float64)
        lum = base + 560.0 * diagonal
        chroma_r = 180.0 * np.sin(2 * np.pi * 3 * xf)
        chroma_b = -180.0 * np.cos(2 * np.pi * 3 * yf)
        noise = rng.normal(0, 8, size=base.shape)
    elif scene == "decorrelated_color":
        lum = base + 240.0 * np.sin(2 * np.pi * 5 * (xf + yf))
        chroma_r = 1600.0 * np.sin(2 * np.pi * 4 * xf) + 700.0 * np.sign(np.sin(2 * np.pi * 3 * yf))
        chroma_b = 1500.0 * np.cos(2 * np.pi * 5 * yf) - 600.0 * np.sign(np.sin(2 * np.pi * 2 * xf))
        noise = rng.normal(0, 10, size=base.shape)
    elif scene == "slanted_edge":
        edge = (0.68 * xf + 0.43 * yf) > 0.58
        lum = BLACK + RANGE * (0.18 + 0.46 * edge.astype(np.float64))
        chroma_r = 250.0 * edge.astype(np.float64)
        chroma_b = -180.0 * edge.astype(np.float64)
        noise = rng.normal(0, 4, size=base.shape)
    elif scene == "thin_black_lines":
        lines = (((x + 2 * y) % 19) == 0) | (((2 * x + y) % 23) == 0)
        lum = base - 1800.0 * lines.astype(np.float64)
        chroma_r = 320.0 * np.sin(2 * np.pi * 2 * yf)
        chroma_b = -280.0 * np.cos(2 * np.pi * 2 * xf)
        noise = rng.normal(0, 6, size=base.shape)
    elif scene == "zone_plate":
        rr = (xf - 0.5) ** 2 + (yf - 0.5) ** 2
        wave = np.sin(2 * np.pi * (8 * rr + 46 * rr * rr))
        lum = base + 950.0 * wave
        chroma_r = 180.0 * np.sin(2 * np.pi * 3 * xf)
        chroma_b = 180.0 * np.cos(2 * np.pi * 4 * yf)
        noise = rng.normal(0, 5, size=base.shape)
    elif scene == "nyquist_checker":
        checker = ((x + y) & 1).astype(np.float64) * 2.0 - 1.0
        lum = base + 620.0 * checker
        chroma_r = 260.0 * ((x & 1).astype(np.float64) * 2.0 - 1.0)
        chroma_b = -260.0 * ((y & 1).astype(np.float64) * 2.0 - 1.0)
        noise = rng.normal(0, 3, size=base.shape)
    elif scene == "micro_contrast":
        lum = base
        for freq, amp in [(5, 180.0), (13, 90.0), (29, 45.0), (47, 24.0)]:
            lum = lum + amp * np.sin(2 * np.pi * freq * (xf + 0.37 * yf))
        chroma_r = 160.0 * np.sin(2 * np.pi * 11 * xf)
        chroma_b = -140.0 * np.cos(2 * np.pi * 9 * yf)
        noise = rng.normal(0, 4, size=base.shape)
    elif scene == "random_foliage":
        field = rng.normal(0, 1, size=base.shape)
        field = (field + np.roll(field, 1, 0) + np.roll(field, -1, 0) + np.roll(field, 1, 1) + np.roll(field, -1, 1)) / 5.0
        lum = base + 820.0 * field + 220.0 * np.sin(2 * np.pi * 19 * xf)
        chroma_r = 420.0 * np.roll(field, 3, 1)
        chroma_b = -390.0 * np.roll(field, -2, 0)
        noise = rng.normal(0, 16, size=base.shape)
    elif scene == "color_checker":
        grid_x = np.clip((x // max(1, plane_w // 8)), 0, 7)
        grid_y = np.clip((y // max(1, plane_h // 6)), 0, 5)
        patch = ((grid_x + 2 * grid_y) % 6).astype(np.float64)
        lum = BLACK + RANGE * (0.18 + 0.095 * patch)
        chroma_r = np.choose(patch.astype(int), [-900, -300, 200, 700, 1200, 300])
        chroma_b = np.choose(patch.astype(int), [800, 200, -500, -900, 100, 1200])
        noise = rng.normal(0, 5, size=base.shape)
    elif scene == "specular_grid":
        grid = ((x % 48) < 3) | ((y % 48) < 3)
        spot = np.exp(-((xf - 0.72) ** 2 + (yf - 0.28) ** 2) / 0.006)
        lum = base + 1250.0 * grid.astype(np.float64) + RANGE * 0.7 * spot
        chroma_r = 520.0 * spot
        chroma_b = -280.0 * spot
        noise = rng.normal(0, 4, size=base.shape)
    elif scene == "shadow_fabric":
        weave = 0.5 * np.sin(2 * np.pi * 31 * xf) + 0.5 * np.sin(2 * np.pi * 27 * yf)
        lum = BLACK + RANGE * (0.045 + 0.03 * xf + 0.02 * yf) + 120.0 * weave
        chroma_r = 70.0 * weave
        chroma_b = -60.0 * np.roll(weave, 1, 1)
        noise = rng.normal(0, 42, size=base.shape)
    elif scene == "chroma_noise":
        lum = base + rng.normal(0, 20, size=base.shape)
        chroma_r = rng.normal(0, 720, size=base.shape)
        chroma_b = rng.normal(0, 680, size=base.shape)
        noise = rng.normal(0, 12, size=base.shape)
    elif scene == "bayer_phase_steps":
        steps = np.floor(xf * 12) / 12.0
        lum = BLACK + RANGE * (0.15 + 0.65 * steps)
        chroma_r = 420.0 * ((y // 8) % 2)
        chroma_b = -380.0 * ((x // 8) % 2)
        green_phase = 220.0 * (((x + y) // 4) % 2) - 110.0
        noise = rng.normal(0, 5, size=base.shape)
        r = lum + chroma_r + noise
        g0 = lum + green_phase + noise * 0.5
        g1 = lum - green_phase + rng.normal(0, 5, size=base.shape)
        b = lum + chroma_b + noise
        return {"R": clip_raw(r), "G0": clip_raw(g0), "G1": clip_raw(g1), "B": clip_raw(b)}
    elif scene == "tile_boundary_stress":
        boundary = ((x % 64) < 2) | ((y % 64) < 2)
        diagonal = ((x + y) % 31) < 2
        lum = base + 780.0 * boundary.astype(np.float64) - 520.0 * diagonal.astype(np.float64)
        chroma_r = 450.0 * diagonal.astype(np.float64)
        chroma_b = -430.0 * boundary.astype(np.float64)
        noise = rng.normal(0, 7, size=base.shape)
    elif scene == "skin_like_smooth":
        lum = BLACK + RANGE * (0.32 + 0.12 * xf + 0.06 * yf)
        lum += 38.0 * np.sin(2 * np.pi * 4 * xf) + 22.0 * np.sin(2 * np.pi * 6 * yf)
        chroma_r = 520.0 + 60.0 * np.sin(2 * np.pi * 3 * yf)
        chroma_b = -380.0 + 45.0 * np.cos(2 * np.pi * 5 * xf)
        noise = rng.normal(0, 3, size=base.shape)
    elif scene == "low_contrast_detail":
        lum = BLACK + RANGE * (0.42 + 0.08 * xf)
        lum += 70.0 * np.sin(2 * np.pi * 23 * xf) * np.sin(2 * np.pi * 17 * yf)
        chroma_r = 45.0 * np.sin(2 * np.pi * 9 * xf)
        chroma_b = -55.0 * np.cos(2 * np.pi * 7 * yf)
        noise = rng.normal(0, 5, size=base.shape)
    elif scene == "high_iso_texture":
        texture = 500.0 * np.sin(2 * np.pi * 15 * xf) * np.cos(2 * np.pi * 21 * yf)
        lum = base + texture
        chroma_r = 240.0 * np.sin(2 * np.pi * 4 * xf)
        chroma_b = -260.0 * np.cos(2 * np.pi * 4 * yf)
        noise = rng.normal(0, 72, size=base.shape)
    elif scene == "red_blue_fine_text":
        strokes = (((x // 5) % 4) == 0) & (((y // 13) % 2) == 0)
        lum = base - 420.0 * strokes.astype(np.float64)
        chroma_r = 1350.0 * strokes.astype(np.float64) - 120.0
        chroma_b = -1250.0 * strokes.astype(np.float64) + 90.0
        noise = rng.normal(0, 5, size=base.shape)
    elif scene == "blue_channel_detail":
        lum = base + 110.0 * np.sin(2 * np.pi * 6 * xf)
        chroma_r = 100.0 * np.sin(2 * np.pi * 3 * yf)
        chroma_b = 900.0 * np.sin(2 * np.pi * 25 * (xf + 0.2 * yf))
        noise = rng.normal(0, 7, size=base.shape)
    else:
        raise ValueError(f"unknown synthetic scene {scene!r}")

    green_phase = 38.0 * np.sin(2 * np.pi * (xf * 3.0 + yf * 2.0))
    r = lum + chroma_r + noise
    g0 = lum + green_phase + noise * 0.6
    g1 = lum - green_phase + rng.normal(0, np.std(noise) if np.std(noise) else 1.0, size=base.shape) * 0.6
    b = lum + chroma_b + noise
    return {"R": clip_raw(r), "G0": clip_raw(g0), "G1": clip_raw(g1), "B": clip_raw(b)}


def load_rggb_raw(path: Path, width: int, height: int) -> dict[str, np.ndarray]:
    if width <= 0 or height <= 0 or width % 2 or height % 2:
        raise ValueError("width and height must be positive even numbers")
    raw = np.fromfile(path, dtype="<u2")
    expected = width * height
    if raw.size != expected:
        raise ValueError(f"{path} has {raw.size} uint16 samples, expected {expected}")
    mosaic = raw.reshape(height, width).astype(np.float64)
    return {
        "R": mosaic[0::2, 0::2],
        "G0": mosaic[0::2, 1::2],
        "G1": mosaic[1::2, 0::2],
        "B": mosaic[1::2, 1::2],
    }


def center_planes(planes: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {k: v.astype(np.float64) - BLACK for k, v in planes.items()}


def uncenter_planes(planes: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {k: clip_raw(v + BLACK) for k, v in planes.items()}


def _left_edge(x: np.ndarray) -> np.ndarray:
    out = np.empty_like(x, dtype=np.float64)
    out[:, 0] = x[:, 0]
    out[:, 1:] = x[:, :-1]
    return out


def _right_edge(x: np.ndarray) -> np.ndarray:
    out = np.empty_like(x, dtype=np.float64)
    out[:, -1] = x[:, -1]
    out[:, :-1] = x[:, 1:]
    return out


def _prev_edge(x: np.ndarray) -> np.ndarray:
    out = np.empty_like(x, dtype=np.float64)
    out[0, :] = x[0, :]
    out[1:, :] = x[:-1, :]
    return out


def _next_edge(x: np.ndarray) -> np.ndarray:
    out = np.empty_like(x, dtype=np.float64)
    out[-1, :] = x[-1, :]
    out[:-1, :] = x[1:, :]
    return out


def _shift2(x: np.ndarray) -> np.ndarray:
    return np.floor(np.asarray(x, dtype=np.float64) / 4.0)


def nikon_step2_predictors(g0: np.ndarray, g1: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Reverse the decoder-visible #826 step2 Bayer predictor in code domain.

    The #826 decoder's final stage writes top-row R/G and bottom-row G/B by
    adding a 32768 midpoint bias and indexing the IQX/IQP tone LUT.  In the
    inverse encoder view, p2 is the top-row green code, p3 is the bottom-row
    green code, while p1/p4 carry red/blue detail around the same local
    predictors used by `step2_bayer_rows()`.
    """

    prev_g1 = _prev_edge(g1)
    next_g0 = _next_edge(g0)
    left_g0 = _left_edge(g0)
    right_g1 = _right_edge(g1)
    pred_r = _shift2(left_g0 + prev_g1 + g1 + g0)
    pred_r[:, 0] = _shift2(prev_g1[:, 0] + 2.0 * g0[:, 0] + g1[:, 0])
    pred_b = _shift2(right_g1 + g1 + g0 + next_g0)
    return pred_r, pred_b


def nikon_step1_offsets(p1: np.ndarray, p4: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rows, cols = p1.shape
    prev_p4 = _prev_edge(p4)
    next_p1 = _next_edge(p1)
    right_p1 = _right_edge(p1)
    left_p4 = _left_edge(p4)
    ll_hl = p1 + p4
    l_offset = _shift2(0.5 * (ll_hl + right_p1 + prev_p4))
    h_offset = _shift2(0.5 * (ll_hl + left_p4 + next_p1))
    if cols:
        h_offset[:, 0] = _shift2(0.5 * (ll_hl[:, 0] + p4[:, 0] + next_p1[:, 0]))
    return l_offset, h_offset


def nikon_step1_merge(
    p1: np.ndarray,
    p2_lh: np.ndarray,
    p3_hh: np.ndarray,
    p4: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Decoder-visible #826 step1 merge from four tile-coeff planes to L/H."""

    prev_p3 = _prev_edge(p3_hh)
    next_p2 = _next_edge(p2_lh)
    next_p3 = _next_edge(p3_hh)
    right_p3 = _right_edge(p3_hh)
    l_offset, h_offset = nikon_step1_offsets(p1, p4)
    cur_predict = p2_lh - _shift2(0.5 * (p3_hh + right_p3 + prev_p3 + _right_edge(prev_p3)))
    l_plane = cur_predict - l_offset
    next_predict = next_p2 - _shift2(0.5 * (p3_hh + right_p3 + next_p3 + _right_edge(next_p3)))
    cur_prev = _left_edge(cur_predict)
    next_prev = _left_edge(next_predict)
    blend = _shift2(cur_prev + cur_predict + next_predict + next_prev)
    if p1.shape[1]:
        blend[:, 0] = 0.5 * (cur_predict[:, 0] + next_predict[:, 0])
    h_plane = p3_hh - h_offset + blend
    return l_plane, h_plane


def nikon_step1_reverse(
    p1: np.ndarray,
    l_plane: np.ndarray,
    h_plane: np.ndarray,
    p4: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Reverse #826 step1 so the proxy codes the pre-step1 LL/LH/HH/HL planes."""

    l_offset, h_offset = nikon_step1_offsets(p1, p4)
    cur_predict = l_plane + l_offset
    next_predict = _next_edge(cur_predict)
    cur_prev = _left_edge(cur_predict)
    next_prev = _left_edge(next_predict)
    blend = _shift2(cur_prev + cur_predict + next_predict + next_prev)
    if p1.shape[1]:
        blend[:, 0] = 0.5 * (cur_predict[:, 0] + next_predict[:, 0])
    p3_hh = h_plane + h_offset - blend
    p2_lh = cur_predict.copy()
    for _ in range(6):
        prev_p3 = _prev_edge(p3_hh)
        right_p3 = _right_edge(p3_hh)
        p2_lh = cur_predict + _shift2(0.5 * (p3_hh + right_p3 + prev_p3 + _right_edge(prev_p3)))
        _l_check, h_check = nikon_step1_merge(p1, p2_lh, p3_hh, p4)
        err = h_check - h_plane
        if float(np.max(np.abs(err))) < 1e-9:
            break
        p3_hh = p3_hh - err
    return p2_lh, p3_hh


def nikon_forward(planes: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    p = {k: nikon_sample_to_decoder_code(v) for k, v in planes.items()}
    r, g0, g1, b = p["R"], p["G0"], p["G1"], p["B"]
    pred_r, pred_b = nikon_step2_predictors(g0, g1)
    p1 = r - pred_r
    p4 = b - pred_b
    p2_lh, p3_hh = nikon_step1_reverse(p1, g0, g1, p4)
    return {
        "p1_LL_step1": p1,
        "p2_LH_step1": p2_lh,
        "p3_HH_step1": p3_hh,
        "p4_HL_step1": p4,
    }


def nikon_inverse(comps: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    p1 = comps["p1_LL_step1"]
    p2_lh = comps["p2_LH_step1"]
    p3_hh = comps["p3_HH_step1"]
    p4 = comps["p4_HL_step1"]
    g0, g1 = nikon_step1_merge(p1, p2_lh, p3_hh, p4)
    pred_r, pred_b = nikon_step2_predictors(g0, g1)
    centered = {
        "R": p1 + pred_r,
        "G0": g0,
        "G1": g1,
        "B": p4 + pred_b,
    }
    return {k: nikon_decoder_code_to_sample(v) for k, v in centered.items()}


def sony_forward(planes: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    p = {k: sony_sample_to_signed_code(v) for k, v in planes.items()}
    r, g0, g1, b = p["R"], p["G0"], p["G1"], p["B"]
    gavg = 0.5 * (g0 + g1)
    return {
        "Gbase": gavg,
        "Gphase_final": g0 - g1,
        "Rres2": 0.5 * (r - gavg),
        "Bres2": 0.5 * (b - gavg),
    }


def sony_inverse(comps: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    gavg = comps["Gbase"]
    gdiff = comps["Gphase_final"]
    code = {
        "R": gavg + 2.0 * comps["Rres2"],
        "G0": gavg + 0.5 * gdiff,
        "G1": gavg - 0.5 * gdiff,
        "B": gavg + 2.0 * comps["Bres2"],
    }
    return {k: sony_signed_code_to_sample(v) for k, v in code.items()}


CODECS = {
    NIKON_CODEC_NAME: ProxyCodec(
        name=NIKON_CODEC_NAME,
        forward=nikon_forward,
        inverse=nikon_inverse,
        component_weights={
            "p1_LL_step1": 1.4,
            "p2_LH_step1": 1.0,
            "p3_HH_step1": 0.8,
            "p4_HL_step1": 1.0,
        },
        transform="cdf53",
        roundtrip_projection_tolerance=1.0,
    ),
    SONY_CODEC_NAME: ProxyCodec(
        name=SONY_CODEC_NAME,
        forward=sony_forward,
        inverse=sony_inverse,
        component_weights={"Gbase": 4.0, "Gphase_final": 1.25, "Rres2": 1.0, "Bres2": 1.0},
        transform="cdf53",
        component_transforms={"Gphase_final": "rowdiff"},
        roundtrip_projection_tolerance=12.0,
    ),
}

DEFAULT_SCENES = [
    "smooth_gradient",
    "fine_texture",
    "color_edges",
    "highlight_rolloff",
    "shadow_noise",
    "green_phase_alias",
    "decorrelated_color",
    "slanted_edge",
    "thin_black_lines",
    "zone_plate",
    "nyquist_checker",
    "micro_contrast",
    "random_foliage",
    "color_checker",
    "specular_grid",
    "shadow_fabric",
    "chroma_noise",
    "bayer_phase_steps",
    "tile_boundary_stress",
    "skin_like_smooth",
    "low_contrast_detail",
    "high_iso_texture",
    "red_blue_fine_text",
    "blue_channel_detail",
]


def haar_forward2d(x: np.ndarray, levels: int) -> tuple[np.ndarray, list[tuple[int, int]]]:
    coeff = x.astype(np.float64).copy()
    sizes: list[tuple[int, int]] = []
    h, w = coeff.shape
    root2 = math.sqrt(2.0)
    for _ in range(levels):
        if h % 2 or w % 2 or h < 2 or w < 2:
            break
        block = coeff[:h, :w]
        lo_x = (block[:, 0::2] + block[:, 1::2]) / root2
        hi_x = (block[:, 0::2] - block[:, 1::2]) / root2
        ll = (lo_x[0::2, :] + lo_x[1::2, :]) / root2
        lh = (lo_x[0::2, :] - lo_x[1::2, :]) / root2
        hl = (hi_x[0::2, :] + hi_x[1::2, :]) / root2
        hh = (hi_x[0::2, :] - hi_x[1::2, :]) / root2
        coeff[: h // 2, : w // 2] = ll
        coeff[: h // 2, w // 2 : w] = hl
        coeff[h // 2 : h, : w // 2] = lh
        coeff[h // 2 : h, w // 2 : w] = hh
        sizes.append((h, w))
        h //= 2
        w //= 2
    return coeff, sizes


def haar_inverse2d(coeff: np.ndarray, sizes: list[tuple[int, int]]) -> np.ndarray:
    out = coeff.astype(np.float64).copy()
    root2 = math.sqrt(2.0)
    for h, w in reversed(sizes):
        ll = out[: h // 2, : w // 2].copy()
        hl = out[: h // 2, w // 2 : w].copy()
        lh = out[h // 2 : h, : w // 2].copy()
        hh = out[h // 2 : h, w // 2 : w].copy()
        lo0 = (ll + lh) / root2
        lo1 = (ll - lh) / root2
        hi0 = (hl + hh) / root2
        hi1 = (hl - hh) / root2
        recon = np.empty((h, w), dtype=np.float64)
        recon[0::2, 0::2] = (lo0 + hi0) / root2
        recon[0::2, 1::2] = (lo0 - hi0) / root2
        recon[1::2, 0::2] = (lo1 + hi1) / root2
        recon[1::2, 1::2] = (lo1 - hi1) / root2
        out[:h, :w] = recon
    return out


def rowdiff_forward2d(x: np.ndarray) -> tuple[np.ndarray, list[tuple[int, int]]]:
    coeff = x.astype(np.float64).copy()
    if coeff.shape[1] > 1:
        coeff[:, 1:] = x[:, 1:] - x[:, :-1]
    return coeff, []


def rowdiff_inverse2d(coeff: np.ndarray) -> np.ndarray:
    out = coeff.astype(np.float64).copy()
    if out.shape[1] > 1:
        out = np.cumsum(out, axis=1)
    return out


def cdf53_forward1d(x: np.ndarray) -> np.ndarray:
    n = int(x.size)
    if n < 2:
        return x.astype(np.float64).copy()
    even = x[0::2].astype(np.float64).copy()
    odd = x[1::2].astype(np.float64).copy()
    n_l = int(even.size)
    n_h = int(odd.size)
    if n_h:
        right_even = even[1 : n_h + 1] if n_l > 1 else even[:1]
        if right_even.size < n_h:
            right_even = np.concatenate([right_even, even[-1:]])
        detail = odd - 0.5 * (even[:n_h] + right_even[:n_h])
        left_detail = np.empty_like(even)
        right_detail = np.empty_like(even)
        left_detail[0] = detail[0]
        if n_l > 1:
            left_detail[1:] = detail[: n_l - 1]
        right_detail[:n_h] = detail
        if n_l > n_h:
            right_detail[-1] = detail[-1]
        approx = even + 0.25 * (left_detail + right_detail)
        return np.concatenate([approx, detail])
    return even


def cdf53_inverse1d(packed: np.ndarray, n: int) -> np.ndarray:
    if n < 2:
        return packed[:n].astype(np.float64).copy()
    n_l = (n + 1) // 2
    n_h = n // 2
    approx = packed[:n_l].astype(np.float64).copy()
    detail = packed[n_l : n_l + n_h].astype(np.float64).copy()
    if n_h:
        left_detail = np.empty_like(approx)
        right_detail = np.empty_like(approx)
        left_detail[0] = detail[0]
        if n_l > 1:
            left_detail[1:] = detail[: n_l - 1]
        right_detail[:n_h] = detail
        if n_l > n_h:
            right_detail[-1] = detail[-1]
        even = approx - 0.25 * (left_detail + right_detail)
        right_even = even[1 : n_h + 1] if n_l > 1 else even[:1]
        if right_even.size < n_h:
            right_even = np.concatenate([right_even, even[-1:]])
        odd = detail + 0.5 * (even[:n_h] + right_even[:n_h])
    else:
        even = approx
        odd = np.empty(0, dtype=np.float64)
    out = np.empty(n, dtype=np.float64)
    out[0::2] = even
    out[1::2] = odd
    return out


def cdf53_forward2d(x: np.ndarray, levels: int) -> tuple[np.ndarray, list[tuple[int, int]]]:
    coeff = x.astype(np.float64).copy()
    sizes: list[tuple[int, int]] = []
    h, w = coeff.shape
    for _ in range(levels):
        if h < 2 or w < 2:
            break
        block = coeff[:h, :w].copy()
        row_pass = np.empty_like(block)
        for r in range(h):
            row_pass[r, :] = cdf53_forward1d(block[r, :])
        col_pass = np.empty_like(row_pass)
        for c in range(w):
            col_pass[:, c] = cdf53_forward1d(row_pass[:, c])
        coeff[:h, :w] = col_pass
        sizes.append((h, w))
        h = (h + 1) // 2
        w = (w + 1) // 2
    return coeff, sizes


def cdf53_inverse2d(coeff: np.ndarray, sizes: list[tuple[int, int]]) -> np.ndarray:
    out = coeff.astype(np.float64).copy()
    for h, w in reversed(sizes):
        block = out[:h, :w].copy()
        col_inv = np.empty_like(block)
        for c in range(w):
            col_inv[:, c] = cdf53_inverse1d(block[:, c], h)
        row_inv = np.empty_like(col_inv)
        for r in range(h):
            row_inv[r, :] = cdf53_inverse1d(col_inv[r, :], w)
        out[:h, :w] = row_inv
    return out


def subband_weight_map(shape: tuple[int, int], sizes: list[tuple[int, int]]) -> np.ndarray:
    weights = np.ones(shape, dtype=np.float64)
    n = len(sizes)
    for level_index, (h, w) in enumerate(sizes):
        detail_weight = 0.85 + 0.15 * (n - level_index)
        weights[: h // 2, w // 2 : w] = detail_weight
        weights[h // 2 : h, : w // 2] = detail_weight
        weights[h // 2 : h, w // 2 : w] = detail_weight * 0.9
    if sizes:
        h, w = sizes[-1]
        weights[: h // 2, : w // 2] = 1.6
    return weights


def entropy_bits(q: np.ndarray) -> float:
    values, counts = np.unique(q.astype(np.int64), return_counts=True)
    del values
    probs = counts.astype(np.float64) / float(q.size)
    entropy = -float(np.sum(probs * np.log2(probs)))
    return entropy * float(q.size)


def quantize_coeffs(
    coeff: np.ndarray, base_step: float, comp_weight: float, sub_weights: np.ndarray
) -> tuple[np.ndarray, float]:
    step = base_step / math.sqrt(comp_weight) / np.sqrt(sub_weights)
    q = np.rint(coeff / step).astype(np.int64)
    bits = entropy_bits(q)
    return q.astype(np.float64) * step, bits


def transform_components(
    planes: dict[str, np.ndarray], codec: ProxyCodec, levels: int
) -> tuple[dict[str, np.ndarray], dict[str, list[tuple[int, int]]], dict[str, np.ndarray]]:
    comps = codec.forward(planes)
    coeffs: dict[str, np.ndarray] = {}
    sizes: dict[str, list[tuple[int, int]]] = {}
    weights: dict[str, np.ndarray] = {}
    for name, comp in comps.items():
        transform = codec.component_transforms.get(name, codec.transform) if codec.component_transforms else codec.transform
        if transform == "cdf53":
            coeff, comp_sizes = cdf53_forward2d(comp, levels)
        elif transform == "rowdiff":
            coeff, comp_sizes = rowdiff_forward2d(comp)
        else:
            coeff, comp_sizes = haar_forward2d(comp, levels)
        coeffs[name] = coeff
        sizes[name] = comp_sizes
        weights[name] = subband_weight_map(coeff.shape, comp_sizes)
    return coeffs, sizes, weights


def rate_for_base_step(
    coeffs: dict[str, np.ndarray],
    codec: ProxyCodec,
    weights: dict[str, np.ndarray],
    base_step: float,
    full_pixel_count: int,
) -> float:
    bits = 0.0
    for name, coeff in coeffs.items():
        _deq, comp_bits = quantize_coeffs(
            coeff, base_step, codec.component_weights[name], weights[name]
        )
        bits += comp_bits
    return bits / float(full_pixel_count)


def encode_proxy(
    planes: dict[str, np.ndarray],
    source_id: str,
    codec: ProxyCodec,
    target_bpp: float,
    levels: int,
) -> EncodeResult:
    start = time.perf_counter()
    coeffs, sizes, weights = transform_components(planes, codec, levels)
    full_pixels = int(next(iter(planes.values())).size * 4)

    lo = 1e-3
    hi = 4096.0
    while rate_for_base_step(coeffs, codec, weights, lo, full_pixels) < target_bpp and lo > 1e-9:
        lo *= 0.25
    while rate_for_base_step(coeffs, codec, weights, hi, full_pixels) > target_bpp and hi < 1e7:
        hi *= 2.0

    for _ in range(42):
        mid = math.sqrt(lo * hi)
        rate = rate_for_base_step(coeffs, codec, weights, mid, full_pixels)
        if rate > target_bpp:
            lo = mid
        else:
            hi = mid

    base_step = hi
    actual_bits = 0.0
    deq_comps: dict[str, np.ndarray] = {}
    for name, coeff in coeffs.items():
        deq_coeff, comp_bits = quantize_coeffs(
            coeff, base_step, codec.component_weights[name], weights[name]
        )
        actual_bits += comp_bits
        transform = codec.component_transforms.get(name, codec.transform) if codec.component_transforms else codec.transform
        if transform == "cdf53":
            deq_comps[name] = cdf53_inverse2d(deq_coeff, sizes[name])
        elif transform == "rowdiff":
            deq_comps[name] = rowdiff_inverse2d(deq_coeff)
        else:
            deq_comps[name] = haar_inverse2d(deq_coeff, sizes[name])
    recon = codec.inverse(deq_comps)
    actual_bpp = actual_bits / float(full_pixels)
    return EncodeResult(
        codec=codec.name,
        source_id=source_id,
        target_bpp=target_bpp,
        actual_bpp=actual_bpp,
        base_step=base_step,
        encode_ms=(time.perf_counter() - start) * 1000.0,
        recon=recon,
    )


def audit_decode_path_reverse_encoder(
    planes: dict[str, np.ndarray], source_id: str, codec: ProxyCodec
) -> RoundtripAudit:
    """Check that the proxy encoder is the algebraic reverse of its decoder path.

    This audit intentionally skips wavelet quantization. For codecs that enter a
    non-bijective decoder LUT/code domain, the remaining sample-domain error is
    the LUT projection residual rather than wavelet quantization loss.
    """

    comps = codec.forward(planes)
    recon = codec.inverse(comps)
    src = flatten_planes(planes)
    rec = flatten_planes(recon)
    diff = rec - src
    mse = float(np.mean(diff * diff))
    mae = float(np.mean(np.abs(diff)))
    max_abs = float(np.max(np.abs(diff))) if diff.size else 0.0
    psnr = float("inf") if mse == 0.0 else 10.0 * math.log10((RANGE * RANGE) / mse)
    return RoundtripAudit(
        codec=codec.name,
        source_id=source_id,
        max_abs_error=max_abs,
        mae=mae,
        mse=mse,
        psnr_raw=psnr,
    )


def flatten_planes(planes: dict[str, np.ndarray]) -> np.ndarray:
    return np.concatenate([planes[k].ravel() for k in ("R", "G0", "G1", "B")])


def metric_summary(src: np.ndarray, rec: np.ndarray) -> dict[str, float]:
    diff = rec.astype(np.float64) - src.astype(np.float64)
    mse = float(np.mean(diff * diff))
    mae = float(np.mean(np.abs(diff)))
    max_abs = float(np.max(np.abs(diff))) if diff.size else 0.0
    psnr = float("inf") if mse == 0.0 else 10.0 * math.log10((RANGE * RANGE) / mse)
    return {"MSE": mse, "MAE": mae, "MAX": max_abs, "PSNR_raw": psnr}


def gradient_magnitude(x: np.ndarray) -> np.ndarray:
    arr = x.astype(np.float64)
    gx = np.diff(arr, axis=1, append=arr[:, -1:])
    gy = np.diff(arr, axis=0, append=arr[-1:, :])
    return np.sqrt(gx * gx + gy * gy)


def laplacian_response(x: np.ndarray) -> np.ndarray:
    arr = x.astype(np.float64)
    return (
        -4.0 * arr
        + np.roll(arr, 1, axis=0)
        + np.roll(arr, -1, axis=0)
        + np.roll(arr, 1, axis=1)
        + np.roll(arr, -1, axis=1)
    )


def gaussian_kernel1d(size: int = 11, sigma: float = 1.5) -> np.ndarray:
    coords = np.arange(size, dtype=np.float64) - (size - 1) / 2.0
    kernel = np.exp(-(coords * coords) / (2.0 * sigma * sigma))
    return kernel / np.sum(kernel)


def separable_filter(x: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    arr = x.astype(np.float64)
    if scipy_ndimage is not None:
        arr = scipy_ndimage.convolve1d(arr, kernel, axis=0, mode="reflect")
        arr = scipy_ndimage.convolve1d(arr, kernel, axis=1, mode="reflect")
        return arr
    radius = int(kernel.size // 2)
    for axis in (0, 1):
        pad_width = [(0, 0)] * arr.ndim
        pad_width[axis] = (radius, radius)
        padded = np.pad(arr, pad_width, mode="reflect")
        windows = np.lib.stride_tricks.sliding_window_view(padded, kernel.size, axis=axis)
        arr = np.tensordot(windows, kernel, axes=([-1], [0]))
    return arr


SSIM_KERNEL = gaussian_kernel1d()


def ssim_components(src: np.ndarray, rec: np.ndarray, data_range: float = RANGE) -> tuple[float, float]:
    x = src.astype(np.float64)
    y = rec.astype(np.float64)
    ux = separable_filter(x, SSIM_KERNEL)
    uy = separable_filter(y, SSIM_KERNEL)
    ux2 = ux * ux
    uy2 = uy * uy
    uxy = ux * uy
    vx = np.maximum(separable_filter(x * x, SSIM_KERNEL) - ux2, 0.0)
    vy = np.maximum(separable_filter(y * y, SSIM_KERNEL) - uy2, 0.0)
    vxy = separable_filter(x * y, SSIM_KERNEL) - uxy
    c1 = (0.01 * data_range) ** 2
    c2 = (0.03 * data_range) ** 2
    luminance = (2.0 * uxy + c1) / (ux2 + uy2 + c1)
    contrast_structure = (2.0 * vxy + c2) / (vx + vy + c2)
    ssim_map = luminance * contrast_structure
    return float(np.mean(ssim_map)), float(np.mean(contrast_structure))


def ssim_index(src: np.ndarray, rec: np.ndarray, data_range: float = RANGE) -> float:
    return ssim_components(src, rec, data_range=data_range)[0]


def downsample2(x: np.ndarray) -> np.ndarray:
    h = (x.shape[0] // 2) * 2
    w = (x.shape[1] // 2) * 2
    if h < 2 or w < 2:
        return x
    return x[:h, :w].reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))


def ms_ssim_index(src: np.ndarray, rec: np.ndarray, data_range: float = RANGE) -> float:
    weights = np.array([0.0448, 0.2856, 0.3001, 0.2363, 0.1333], dtype=np.float64)
    x = src.astype(np.float64)
    y = rec.astype(np.float64)
    mssim: list[float] = []
    mcs: list[float] = []
    for level in range(len(weights)):
        ssim_value, cs_value = ssim_components(x, y, data_range=data_range)
        mssim.append(max(ssim_value, 1e-12))
        mcs.append(max(cs_value, 1e-12))
        if level != len(weights) - 1:
            x = downsample2(x)
            y = downsample2(y)
    score = 1.0
    for value, weight in zip(mcs[:-1], weights[:-1]):
        score *= value**float(weight)
    score *= mssim[-1] ** float(weights[-1])
    return float(score)


def prewitt_magnitude(x: np.ndarray) -> np.ndarray:
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


def gmsd_index(src: np.ndarray, rec: np.ndarray, data_range: float = RANGE) -> float:
    gs = prewitt_magnitude(src)
    gr = prewitt_magnitude(rec)
    c = 0.0026 * data_range * data_range
    gms = (2.0 * gs * gr + c) / (gs * gs + gr * gr + c)
    return float(np.std(gms))


def highpass_response(x: np.ndarray) -> np.ndarray:
    arr = x.astype(np.float64)
    smooth = (
        arr
        + np.roll(arr, 1, axis=0)
        + np.roll(arr, -1, axis=0)
        + np.roll(arr, 1, axis=1)
        + np.roll(arr, -1, axis=1)
    ) / 5.0
    return arr - smooth


def block_mae(src: np.ndarray, rec: np.ndarray, block: int = 8) -> tuple[float, float]:
    h = min(src.shape[0], rec.shape[0])
    w = min(src.shape[1], rec.shape[1])
    h = (h // block) * block
    w = (w // block) * block
    if h == 0 or w == 0:
        return 0.0, 0.0
    diff = np.abs(rec[:h, :w] - src[:h, :w])
    blocks = diff.reshape(h // block, block, w // block, block).mean(axis=(1, 3))
    return float(np.mean(blocks)), float(np.max(blocks))


def detail_metric_summary(source: dict[str, np.ndarray], recon: dict[str, np.ndarray]) -> dict[str, float]:
    grad_src = []
    grad_rec = []
    lap_src = []
    lap_rec = []
    hp_src = []
    hp_rec = []
    block_mean = []
    block_worst = []
    ssim_scores = []
    ms_ssim_scores = []
    gmsd_scores = []
    for plane in ("R", "G0", "G1", "B"):
        src = source[plane]
        rec = recon[plane]
        grad_src.append(gradient_magnitude(src).ravel())
        grad_rec.append(gradient_magnitude(rec).ravel())
        lap_src.append(laplacian_response(src).ravel())
        lap_rec.append(laplacian_response(rec).ravel())
        hp_src.append(highpass_response(src).ravel())
        hp_rec.append(highpass_response(rec).ravel())
        bm, bw = block_mae(src, rec, block=8)
        block_mean.append(bm)
        block_worst.append(bw)
        ssim_scores.append(ssim_index(src, rec))
        ms_ssim_scores.append(ms_ssim_index(src, rec))
        gmsd_scores.append(gmsd_index(src, rec))

    grad_s = np.concatenate(grad_src)
    grad_r = np.concatenate(grad_rec)
    lap_s = np.concatenate(lap_src)
    lap_r = np.concatenate(lap_rec)
    hp_s = np.concatenate(hp_src)
    hp_r = np.concatenate(hp_rec)
    grad_diff = grad_r - grad_s
    lap_diff = lap_r - lap_s
    grad_mse = float(np.mean(grad_diff * grad_diff))
    grad_range = max(float(np.percentile(grad_s, 99.5)), 1.0)
    edge_threshold = float(np.percentile(grad_s, 90.0))
    edge_mask = grad_s >= edge_threshold
    src_energy = float(np.mean(hp_s * hp_s))
    rec_energy = float(np.mean(hp_r * hp_r))
    return {
        "grad_mae": float(np.mean(np.abs(grad_diff))),
        "grad_psnr": float("inf") if grad_mse == 0.0 else 10.0 * math.log10((grad_range * grad_range) / grad_mse),
        "laplacian_mae": float(np.mean(np.abs(lap_diff))),
        "edge_mae": float(np.mean(np.abs((flatten_planes(recon) - flatten_planes(source))[edge_mask]))),
        "highfreq_energy_ratio": rec_energy / src_energy if src_energy > 0 else 1.0,
        "block8_mae_mean": float(statistics.mean(block_mean)),
        "block8_mae_worst": float(max(block_worst)),
        "ssim_mean": float(statistics.mean(ssim_scores)),
        "ms_ssim_mean": float(statistics.mean(ms_ssim_scores)),
        "gmsd_mean": float(statistics.mean(gmsd_scores)),
    }


def collect_metrics(
    source: dict[str, np.ndarray], result: EncodeResult
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    splits: dict[str, tuple[np.ndarray, np.ndarray]] = {
        "whole": (flatten_planes(source), flatten_planes(result.recon)),
    }
    for plane in ("R", "G0", "G1", "B"):
        splits[plane] = (source[plane].ravel(), result.recon[plane].ravel())

    src_all = flatten_planes(source)
    rec_all = flatten_planes(result.recon)
    highlight_mask = src_all >= WHITE * 0.90
    shadow_mask = src_all <= BLACK + RANGE * 0.05
    if np.any(highlight_mask):
        splits["highlight"] = (src_all[highlight_mask], rec_all[highlight_mask])
    if np.any(shadow_mask):
        splits["shadow"] = (src_all[shadow_mask], rec_all[shadow_mask])

    for split, (src_values, rec_values) in splits.items():
        stats = metric_summary(src_values, rec_values)
        for metric, value in stats.items():
            rows.append(
                {
                    "codec": result.codec,
                    "source_id": result.source_id,
                    "target_bpp": f"{result.target_bpp:.6f}",
                    "actual_bpp": f"{result.actual_bpp:.9f}",
                    "metric": metric,
                    "value": f"{value:.9f}",
                    "split": split,
                    "base_step": f"{result.base_step:.9f}",
                    "encode_ms": f"{result.encode_ms:.3f}",
                }
            )
    for metric, value in detail_metric_summary(source, result.recon).items():
        rows.append(
            {
                "codec": result.codec,
                "source_id": result.source_id,
                "target_bpp": f"{result.target_bpp:.6f}",
                "actual_bpp": f"{result.actual_bpp:.9f}",
                "metric": metric,
                "value": f"{value:.9f}",
                "split": "detail",
                "base_step": f"{result.base_step:.9f}",
                "encode_ms": f"{result.encode_ms:.3f}",
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"no rows to write for {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_roundtrip_audit(
    out_dir: Path, sources: dict[str, dict[str, np.ndarray]]
) -> tuple[Path, dict[str, float]]:
    rows: list[dict[str, object]] = []
    max_by_codec: dict[str, float] = {codec.name: 0.0 for codec in CODECS.values()}
    for source_id, planes in sources.items():
        for codec in CODECS.values():
            audit = audit_decode_path_reverse_encoder(planes, source_id, codec)
            max_by_codec[audit.codec] = max(max_by_codec[audit.codec], audit.max_abs_error)
            if audit.max_abs_error == 0.0:
                status = "exact"
            elif audit.max_abs_error <= codec.roundtrip_projection_tolerance:
                status = "lut_projection_limited"
            else:
                status = "mismatch"
            rows.append(
                {
                    "codec": audit.codec,
                    "source_id": audit.source_id,
                    "max_abs_error": f"{audit.max_abs_error:.9f}",
                    "mae": f"{audit.mae:.9f}",
                    "mse": f"{audit.mse:.9f}",
                    "psnr_raw": "inf" if math.isinf(audit.psnr_raw) else f"{audit.psnr_raw:.9f}",
                    "status": status,
                }
            )
    path = out_dir / "roundtrip_audit.csv"
    write_csv(path, rows)
    return path, max_by_codec


def write_delta_summaries(out_dir: Path, metric_rows: list[dict[str, object]]) -> dict[str, str]:
    psnr: dict[tuple[str, float, str], float] = {}
    mae: dict[tuple[str, float, str], float] = {}
    max_err: dict[tuple[str, float, str], float] = {}
    actual_bpp: dict[tuple[str, float, str], float] = {}
    detail: dict[tuple[str, float, str, str], float] = {}
    for row in metric_rows:
        if row["split"] == "detail":
            key = (str(row["source_id"]), float(row["target_bpp"]), str(row["codec"]), str(row["metric"]))
            detail[key] = float(row["value"])
            continue
        if row["split"] != "whole":
            continue
        key = (str(row["source_id"]), float(row["target_bpp"]), str(row["codec"]))
        actual_bpp[key] = float(row["actual_bpp"])
        if row["metric"] == "PSNR_raw":
            psnr[key] = float(row["value"])
        elif row["metric"] == "MAE":
            mae[key] = float(row["value"])
        elif row["metric"] == "MAX":
            max_err[key] = float(row["value"])

    codec_n = NIKON_CODEC_NAME
    codec_s = SONY_CODEC_NAME
    source_ids = sorted({str(row["source_id"]) for row in metric_rows})
    targets = sorted({float(row["target_bpp"]) for row in metric_rows})

    target_rows: list[dict[str, object]] = []
    for target in targets:
        psnr_delta = []
        mae_delta = []
        max_delta = []
        bpp_delta = []
        for source_id in source_ids:
            key_n = (source_id, target, codec_n)
            key_s = (source_id, target, codec_s)
            if key_n not in psnr or key_s not in psnr:
                continue
            psnr_delta.append(psnr[key_s] - psnr[key_n])
            mae_delta.append(mae[key_s] - mae[key_n])
            max_delta.append(max_err[key_s] - max_err[key_n])
            bpp_delta.append(actual_bpp[key_s] - actual_bpp[key_n])
        target_rows.append(
            {
                "target_bpp": f"{target:.6f}",
                "median_actual_bpp_sony_minus_nikon": f"{statistics.median(bpp_delta):.9f}",
                "median_psnr_sony_minus_nikon_db": f"{statistics.median(psnr_delta):.9f}",
                "median_mae_sony_minus_nikon": f"{statistics.median(mae_delta):.9f}",
                "median_max_sony_minus_nikon": f"{statistics.median(max_delta):.9f}",
                "sony_psnr_wins": sum(1 for value in psnr_delta if value > 0),
                "source_count": len(psnr_delta),
            }
        )

    source_rows: list[dict[str, object]] = []
    for source_id in source_ids:
        psnr_delta = []
        mae_delta = []
        for target in targets:
            key_n = (source_id, target, codec_n)
            key_s = (source_id, target, codec_s)
            if key_n not in psnr or key_s not in psnr:
                continue
            psnr_delta.append(psnr[key_s] - psnr[key_n])
            mae_delta.append(mae[key_s] - mae[key_n])
        source_rows.append(
            {
                "source_id": source_id,
                "mean_psnr_sony_minus_nikon_db": f"{statistics.mean(psnr_delta):.9f}",
                "mean_mae_sony_minus_nikon": f"{statistics.mean(mae_delta):.9f}",
                "sony_psnr_wins": sum(1 for value in psnr_delta if value > 0),
                "target_count": len(psnr_delta),
            }
        )

    target_path = out_dir / "same_rate_summary.csv"
    source_path = out_dir / "source_delta_summary.csv"
    detail_path = out_dir / "detail_summary.csv"
    write_csv(target_path, target_rows)
    write_csv(source_path, source_rows)

    detail_metrics = [
        ("ssim_mean", "higher"),
        ("ms_ssim_mean", "higher"),
        ("gmsd_mean", "lower"),
        ("grad_psnr", "higher"),
        ("grad_mae", "lower"),
        ("laplacian_mae", "lower"),
        ("edge_mae", "lower"),
        ("highfreq_energy_ratio", "closer_to_1"),
        ("block8_mae_mean", "lower"),
        ("block8_mae_worst", "lower"),
    ]
    detail_rows: list[dict[str, object]] = []
    for target in targets:
        for metric, direction in detail_metrics:
            deltas = []
            sony_wins = 0
            source_count = 0
            for source_id in source_ids:
                key_n = (source_id, target, codec_n, metric)
                key_s = (source_id, target, codec_s, metric)
                if key_n not in detail or key_s not in detail:
                    continue
                n = detail[key_n]
                s = detail[key_s]
                source_count += 1
                if direction == "higher":
                    delta = s - n
                    sony_wins += int(delta > 0)
                elif direction == "lower":
                    delta = s - n
                    sony_wins += int(delta < 0)
                else:
                    delta = abs(s - 1.0) - abs(n - 1.0)
                    sony_wins += int(delta < 0)
                deltas.append(delta)
            if deltas:
                detail_rows.append(
                    {
                        "target_bpp": f"{target:.6f}",
                        "metric": metric,
                        "direction": direction,
                        "median_sony_minus_nikon": f"{statistics.median(deltas):.9f}",
                        "sony_wins": sony_wins,
                        "source_count": source_count,
                    }
                )
    write_csv(detail_path, detail_rows)
    return {
        "same_rate_summary": str(target_path),
        "source_delta_summary": str(source_path),
        "detail_summary": str(detail_path),
    }


def parse_targets(text: str) -> list[float]:
    targets = [float(part.strip()) for part in text.split(",") if part.strip()]
    if len(targets) < 1 or any(t <= 0 for t in targets):
        raise ValueError("targets must be positive bpp values")
    return targets


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=Path("out/proxy_four_plane_benchmark"))
    ap.add_argument("--width", type=int, default=512, help="full RGGB mosaic width")
    ap.add_argument("--height", type=int, default=512, help="full RGGB mosaic height")
    ap.add_argument("--levels", type=int, default=4)
    ap.add_argument("--targets", default="1.5,2.0,2.5,3.0,4.0,5.0")
    ap.add_argument("--seed", type=int, default=20260602)
    ap.add_argument("--input-raw", type=Path, help="optional little-endian uint16 RGGB raw mosaic")
    ns = ap.parse_args()

    if ns.width <= 0 or ns.height <= 0 or ns.width % 2 or ns.height % 2:
        raise ValueError("width and height must be positive even numbers")

    rng = np.random.default_rng(ns.seed)
    plane_h = ns.height // 2
    plane_w = ns.width // 2
    sources: dict[str, dict[str, np.ndarray]] = {}
    if ns.input_raw:
        sources[ns.input_raw.stem] = load_rggb_raw(ns.input_raw, ns.width, ns.height)
    else:
        for scene in DEFAULT_SCENES:
            sources[scene] = generate_scene(scene, plane_h, plane_w, rng)

    targets = parse_targets(ns.targets)
    ns.out_dir.mkdir(parents=True, exist_ok=True)
    metric_rows: list[dict[str, object]] = []
    encode_rows: list[dict[str, object]] = []
    roundtrip_path, roundtrip_max_by_codec = write_roundtrip_audit(ns.out_dir, sources)

    for source_id, planes in sources.items():
        for target_bpp in targets:
            for codec in CODECS.values():
                result = encode_proxy(planes, source_id, codec, target_bpp, ns.levels)
                encode_rows.append(
                    {
                        "codec": result.codec,
                        "source_id": result.source_id,
                        "target_bpp": f"{result.target_bpp:.6f}",
                        "actual_bpp": f"{result.actual_bpp:.9f}",
                        "base_step": f"{result.base_step:.9f}",
                        "encode_ms": f"{result.encode_ms:.3f}",
                    }
                )
                metric_rows.extend(collect_metrics(planes, result))

    write_csv(ns.out_dir / "encodes.csv", encode_rows)
    write_csv(ns.out_dir / "metrics.csv", metric_rows)
    summaries = write_delta_summaries(ns.out_dir, metric_rows)
    manifest = {
        "kind": "L2 proxy virtual four-plane RGGB benchmark",
        "evidence_level": "L2 same-source mathematical proxy benchmark; not a production encoder",
        "not_production_encoder_evidence": True,
        "not_a_production_encoder": True,
        "run_id": ns.out_dir.name,
        "seed": ns.seed,
        "input_mode": "single_input_raw" if ns.input_raw else "deterministic_synthetic_scenes",
        "input_raw": str(ns.input_raw) if ns.input_raw else "",
        "width": ns.width,
        "height": ns.height,
        "plane_width": plane_w,
        "plane_height": plane_h,
        "levels": ns.levels,
        "targets": targets,
        "targets_bpp": targets,
        "black": BLACK,
        "white": WHITE,
        "sources": list(sources.keys()),
        "source_count": len(sources),
        "codec_names": list(CODECS.keys()),
        "codecs": list(CODECS.keys()),
        "codec_transforms": {name: codec.transform for name, codec in CODECS.items()},
        "encode_row_count": len(encode_rows),
        "metric_row_count": len(metric_rows),
        "repro_command": [
            "python",
            "tools/proxy_four_plane_benchmark.py",
            "--out-dir",
            str(ns.out_dir),
            "--width",
            str(ns.width),
            "--height",
            str(ns.height),
            "--levels",
            str(ns.levels),
            "--targets",
            ns.targets,
            "--seed",
            str(ns.seed),
        ],
        "outputs": {
            "encodes": str(ns.out_dir / "encodes.csv"),
            "metrics": str(ns.out_dir / "metrics.csv"),
            "roundtrip_audit": str(roundtrip_path),
            **summaries,
        },
        "roundtrip_reverse_encoder_max_abs_error": roundtrip_max_by_codec,
    }
    (ns.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
