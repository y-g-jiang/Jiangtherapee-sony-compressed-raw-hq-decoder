#!/usr/bin/env python3
"""Strict decoder-visible #824/#826 same-source math evaluation.

This is a fresh replacement for the older four-plane proxy benchmark.  It only
uses structures that are visible in the LibRaw #824/#826 decoders:

* Sony #824 ARW6/LLVC3: signed code LUT domain, final green/RB residual relation,
  CDF 5/3-like subband math, packet selectors, adaptive width, zero runs,
  4-lane magnitude coding and sign bits.
* Nikon #826 HE: IQX/IQP LUT code domain, step1/step2 Bayer equations,
  coefficient GTLI truncation, GCLI groups, bit-plane nibbles and sign bits.

Unknown camera RD policy is made explicit as canonical sweep variables.  The
script does not read or write old proxy outputs and does not generate figures.
"""

from __future__ import annotations

import argparse
import os
import csv
import json
import math
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import numpy as np

try:
    from scipy import ndimage as scipy_ndimage
except Exception:  # pragma: no cover
    scipy_ndimage = None


BLACK = 512.0
WHITE = 16383.0
RANGE = WHITE - BLACK
SONY_CODE_BIAS = 2048.0
SONY_LUT_SIZE = 4096
NIKON_CODE_BIAS = float(1 << (2 + 14 - 1))
NIKON_LUT_SIZE = 81792
TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_SONY_LUT = TOOLS_DIR / "data" / "sony_llvc3_static_lut4096.tsv"
DEFAULT_NIKON_LUT = TOOLS_DIR / "data" / "nikon_he_iqx_iqp_lut81792.tsv"

SONY_CODEC = "sony_824_decoder_visible_packet_canonical"
NIKON_CODEC = "nikon_826_decoder_visible_precinct_canonical"

SCENES = [
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

GTLI_ROWS: dict[tuple[int, int], tuple[int, ...]] = {
    (4, 0): (1,1,2,2,3,3, 2,3,3,4,4,4, 4, 2,3,3,4,4,4, 3,4, 4,4, 4, 4,4),
    (4, 1): (1,1,2,2,3,3, 2,3,3,4,4,4, 4, 2,3,3,3,4,4, 3,4, 4,4, 4, 4,4),
    (4, 2): (1,1,2,2,3,3, 2,3,3,3,4,4, 4, 2,3,3,3,4,4, 3,4, 4,4, 4, 4,4),
    (4, 3): (0,1,2,2,3,3, 2,3,3,3,4,4, 4, 2,3,3,3,4,4, 3,4, 4,4, 4, 4,4),
    (4, 4): (0,1,2,2,3,3, 2,2,3,3,4,4, 4, 2,3,3,3,4,4, 3,4, 4,4, 4, 4,4),
    (4, 5): (0,1,2,2,3,3, 2,2,3,3,4,4, 4, 2,2,3,3,4,4, 3,4, 4,4, 4, 4,4),
    (4, 6): (0,1,2,2,3,3, 1,2,3,3,4,4, 4, 2,2,3,3,4,4, 3,4, 4,4, 4, 4,4),
    (4, 7): (0,1,2,2,3,3, 1,2,3,3,4,4, 4, 1,2,3,3,4,4, 3,4, 4,4, 4, 4,4),
    (4, 11): (0,1,1,2,2,3, 1,2,3,3,3,4, 4, 1,2,3,3,3,4, 3,4, 4,4, 4, 4,4),
    (4, 12): (0,1,1,2,2,3, 1,2,3,3,3,4, 3, 1,2,3,3,3,4, 3,4, 4,4, 3, 4,4),
    (5, 12): (1,2,2,3,3,4, 2,3,4,4,4,5, 4, 2,3,4,4,4,5, 4,5, 5,5, 4, 5,5),
    (5, 13): (1,2,2,3,3,4, 2,3,4,4,4,5, 4, 2,3,4,4,4,5, 4,5, 5,5, 4, 4,5),
    (5, 14): (1,2,2,3,3,4, 2,3,4,4,4,4, 4, 2,3,4,4,4,5, 4,5, 5,5, 4, 4,5),
    (5, 15): (1,2,2,3,3,4, 2,3,4,4,4,4, 4, 2,3,4,4,4,4, 4,5, 5,5, 4, 4,5),
    (5, 16): (1,2,2,3,3,4, 2,3,4,4,4,4, 4, 2,3,4,4,4,4, 4,5, 4,5, 4, 4,5),
    (5, 20): (1,1,2,3,3,4, 2,3,3,4,4,4, 4, 2,3,3,4,4,4, 4,4, 4,5, 4, 4,5),
    (5, 21): (1,1,2,3,3,4, 2,3,3,4,4,4, 4, 2,3,3,4,4,4, 3,4, 4,5, 4, 4,5),
    (5, 22): (1,1,2,3,3,3, 2,3,3,4,4,4, 4, 2,3,3,4,4,4, 3,4, 4,5, 4, 4,5),
    (5, 23): (1,1,2,2,3,3, 2,3,3,4,4,4, 4, 2,3,3,4,4,4, 3,4, 4,5, 4, 4,5),
    (5, 24): (1,1,2,2,3,3, 2,3,3,4,4,4, 4, 2,3,3,4,4,4, 3,4, 4,4, 4, 4,5),
}

NIKON_SUBBANDS = [
    (0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (5, 0),
    (6, 1), (7, 1), (8, 1), (9, 1), (10, 1), (11, 1),
    (12, 2),
    (13, 3), (14, 3), (15, 3), (16, 3), (17, 3), (18, 3),
    (19, 4), (20, 4), (21, 5), (22, 5), (23, 6), (24, 7), (25, 7),
]

NIKON_COMPONENT_TO_SUBBAND = {
    "p1_LL_step1": 12,
    "p2_LH_step1": 13,
    "p3_HH_step1": 14,
    "p4_HL_step1": 23,
}

# Exact constants from LibRaw #826
# src/decoders/nikon_he/nikon_he_dequantize.h:kMidpointScaleTable.
NIKON_MIDPOINT_SCALE_TABLE = (
    87381, 74898, 69905, 67650, 66576, 66052, 65793, 65664,
    65600, 65568, 65552, 65544, 65540, 65538, 65537, 0,
)


@dataclass(frozen=True)
class Codec:
    name: str
    forward: Callable[[dict[str, np.ndarray]], dict[str, np.ndarray]]
    inverse: Callable[[dict[str, np.ndarray]], dict[str, np.ndarray]]
    syntax_encode: Callable[[dict[str, np.ndarray], float], tuple[dict[str, np.ndarray], dict[str, float]]]
    knob_name: str


@dataclass
class EncodeResult:
    codec: str
    source_id: str
    target_bpp: float
    actual_bpp: float
    knob: float
    encode_ms: float
    recon: dict[str, np.ndarray]
    syntax: dict[str, float]


def clip_raw(x: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(x), BLACK, WHITE).astype(np.float64)


def load_code_lut(path: Path, size: int, value_column: int = 1) -> np.ndarray:
    rows: list[float] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(",", "\t").split()
            if not parts or parts[0].lower() in {"code", "input", "src"}:
                continue
            try:
                rows.append(float(parts[min(value_column, len(parts) - 1)]))
            except ValueError:
                continue
    if len(rows) < size:
        raise ValueError(f"could not load {size} entries from {path}")
    return np.asarray(rows[:size], dtype=np.float64)


_SONY_LUT: np.ndarray | None = None
_NIKON_LUT: np.ndarray | None = None


def sony_lut() -> np.ndarray:
    global _SONY_LUT
    if _SONY_LUT is None:
        _SONY_LUT = load_code_lut(DEFAULT_SONY_LUT, SONY_LUT_SIZE, value_column=1)
    return _SONY_LUT


def nikon_lut() -> np.ndarray:
    global _NIKON_LUT
    if _NIKON_LUT is None:
        # The TSV stores both decoder-internal i32 LUT values and the final
        # 14-bit sample values.  Metrics below compare reconstructed RAW
        # samples, so the inverse/projection LUT must use sample14.
        _NIKON_LUT = load_code_lut(DEFAULT_NIKON_LUT, NIKON_LUT_SIZE, value_column=2)
    return _NIKON_LUT


def inverse_lut_nearest(samples: np.ndarray, lut: np.ndarray) -> np.ndarray:
    idx = np.searchsorted(lut, samples, side="left")
    idx = np.clip(idx, 0, lut.size - 1)
    prev = np.clip(idx - 1, 0, lut.size - 1)
    choose_prev = np.abs(lut[prev] - samples) <= np.abs(lut[idx] - samples)
    return np.where(choose_prev, prev, idx).astype(np.float64)


def sony_sample_to_signed_code(samples: np.ndarray) -> np.ndarray:
    return np.clip(inverse_lut_nearest(samples, sony_lut()), 0, SONY_LUT_SIZE - 1) - SONY_CODE_BIAS


def sony_signed_code_to_sample(code: np.ndarray) -> np.ndarray:
    idx = np.clip(np.rint(code + SONY_CODE_BIAS), 0, SONY_LUT_SIZE - 1).astype(np.int64)
    return sony_lut()[idx].astype(np.float64)


def nikon_sample_to_decoder_code(samples: np.ndarray) -> np.ndarray:
    return np.clip(inverse_lut_nearest(samples, nikon_lut()), 0, NIKON_LUT_SIZE - 1) - NIKON_CODE_BIAS


def nikon_decoder_code_to_sample(code: np.ndarray) -> np.ndarray:
    idx = np.clip(np.rint(code + NIKON_CODE_BIAS), 0, NIKON_LUT_SIZE - 1).astype(np.int64)
    return nikon_lut()[idx].astype(np.float64)


def generate_scene(scene: str, plane_h: int, plane_w: int, rng: np.random.Generator) -> dict[str, np.ndarray]:
    y, x = np.mgrid[0:plane_h, 0:plane_w]
    xf = x / max(1, plane_w - 1)
    yf = y / max(1, plane_h - 1)
    base = BLACK + RANGE * (0.12 + 0.55 * xf + 0.18 * yf)
    noise = rng.normal(0, 5, size=base.shape)

    if scene == "smooth_gradient":
        lum = base + 180.0 * np.sin(2 * np.pi * xf)
        cr = 180.0 * np.sin(2 * np.pi * yf)
        cb = -140.0 * np.cos(2 * np.pi * xf)
    elif scene == "fine_texture":
        tex = 900.0 * np.sin(2 * np.pi * 17 * xf) * np.sin(2 * np.pi * 11 * yf)
        tex += 260.0 * ((x + y) % 2) - 130.0
        lum, cr, cb, noise = base + tex, 380.0 * np.sin(2*np.pi*7*xf), -350.0*np.sin(2*np.pi*9*yf), rng.normal(0, 12, base.shape)
    elif scene == "color_edges":
        stripes = ((x // 16) % 3).astype(np.float64)
        lum = BLACK + RANGE * (0.25 + 0.40 * yf)
        cr = np.where(stripes == 0, 2100.0, np.where(stripes == 1, -900.0, 200.0))
        cb = np.where(stripes == 2, 2100.0, np.where(stripes == 1, -800.0, 100.0))
    elif scene == "highlight_rolloff":
        radius = (xf - 0.62) ** 2 + (yf - 0.36) ** 2
        spot = RANGE * 0.72 * np.exp(-radius / 0.012)
        lum, cr, cb = base + spot, 260.0 + 850.0*np.exp(-radius/0.02), -160.0 + 260.0*np.exp(-radius/0.05)
    elif scene == "shadow_noise":
        lum, cr, cb, noise = BLACK + RANGE*(0.035 + 0.075*xf + 0.045*yf), 80*np.sin(2*np.pi*5*xf), -90*np.cos(2*np.pi*4*yf), rng.normal(0, 38, base.shape)
    elif scene == "green_phase_alias":
        diag = ((x + 2 * y) % 13 < 6).astype(np.float64)
        lum, cr, cb = base + 560.0 * diag, 180*np.sin(2*np.pi*3*xf), -180*np.cos(2*np.pi*3*yf)
    elif scene == "decorrelated_color":
        lum = base + 240.0 * np.sin(2*np.pi*5*(xf+yf))
        cr = 1600*np.sin(2*np.pi*4*xf) + 700*np.sign(np.sin(2*np.pi*3*yf))
        cb = 1500*np.cos(2*np.pi*5*yf) - 600*np.sign(np.sin(2*np.pi*2*xf))
    elif scene == "slanted_edge":
        edge = (0.68 * xf + 0.43 * yf) > 0.58
        lum, cr, cb = BLACK + RANGE * (0.18 + 0.46 * edge.astype(float)), 250*edge.astype(float), -180*edge.astype(float)
    elif scene == "thin_black_lines":
        lines = (((x + 2*y) % 19) == 0) | (((2*x + y) % 23) == 0)
        lum, cr, cb = base - 1800*lines.astype(float), 320*np.sin(2*np.pi*2*yf), -280*np.cos(2*np.pi*2*xf)
    elif scene == "zone_plate":
        rr = (xf - 0.5) ** 2 + (yf - 0.5) ** 2
        lum, cr, cb = base + 950*np.sin(2*np.pi*(8*rr + 46*rr*rr)), 180*np.sin(2*np.pi*3*xf), 180*np.cos(2*np.pi*4*yf)
    elif scene == "nyquist_checker":
        checker = ((x + y) & 1).astype(float) * 2.0 - 1.0
        lum, cr, cb = base + 620*checker, 260*((x & 1).astype(float)*2-1), -260*((y & 1).astype(float)*2-1)
    elif scene == "micro_contrast":
        lum = base.copy()
        for freq, amp in [(5, 180), (13, 90), (29, 45), (47, 24)]:
            lum += amp * np.sin(2*np.pi*freq*(xf + 0.37*yf))
        cr, cb = 160*np.sin(2*np.pi*11*xf), -140*np.cos(2*np.pi*9*yf)
    elif scene == "random_foliage":
        field = rng.normal(0, 1, base.shape)
        field = (field + np.roll(field, 1, 0) + np.roll(field, -1, 0) + np.roll(field, 1, 1) + np.roll(field, -1, 1)) / 5
        lum, cr, cb, noise = base + 820*field + 220*np.sin(2*np.pi*19*xf), 420*np.roll(field, 3, 1), -390*np.roll(field, -2, 0), rng.normal(0, 16, base.shape)
    elif scene == "color_checker":
        gx = np.clip(x // max(1, plane_w // 8), 0, 7)
        gy = np.clip(y // max(1, plane_h // 6), 0, 5)
        patch = ((gx + 2 * gy) % 6).astype(int)
        lum = BLACK + RANGE * (0.18 + 0.095 * patch)
        cr = np.choose(patch, [-900, -300, 200, 700, 1200, 300])
        cb = np.choose(patch, [800, 200, -500, -900, 100, 1200])
    elif scene == "specular_grid":
        grid = ((x % 48) < 3) | ((y % 48) < 3)
        spot = np.exp(-((xf - 0.72) ** 2 + (yf - 0.28) ** 2) / 0.006)
        lum, cr, cb = base + 1250*grid.astype(float) + RANGE*0.7*spot, 520*spot, -280*spot
    elif scene == "shadow_fabric":
        weave = 0.5*np.sin(2*np.pi*31*xf) + 0.5*np.sin(2*np.pi*27*yf)
        lum, cr, cb, noise = BLACK + RANGE*(0.045 + 0.03*xf + 0.02*yf) + 120*weave, 70*weave, -60*np.roll(weave,1,1), rng.normal(0, 42, base.shape)
    elif scene == "chroma_noise":
        lum, cr, cb, noise = base + rng.normal(0, 20, base.shape), rng.normal(0, 720, base.shape), rng.normal(0, 680, base.shape), rng.normal(0, 12, base.shape)
    elif scene == "bayer_phase_steps":
        steps = np.floor(xf * 12) / 12.0
        lum = BLACK + RANGE * (0.15 + 0.65 * steps)
        cr, cb = 420*((y//8)%2), -380*((x//8)%2)
        gp = 220*(((x+y)//4)%2) - 110
        return {"R": clip_raw(lum+cr+noise), "G0": clip_raw(lum+gp+noise*0.5), "G1": clip_raw(lum-gp+rng.normal(0,5,base.shape)), "B": clip_raw(lum+cb+noise)}
    elif scene == "tile_boundary_stress":
        boundary = ((x % 64) < 2) | ((y % 64) < 2)
        diag = ((x + y) % 31) < 2
        lum, cr, cb = base + 780*boundary.astype(float) - 520*diag.astype(float), 450*diag.astype(float), -430*boundary.astype(float)
    elif scene == "skin_like_smooth":
        lum = BLACK + RANGE*(0.32 + 0.12*xf + 0.06*yf) + 38*np.sin(2*np.pi*4*xf) + 22*np.sin(2*np.pi*6*yf)
        cr, cb = 520 + 60*np.sin(2*np.pi*3*yf), -380 + 45*np.cos(2*np.pi*5*xf)
    elif scene == "low_contrast_detail":
        lum = BLACK + RANGE*(0.42 + 0.08*xf) + 70*np.sin(2*np.pi*23*xf)*np.sin(2*np.pi*17*yf)
        cr, cb = 45*np.sin(2*np.pi*9*xf), -55*np.cos(2*np.pi*7*yf)
    elif scene == "high_iso_texture":
        tex = 500*np.sin(2*np.pi*15*xf)*np.cos(2*np.pi*21*yf)
        lum, cr, cb, noise = base + tex, 240*np.sin(2*np.pi*4*xf), -260*np.cos(2*np.pi*4*yf), rng.normal(0, 72, base.shape)
    elif scene == "red_blue_fine_text":
        strokes = (((x // 5) % 4) == 0) & (((y // 13) % 2) == 0)
        lum, cr, cb = base - 420*strokes.astype(float), 1350*strokes.astype(float)-120, -1250*strokes.astype(float)+90
    elif scene == "blue_channel_detail":
        lum, cr, cb = base + 110*np.sin(2*np.pi*6*xf), 100*np.sin(2*np.pi*3*yf), 900*np.sin(2*np.pi*25*(xf + 0.2*yf))
    else:
        raise ValueError(scene)

    gp = 38.0 * np.sin(2 * np.pi * (xf * 3.0 + yf * 2.0))
    return {"R": clip_raw(lum + cr + noise), "G0": clip_raw(lum + gp + noise * 0.6), "G1": clip_raw(lum - gp + rng.normal(0, max(float(np.std(noise)), 1.0), base.shape) * 0.6), "B": clip_raw(lum + cb + noise)}


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


def floor_shift2(x: np.ndarray) -> np.ndarray:
    return np.floor(np.asarray(x, dtype=np.float64) / 4.0)


def nikon_step2_predictors(g0: np.ndarray, g1: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    prev_g1 = _prev_edge(g1)
    next_g0 = _next_edge(g0)
    pred_r = floor_shift2(_left_edge(g0) + prev_g1 + g1 + g0)
    pred_r[:, 0] = floor_shift2(prev_g1[:, 0] + 2.0 * g0[:, 0] + g1[:, 0])
    pred_b = floor_shift2(_right_edge(g1) + g1 + g0 + next_g0)
    return pred_r, pred_b


def nikon_step1_offsets(p1: np.ndarray, p4: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    prev_p4 = _prev_edge(p4)
    next_p1 = _next_edge(p1)
    ll_hl = p1 + p4
    l_offset = floor_shift2(0.5 * (ll_hl + _right_edge(p1) + prev_p4))
    h_offset = floor_shift2(0.5 * (ll_hl + _left_edge(p4) + next_p1))
    if p1.shape[1]:
        h_offset[:, 0] = floor_shift2(0.5 * (ll_hl[:, 0] + p4[:, 0] + next_p1[:, 0]))
    return l_offset, h_offset


def nikon_step1_merge(p1: np.ndarray, p2: np.ndarray, p3: np.ndarray, p4: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    prev_p3 = _prev_edge(p3)
    next_p2 = _next_edge(p2)
    next_p3 = _next_edge(p3)
    right_p3 = _right_edge(p3)
    l_offset, h_offset = nikon_step1_offsets(p1, p4)
    cur_predict = p2 - floor_shift2(0.5 * (p3 + right_p3 + prev_p3 + _right_edge(prev_p3)))
    l_plane = cur_predict - l_offset
    next_predict = next_p2 - floor_shift2(0.5 * (p3 + right_p3 + next_p3 + _right_edge(next_p3)))
    blend = floor_shift2(_left_edge(cur_predict) + cur_predict + next_predict + _left_edge(next_predict))
    if p1.shape[1]:
        blend[:, 0] = 0.5 * (cur_predict[:, 0] + next_predict[:, 0])
    return l_plane, p3 - h_offset + blend


def nikon_step1_reverse(p1: np.ndarray, l_plane: np.ndarray, h_plane: np.ndarray, p4: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    l_offset, h_offset = nikon_step1_offsets(p1, p4)
    cur_predict = l_plane + l_offset
    next_predict = _next_edge(cur_predict)
    blend = floor_shift2(_left_edge(cur_predict) + cur_predict + next_predict + _left_edge(next_predict))
    if p1.shape[1]:
        blend[:, 0] = 0.5 * (cur_predict[:, 0] + next_predict[:, 0])
    p3 = h_plane + h_offset - blend
    p2 = cur_predict.copy()
    for _ in range(6):
        prev_p3 = _prev_edge(p3)
        p2 = cur_predict + floor_shift2(0.5 * (p3 + _right_edge(p3) + prev_p3 + _right_edge(prev_p3)))
        _l, h_check = nikon_step1_merge(p1, p2, p3, p4)
        err = h_check - h_plane
        if float(np.max(np.abs(err))) < 1e-9:
            break
        p3 -= err
    return p2, p3


def nikon_forward(planes: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    p = {k: nikon_sample_to_decoder_code(v) for k, v in planes.items()}
    r, g0, g1, b = p["R"], p["G0"], p["G1"], p["B"]
    pred_r, pred_b = nikon_step2_predictors(g0, g1)
    p1 = r - pred_r
    p4 = b - pred_b
    p2, p3 = nikon_step1_reverse(p1, g0, g1, p4)
    return {"p1_LL_step1": p1, "p2_LH_step1": p2, "p3_HH_step1": p3, "p4_HL_step1": p4}


def nikon_inverse(comps: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    p1, p2, p3, p4 = comps["p1_LL_step1"], comps["p2_LH_step1"], comps["p3_HH_step1"], comps["p4_HL_step1"]
    g0, g1 = nikon_step1_merge(p1, p2, p3, p4)
    pred_r, pred_b = nikon_step2_predictors(g0, g1)
    centered = {"R": p1 + pred_r, "G0": g0, "G1": g1, "B": p4 + pred_b}
    return {k: nikon_decoder_code_to_sample(v) for k, v in centered.items()}


def sony_forward(planes: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    p = {k: sony_sample_to_signed_code(v) for k, v in planes.items()}
    r, g0, g1, b = p["R"], p["G0"], p["G1"], p["B"]
    gavg = 0.5 * (g0 + g1)
    return {"Gbase": gavg, "Gphase_final": g0 - g1, "Rres2": 0.5 * (r - gavg), "Bres2": 0.5 * (b - gavg)}


def sony_inverse(comps: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    gavg = comps["Gbase"]
    gdiff = comps["Gphase_final"]
    code = {"R": gavg + 2*comps["Rres2"], "G0": gavg + 0.5*gdiff, "G1": gavg - 0.5*gdiff, "B": gavg + 2*comps["Bres2"]}
    return {k: sony_signed_code_to_sample(v) for k, v in code.items()}


def cdf53_forward1d(x: np.ndarray) -> np.ndarray:
    n = int(x.size)
    if n < 2:
        return x.astype(np.float64).copy()
    even = x[0::2].astype(np.float64).copy()
    odd = x[1::2].astype(np.float64).copy()
    n_l, n_h = int(even.size), int(odd.size)
    right = even[1:n_h+1] if n_l > 1 else even[:1]
    if right.size < n_h:
        right = np.concatenate([right, even[-1:]])
    detail = odd - 0.5 * (even[:n_h] + right[:n_h])
    left_detail = np.empty_like(even)
    right_detail = np.empty_like(even)
    left_detail[0] = detail[0]
    if n_l > 1:
        left_detail[1:] = detail[: n_l - 1]
    right_detail[:n_h] = detail
    if n_l > n_h:
        right_detail[-1] = detail[-1]
    return np.concatenate([even + 0.25 * (left_detail + right_detail), detail])


def cdf53_inverse1d(packed: np.ndarray, n: int) -> np.ndarray:
    if n < 2:
        return packed[:n].astype(np.float64).copy()
    n_l, n_h = (n + 1) // 2, n // 2
    approx = packed[:n_l].astype(np.float64).copy()
    detail = packed[n_l:n_l+n_h].astype(np.float64).copy()
    left_detail = np.empty_like(approx)
    right_detail = np.empty_like(approx)
    left_detail[0] = detail[0]
    if n_l > 1:
        left_detail[1:] = detail[: n_l - 1]
    right_detail[:n_h] = detail
    if n_l > n_h:
        right_detail[-1] = detail[-1]
    even = approx - 0.25 * (left_detail + right_detail)
    right = even[1:n_h+1] if n_l > 1 else even[:1]
    if right.size < n_h:
        right = np.concatenate([right, even[-1:]])
    odd = detail + 0.5 * (even[:n_h] + right[:n_h])
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
        row = np.vstack([cdf53_forward1d(block[r, :]) for r in range(h)])
        col = np.empty_like(row)
        for c in range(w):
            col[:, c] = cdf53_forward1d(row[:, c])
        coeff[:h, :w] = col
        sizes.append((h, w))
        h, w = (h + 1) // 2, (w + 1) // 2
    return coeff, sizes


def cdf53_inverse2d(coeff: np.ndarray, sizes: list[tuple[int, int]]) -> np.ndarray:
    out = coeff.astype(np.float64).copy()
    for h, w in reversed(sizes):
        block = out[:h, :w].copy()
        col = np.empty_like(block)
        for c in range(w):
            col[:, c] = cdf53_inverse1d(block[:, c], h)
        row = np.vstack([cdf53_inverse1d(col[r, :], w) for r in range(h)])
        out[:h, :w] = row
    return out


def transform_components(comps: dict[str, np.ndarray], levels: int) -> tuple[dict[str, np.ndarray], dict[str, list[tuple[int, int]]]]:
    coeffs: dict[str, np.ndarray] = {}
    sizes: dict[str, list[tuple[int, int]]] = {}
    for name, comp in comps.items():
        coeffs[name], sizes[name] = cdf53_forward2d(comp, levels)
    return coeffs, sizes


def inverse_transform_components(coeffs: dict[str, np.ndarray], sizes: dict[str, list[tuple[int, int]]]) -> dict[str, np.ndarray]:
    return {name: cdf53_inverse2d(coeff, sizes[name]) for name, coeff in coeffs.items()}


def bits_for_sony_width_update(prev: int, new: int) -> int:
    if new == prev:
        return 1
    if new > prev:
        return 2 + (new - prev)
    dec = prev - new
    return 2 + min(dec, max(prev - 1, 0))


def bits_for_sony_zero_run(run: int, remaining: int) -> int:
    if remaining <= 1:
        return 0
    max_prefix = (remaining - 1).bit_length()
    if run >= remaining:
        return max_prefix
    zeros = max(0, int(math.floor(math.log2(max(run, 1)))))
    return min(zeros, max_prefix) + 1 + zeros


def sony_quantize_group(vals: np.ndarray, base_step: float, selector: int) -> np.ndarray:
    step = base_step * (2.0 ** max(selector, 0))
    return np.rint(vals / step).astype(np.int64)


def sony_dequantize_group(q: np.ndarray, base_step: float, selector: int) -> np.ndarray:
    step = base_step * (2.0 ** max(selector, 0))
    return q.astype(np.float64) * step


def sony_syntax_encode(coeffs: dict[str, np.ndarray], base_step: float) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    deq: dict[str, np.ndarray] = {}
    total_bits = 0
    payload_bits = 0
    sign_bits = 0
    selector_bits = 0
    control_bits = 0
    zero_run_bits = 0
    width_bits = 0
    groups_total = 0
    nonzero_groups = 0
    selector_sum = 0
    for comp_index, (name, coeff) in enumerate(coeffs.items()):
        flat = np.asarray(coeff, dtype=np.float64).reshape(-1, coeff.shape[-1])
        out = np.empty_like(flat, dtype=np.float64)
        selector = 0 if name == "Gbase" else (1 if name == "Gphase_final" else 2)
        selector_sum += selector
        rows = flat.shape[0]
        control_bits += 128 + rows * (16 + 4)
        selector_bits += rows * 4
        for y in range(rows):
            row = flat[y]
            groups = int(math.ceil(row.size / 4))
            width_state = 0
            gi = 0
            while gi < groups:
                vals = np.zeros(4, dtype=np.float64)
                start = gi * 4
                chunk = row[start:start+4]
                vals[: chunk.size] = chunk
                q = sony_quantize_group(vals, base_step, selector)
                width = int(max(0, max((abs(int(v)).bit_length() for v in q), default=0)))
                groups_total += 1
                width_bits += bits_for_sony_width_update(width_state, width)
                if width == 0:
                    run = 1
                    while gi + run < groups:
                        nxt = np.zeros(4, dtype=np.float64)
                        ns = (gi + run) * 4
                        nchunk = row[ns:ns+4]
                        nxt[:nchunk.size] = nchunk
                        if np.any(sony_quantize_group(nxt, base_step, selector)):
                            break
                        run += 1
                    zero_run_bits += bits_for_sony_zero_run(run, groups - gi)
                    for rz in range(run):
                        zs = (gi + rz) * 4
                        out[y, zs:zs+4] = 0.0
                    gi += run
                    width_state = 0
                    continue
                nonzero_groups += 1
                payload_bits += width * 4
                sign_count = int(np.count_nonzero(q))
                sign_bits += sign_count
                out[y, start:start+4] = sony_dequantize_group(q, base_step, selector)
                width_state = width
                gi += 1
        deq[name] = out.reshape(coeff.shape)
    total_bits = control_bits + selector_bits + width_bits + zero_run_bits + payload_bits + sign_bits
    return deq, {
        "syntax_total_bits": float(total_bits),
        "control_bits": float(control_bits),
        "selector_bits": float(selector_bits),
        "width_update_bits": float(width_bits),
        "zero_run_bits": float(zero_run_bits),
        "payload_bits": float(payload_bits),
        "sign_bits": float(sign_bits),
        "groups_total": float(groups_total),
        "nonzero_groups": float(nonzero_groups),
        "mean_selector": float(selector_sum / max(1, len(coeffs))),
        "canonical_policy": 1.0,
    }


def midpoint_scale(bit_plane_count: int) -> int:
    if bit_plane_count <= 0:
        return 0
    if bit_plane_count > len(NIKON_MIDPOINT_SCALE_TABLE):
        return 0
    return NIKON_MIDPOINT_SCALE_TABLE[bit_plane_count - 1]


def nikon_dequantize(q: np.ndarray, gcli: np.ndarray, gtli: int) -> np.ndarray:
    out = np.zeros_like(q, dtype=np.float64)
    for i, val in enumerate(q):
        if val == 0 or gcli[i // 4] <= gtli:
            continue
        bpc = int(gcli[i // 4] - gtli)
        if bpc < 1 or bpc > 15:
            continue
        mag = abs(int(val)) >> gtli
        shifted = (mag * midpoint_scale(bpc)) >> (16 - gtli)
        res = shifted << 4
        out[i] = -res if val < 0 else res
    return out


def nikon_quantize_nearest_dequant(flat: np.ndarray, gtli: int) -> tuple[np.ndarray, np.ndarray]:
    """Choose #826 entropy coefficients nearest to ``flat`` after dequant.

    The decoder determines the dequantization lattice once GTLI and each
    group's GCLI are known.  This canonical encoder inverts that lattice
    locally: for each 4-coefficient group it chooses magnitudes that land
    closest to the requested coefficient values, then lets the largest
    chosen magnitude define GCLI exactly as the decoder syntax does.
    """
    padded = flat
    if flat.size % 4:
        padded = np.pad(flat, (0, 4 - flat.size % 4))
    groups = padded.reshape(-1, 4)
    q_groups = np.zeros_like(groups, dtype=np.int64)
    gcli = np.zeros(groups.shape[0], dtype=np.int64)
    max_magnitude = (1 << 15) - 1
    for gi, group in enumerate(groups):
        signs = np.sign(group).astype(np.int64)
        target = np.abs(group.astype(np.float64))
        m = np.clip(
            np.rint(target / (float(1 << gtli) * float(1 << 4))).astype(np.int64),
            0,
            max_magnitude,
        )
        last_bpc = -1
        for _ in range(4):
            group_max = int(np.max(m))
            if group_max <= 0:
                bpc = 0
                m = np.zeros_like(m)
                break
            bpc = min(15, max(1, group_max.bit_length()))
            if bpc == last_bpc:
                break
            last_bpc = bpc
            scale = float(midpoint_scale(bpc))
            if scale <= 0:
                m = np.zeros_like(m)
                bpc = 0
                break
            ideal = target * float(1 << (16 - gtli)) / (scale * float(1 << 4))
            m = np.clip(np.rint(ideal).astype(np.int64), 0, (1 << bpc) - 1)
        group_max = int(np.max(m))
        if group_max <= 0:
            gcli[gi] = gtli
            continue
        bpc = min(15, max(1, group_max.bit_length()))
        q_groups[gi] = signs * (m << gtli)
        gcli[gi] = gtli + bpc
    return q_groups.reshape(-1)[: flat.size], gcli


def nikon_syntax_encode(coeffs: dict[str, np.ndarray], quality_shift: float) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    deq: dict[str, np.ndarray] = {}
    total_bits = 0
    header_bits = 0
    sig_bits = 0
    gcli_bits = 0
    data_bits = 0
    sign_bits = 0
    group_count = 0
    nonzero_groups = 0
    selected_bp_values: list[int] = []
    selected_br_values: list[int] = []
    keys = list(GTLI_ROWS.keys())
    # Canonical sweep layout: row_index selects a decoder-visible (Bp, Br)
    # GTLI row, and quality_bias shifts the row's GTLI values downward for
    # higher-quality encoder choices.  The shifted values still use the #826
    # GCLI/bit-plane/dequant syntax; only the camera's private row-selection
    # policy is replaced by an explicit sweep.
    qv = int(round(quality_shift))
    row_index = qv % len(keys)
    quality_bias = qv // len(keys)
    bp, br = keys[row_index]
    gtli_row = GTLI_ROWS[(bp, br)]
    for name, coeff in coeffs.items():
        sb = NIKON_COMPONENT_TO_SUBBAND[name]
        gtli = max(0, int(gtli_row[sb]))
        effective_gtli = max(0, gtli - quality_bias)
        flat = np.asarray(coeff, dtype=np.float64).reshape(-1)
        q, gcli = nikon_quantize_nearest_dequant(flat, effective_gtli)
        q_groups = q.reshape(-1, 4) if q.size % 4 == 0 else np.pad(q, (0, 4 - q.size % 4)).reshape(-1, 4)
        dq = nikon_dequantize(q_groups.reshape(-1), gcli, effective_gtli)[: flat.size].reshape(coeff.shape)
        deq[name] = dq

        groups = int(gcli.size)
        group_count += groups
        nz = int(np.count_nonzero(np.any(q_groups != 0, axis=1)))
        nonzero_groups += nz
        header_bits += 24 + 8 + 8 + 56 + 56
        sig_blocks = int(math.ceil(groups / 8))
        sig_bits += sig_blocks
        for start in range(0, groups, 8):
            block = gcli[start:start+8]
            if np.all(block == effective_gtli):
                continue
            for gv in block:
                gcli_bits += int(max(0, gv - effective_gtli)) + 1
        for gi, group in enumerate(q_groups):
            bitplanes = int(max(0, gcli[gi] - effective_gtli))
            data_bits += bitplanes * 4
            sign_bits += int(np.count_nonzero(group))
        selected_bp_values.append(bp)
        selected_br_values.append(br)
    total_bits = header_bits + sig_bits + gcli_bits + data_bits + sign_bits
    return deq, {
        "syntax_total_bits": float(total_bits),
        "header_bits": float(header_bits),
        "sig_bits": float(sig_bits),
        "gcli_bits": float(gcli_bits),
        "data_bits": float(data_bits),
        "sign_bits": float(sign_bits),
        "groups_total": float(group_count),
        "nonzero_groups": float(nonzero_groups),
        "mean_bp": float(statistics.mean(selected_bp_values)),
        "mean_br": float(statistics.mean(selected_br_values)),
        "canonical_policy": 1.0,
    }


CODECS = {
    SONY_CODEC: Codec(SONY_CODEC, sony_forward, sony_inverse, sony_syntax_encode, "base_step"),
    NIKON_CODEC: Codec(NIKON_CODEC, nikon_forward, nikon_inverse, nikon_syntax_encode, "gtli_row_index"),
}


def flatten_planes(planes: dict[str, np.ndarray]) -> np.ndarray:
    return np.concatenate([planes[k].ravel() for k in ("R", "G0", "G1", "B")])


def metric_summary(src: np.ndarray, rec: np.ndarray) -> dict[str, float]:
    diff = rec.astype(np.float64) - src.astype(np.float64)
    mse = float(np.mean(diff * diff))
    mae = float(np.mean(np.abs(diff)))
    max_abs = float(np.max(np.abs(diff))) if diff.size else 0.0
    psnr = float("inf") if mse == 0 else 10.0 * math.log10((RANGE * RANGE) / mse)
    return {"MSE": mse, "MAE": mae, "MAX": max_abs, "PSNR_raw": psnr}


def gradient_magnitude(x: np.ndarray) -> np.ndarray:
    arr = x.astype(np.float64)
    return np.sqrt(np.diff(arr, axis=1, append=arr[:, -1:]) ** 2 + np.diff(arr, axis=0, append=arr[-1:, :]) ** 2)


def laplacian_response(x: np.ndarray) -> np.ndarray:
    arr = x.astype(np.float64)
    return -4*arr + np.roll(arr, 1, 0) + np.roll(arr, -1, 0) + np.roll(arr, 1, 1) + np.roll(arr, -1, 1)


def gaussian_kernel1d(size: int = 11, sigma: float = 1.5) -> np.ndarray:
    coords = np.arange(size, dtype=np.float64) - (size - 1) / 2
    k = np.exp(-(coords * coords) / (2 * sigma * sigma))
    return k / np.sum(k)


SSIM_KERNEL = gaussian_kernel1d()


def separable_filter(x: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    if scipy_ndimage is not None:
        return scipy_ndimage.convolve1d(scipy_ndimage.convolve1d(x.astype(np.float64), kernel, axis=0, mode="reflect"), kernel, axis=1, mode="reflect")
    arr = x.astype(np.float64)
    for axis in (0, 1):
        radius = kernel.size // 2
        pad = [(0, 0)] * arr.ndim
        pad[axis] = (radius, radius)
        windows = np.lib.stride_tricks.sliding_window_view(np.pad(arr, pad, mode="reflect"), kernel.size, axis=axis)
        arr = np.tensordot(windows, kernel, axes=([-1], [0]))
    return arr


def ssim_components(src: np.ndarray, rec: np.ndarray) -> tuple[float, float]:
    x, y = src.astype(np.float64), rec.astype(np.float64)
    ux, uy = separable_filter(x, SSIM_KERNEL), separable_filter(y, SSIM_KERNEL)
    vx = np.maximum(separable_filter(x*x, SSIM_KERNEL) - ux*ux, 0)
    vy = np.maximum(separable_filter(y*y, SSIM_KERNEL) - uy*uy, 0)
    vxy = separable_filter(x*y, SSIM_KERNEL) - ux*uy
    c1, c2 = (0.01 * RANGE) ** 2, (0.03 * RANGE) ** 2
    luminance = (2*ux*uy+c1)/(ux*ux+uy*uy+c1)
    contrast_structure = (2*vxy+c2)/(vx+vy+c2)
    return float(np.mean(luminance * contrast_structure)), float(np.mean(contrast_structure))


def ssim_index(src: np.ndarray, rec: np.ndarray) -> float:
    return ssim_components(src, rec)[0]


def downsample2(x: np.ndarray) -> np.ndarray:
    h = (x.shape[0] // 2) * 2
    w = (x.shape[1] // 2) * 2
    if h < 2 or w < 2:
        return x
    return x[:h, :w].reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))


def ms_ssim_index(src: np.ndarray, rec: np.ndarray) -> float:
    weights = np.array([0.0448, 0.2856, 0.3001, 0.2363, 0.1333], dtype=np.float64)
    x, y = src.astype(np.float64), rec.astype(np.float64)
    mssim: list[float] = []
    mcs: list[float] = []
    for level in range(len(weights)):
        ssim_value, cs_value = ssim_components(x, y)
        mssim.append(max(ssim_value, 1e-12))
        mcs.append(max(cs_value, 1e-12))
        if level != len(weights) - 1:
            x = downsample2(x)
            y = downsample2(y)
    score = 1.0
    for value, weight in zip(mcs[:-1], weights[:-1]):
        score *= value ** float(weight)
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


def gmsd_index(src: np.ndarray, rec: np.ndarray) -> float:
    gs = prewitt_magnitude(src)
    gr = prewitt_magnitude(rec)
    c = 0.0026 * RANGE * RANGE
    gms = (2.0 * gs * gr + c) / (gs * gs + gr * gr + c)
    return float(np.std(gms))


def detail_metric_summary(source: dict[str, np.ndarray], recon: dict[str, np.ndarray]) -> dict[str, float]:
    grad_s, grad_r, lap_s, lap_r = [], [], [], []
    ssim_scores, ms_ssim_scores, gmsd_scores = [], [], []
    for plane in ("R", "G0", "G1", "B"):
        grad_s.append(gradient_magnitude(source[plane]).ravel())
        grad_r.append(gradient_magnitude(recon[plane]).ravel())
        lap_s.append(laplacian_response(source[plane]).ravel())
        lap_r.append(laplacian_response(recon[plane]).ravel())
        ssim_scores.append(ssim_index(source[plane], recon[plane]))
        ms_ssim_scores.append(ms_ssim_index(source[plane], recon[plane]))
        gmsd_scores.append(gmsd_index(source[plane], recon[plane]))
    gs, gr = np.concatenate(grad_s), np.concatenate(grad_r)
    ls, lr = np.concatenate(lap_s), np.concatenate(lap_r)
    gd = gr - gs
    grad_mse = float(np.mean(gd * gd))
    grad_range = max(float(np.percentile(gs, 99.5)), 1.0)
    return {
        "grad_mae": float(np.mean(np.abs(gd))),
        "grad_psnr": float("inf") if grad_mse == 0 else 10.0 * math.log10((grad_range * grad_range) / grad_mse),
        "laplacian_mae": float(np.mean(np.abs(lr - ls))),
        "ssim_mean": float(statistics.mean(ssim_scores)),
        "ms_ssim_mean": float(statistics.mean(ms_ssim_scores)),
        "gmsd_mean": float(statistics.mean(gmsd_scores)),
    }


def collect_metrics(source: dict[str, np.ndarray], result: EncodeResult) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    splits = {"whole": (flatten_planes(source), flatten_planes(result.recon))}
    for plane in ("R", "G0", "G1", "B"):
        splits[plane] = (source[plane].ravel(), result.recon[plane].ravel())
    for split, (src, rec) in splits.items():
        for metric, value in metric_summary(src, rec).items():
            rows.append({"codec": result.codec, "source_id": result.source_id, "target_bpp": f"{result.target_bpp:.6f}", "actual_bpp": f"{result.actual_bpp:.9f}", "metric": metric, "value": f"{value:.9f}", "split": split, "knob": f"{result.knob:.9f}", "encode_ms": f"{result.encode_ms:.3f}"})
    for metric, value in detail_metric_summary(source, result.recon).items():
        rows.append({"codec": result.codec, "source_id": result.source_id, "target_bpp": f"{result.target_bpp:.6f}", "actual_bpp": f"{result.actual_bpp:.9f}", "metric": metric, "value": f"{value:.9f}", "split": "detail", "knob": f"{result.knob:.9f}", "encode_ms": f"{result.encode_ms:.3f}"})
    return rows


def encode_canonical(planes: dict[str, np.ndarray], source_id: str, codec: Codec, target_bpp: float, levels: int) -> EncodeResult:
    start = time.perf_counter()
    comps = codec.forward(planes)
    coeffs, sizes = transform_components(comps, levels)
    pixel_count = int(next(iter(planes.values())).size * 4)

    if codec.name == SONY_CODEC:
        lo, hi = 0.25, 4096.0
        for _ in range(24):
            mid = math.sqrt(lo * hi)
            deq, syntax = codec.syntax_encode(coeffs, mid)
            rate = syntax["syntax_total_bits"] / pixel_count
            if rate > target_bpp:
                lo = mid
            else:
                hi = mid
        knob = hi
    else:
        # Nikon #826 exposes finite GTLI rows.  Choose the closest row index to target bpp.
        best = None
        for idx in range(len(GTLI_ROWS) * 6):
            deq, syntax = codec.syntax_encode(coeffs, float(idx))
            rate = syntax["syntax_total_bits"] / pixel_count
            score = abs(rate - target_bpp)
            if best is None or score < best[0]:
                best = (score, idx, rate, deq, syntax)
        assert best is not None
        knob = float(best[1])

    deq_coeffs, syntax = codec.syntax_encode(coeffs, knob)
    actual_bpp = syntax["syntax_total_bits"] / pixel_count
    recon_comps = inverse_transform_components(deq_coeffs, sizes)
    recon = codec.inverse(recon_comps)
    return EncodeResult(codec.name, source_id, target_bpp, actual_bpp, knob, (time.perf_counter() - start) * 1000.0, recon, syntax)


def finish_precomputed_encode(
    planes: dict[str, np.ndarray],
    source_id: str,
    codec: Codec,
    target_bpp: float,
    coeffs: dict[str, np.ndarray],
    sizes: dict[str, list[tuple[int, int]]],
    knob: float,
    encode_ms: float,
    deq_coeffs: dict[str, np.ndarray] | None = None,
    syntax: dict[str, float] | None = None,
) -> EncodeResult:
    if deq_coeffs is None or syntax is None:
        deq_coeffs, syntax = codec.syntax_encode(coeffs, knob)
    actual_bpp = syntax["syntax_total_bits"] / int(next(iter(planes.values())).size * 4)
    recon_comps = inverse_transform_components(deq_coeffs, sizes)
    recon = codec.inverse(recon_comps)
    return EncodeResult(codec.name, source_id, target_bpp, actual_bpp, knob, encode_ms, recon, syntax)


def encode_sony_precomputed(
    planes: dict[str, np.ndarray],
    source_id: str,
    codec: Codec,
    target_bpp: float,
    coeffs: dict[str, np.ndarray],
    sizes: dict[str, list[tuple[int, int]]],
) -> EncodeResult:
    start = time.perf_counter()
    pixel_count = int(next(iter(planes.values())).size * 4)
    lo, hi = 0.25, 4096.0
    for _ in range(24):
        mid = math.sqrt(lo * hi)
        _deq, syntax = codec.syntax_encode(coeffs, mid)
        rate = syntax["syntax_total_bits"] / pixel_count
        if rate > target_bpp:
            lo = mid
        else:
            hi = mid
    knob = hi
    deq_coeffs, syntax = codec.syntax_encode(coeffs, knob)
    return finish_precomputed_encode(
        planes,
        source_id,
        codec,
        target_bpp,
        coeffs,
        sizes,
        knob,
        (time.perf_counter() - start) * 1000.0,
        deq_coeffs,
        syntax,
    )


def encode_nikon_targets_precomputed(
    planes: dict[str, np.ndarray],
    source_id: str,
    codec: Codec,
    targets: list[float],
    coeffs: dict[str, np.ndarray],
    sizes: dict[str, list[tuple[int, int]]],
) -> list[EncodeResult]:
    start = time.perf_counter()
    pixel_count = int(next(iter(planes.values())).size * 4)
    best: dict[float, tuple[float, float, dict[str, np.ndarray], dict[str, float]]] = {}
    for idx in range(len(GTLI_ROWS) * 6):
        knob = float(idx)
        deq, syntax = codec.syntax_encode(coeffs, knob)
        rate = syntax["syntax_total_bits"] / pixel_count
        for target in targets:
            score = abs(rate - target)
            if target not in best or score < best[target][0]:
                best[target] = (score, knob, deq, syntax)
    elapsed_ms = (time.perf_counter() - start) * 1000.0 / max(1, len(targets))
    out: list[EncodeResult] = []
    for target in targets:
        if target not in best:
            raise RuntimeError(f"no Nikon candidate generated for target {target}")
        _score, knob, deq, syntax = best[target]
        out.append(
            finish_precomputed_encode(
                planes,
                source_id,
                codec,
                target,
                coeffs,
                sizes,
                knob,
                elapsed_ms,
                deq,
                syntax,
            )
        )
    return out


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"no rows for {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_summaries(
    out_dir: Path,
    encode_rows: list[dict[str, object]],
    metric_rows: list[dict[str, object]],
) -> None:
    targets = sorted({float(r["target_bpp"]) for r in metric_rows})
    target_rows = []
    for target in targets:
        for metric in ["PSNR_raw", "MAE", "MAX", "grad_psnr", "laplacian_mae", "ssim_mean", "ms_ssim_mean", "gmsd_mean"]:
            vals_n, vals_s = [], []
            for r in metric_rows:
                if float(r["target_bpp"]) != target or r["metric"] != metric:
                    continue
                if r["split"] not in {"whole", "detail"}:
                    continue
                if r["codec"] == NIKON_CODEC:
                    vals_n.append(float(r["value"]))
                elif r["codec"] == SONY_CODEC:
                    vals_s.append(float(r["value"]))
            if vals_n and vals_s:
                med_s = statistics.median(vals_s)
                med_n = statistics.median(vals_n)
                target_rows.append({
                    "target_bpp": f"{target:.6f}",
                    "metric": metric,
                    "median_sony": f"{med_s:.9f}",
                    "median_nikon": f"{med_n:.9f}",
                    "median_sony_minus_nikon": f"{med_s - med_n:.9f}",
                    "n_sony": len(vals_s),
                    "n_nikon": len(vals_n),
                    "summary_scope": "same requested target, not same actual bpp",
                })
    write_csv(out_dir / "target_request_summary.csv", target_rows)

    metric_lookup: dict[tuple[str, str, str], list[float]] = {}
    for r in metric_rows:
        key = (str(r["target_bpp"]), str(r["codec"]), f"{r['metric']}|{r['split']}")
        metric_lookup.setdefault(key, []).append(float(r["value"]))

    rate_rows = []
    for target in sorted({str(r["target_bpp"]) for r in encode_rows}):
        for codec_name in sorted({str(r["codec"]) for r in encode_rows}):
            rows_for_key = [r for r in encode_rows if str(r["target_bpp"]) == target and str(r["codec"]) == codec_name]
            if not rows_for_key:
                continue
            rates = [float(r["actual_bpp"]) for r in rows_for_key]
            out = {
                "target_bpp": target,
                "codec": codec_name,
                "actual_bpp_min": f"{min(rates):.9f}",
                "actual_bpp_median": f"{statistics.median(rates):.9f}",
                "actual_bpp_max": f"{max(rates):.9f}",
                "n": len(rates),
            }
            for metric_key in [
                "PSNR_raw|whole",
                "MAE|whole",
                "MAX|whole",
                "grad_psnr|detail",
                "laplacian_mae|detail",
                "ssim_mean|detail",
                "ms_ssim_mean|detail",
                "gmsd_mean|detail",
            ]:
                vals = metric_lookup.get((target, codec_name, metric_key), [])
                out[f"median_{metric_key.replace('|', '_')}"] = f"{statistics.median(vals):.9f}" if vals else ""
            rate_rows.append(out)
    write_csv(out_dir / "rate_summary.csv", rate_rows)


def evaluate_source_task(
    source_id: str,
    planes: dict[str, np.ndarray],
    targets: list[float],
    levels: int,
) -> tuple[str, list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    encode_rows: list[dict[str, object]] = []
    metric_rows: list[dict[str, object]] = []
    syntax_rows: list[dict[str, object]] = []
    roundtrip_rows: list[dict[str, object]] = []

    for codec in CODECS.values():
        comps = codec.forward(planes)
        recon0 = codec.inverse(comps)
        stats = metric_summary(flatten_planes(planes), flatten_planes(recon0))
        roundtrip_rows.append({"codec": codec.name, "source_id": source_id, **{k: f"{v:.9f}" for k, v in stats.items()}})

    for codec in CODECS.values():
        comps = codec.forward(planes)
        coeffs, sizes = transform_components(comps, levels)
        if codec.name == NIKON_CODEC:
            results = encode_nikon_targets_precomputed(planes, source_id, codec, targets, coeffs, sizes)
        else:
            results = [
                encode_sony_precomputed(planes, source_id, codec, target, coeffs, sizes)
                for target in targets
            ]
        for result in results:
            encode_rows.append({
                "codec": result.codec,
                "source_id": source_id,
                "target_bpp": f"{result.target_bpp:.6f}",
                "actual_bpp": f"{result.actual_bpp:.9f}",
                "knob_name": codec.knob_name,
                "knob": f"{result.knob:.9f}",
                "encode_ms": f"{result.encode_ms:.3f}",
            })
            syntax_rows.append({
                "codec": result.codec,
                "source_id": source_id,
                "target_bpp": f"{result.target_bpp:.6f}",
                **{k: f"{v:.9f}" for k, v in result.syntax.items()},
            })
            metric_rows.extend(collect_metrics(planes, result))

    return source_id, encode_rows, metric_rows, syntax_rows, roundtrip_rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=Path("out/strict_824_826_math_eval_20260603"))
    ap.add_argument("--width", type=int, default=256)
    ap.add_argument("--height", type=int, default=256)
    ap.add_argument("--levels", type=int, default=3)
    ap.add_argument("--targets", default="1.5,2.0,2.5,3.0,4.0,5.0")
    ap.add_argument("--seed", type=int, default=20260603)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1), help="parallel scene workers")
    ns = ap.parse_args()

    if ns.width % 2 or ns.height % 2:
        raise ValueError("width and height must be even")
    targets = [float(x) for x in ns.targets.split(",") if x.strip()]
    rng = np.random.default_rng(ns.seed)
    sources = {scene: generate_scene(scene, ns.height // 2, ns.width // 2, rng) for scene in SCENES}
    ns.out_dir.mkdir(parents=True, exist_ok=True)
    max_windows_workers = 61 if os.name == "nt" else len(sources)
    jobs = max(1, min(ns.jobs, len(sources), max_windows_workers))

    encode_rows: list[dict[str, object]] = []
    metric_rows: list[dict[str, object]] = []
    syntax_rows: list[dict[str, object]] = []
    roundtrip_rows: list[dict[str, object]] = []

    if jobs <= 1:
        results = [evaluate_source_task(source_id, planes, targets, ns.levels) for source_id, planes in sources.items()]
    else:
        results_by_source: dict[str, tuple[str, list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]] = {}
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = {
                pool.submit(evaluate_source_task, source_id, planes, targets, ns.levels): source_id
                for source_id, planes in sources.items()
            }
            for future in as_completed(futures):
                result = future.result()
                results_by_source[result[0]] = result
                print(f"finished {result[0]}", flush=True)
        results = [results_by_source[source_id] for source_id in sources]

    for _source_id, enc, met, syn, rt in results:
        encode_rows.extend(enc)
        metric_rows.extend(met)
        syntax_rows.extend(syn)
        roundtrip_rows.extend(rt)

    write_csv(ns.out_dir / "encodes.csv", encode_rows)
    write_csv(ns.out_dir / "metrics.csv", metric_rows)
    write_csv(ns.out_dir / "syntax_summary.csv", syntax_rows)
    write_csv(ns.out_dir / "roundtrip_audit.csv", roundtrip_rows)
    write_summaries(ns.out_dir, encode_rows, metric_rows)
    manifest = {
        "kind": "strict #824/#826 decoder-visible math evaluation",
        "seed": ns.seed,
        "width": ns.width,
        "height": ns.height,
        "levels": ns.levels,
        "targets_bpp": targets,
        "source_count": len(sources),
        "codec_names": [NIKON_CODEC, SONY_CODEC],
        "decoder_visible_only": True,
        "old_proxy_outputs_used": False,
        "figure_outputs_generated": False,
        "jobs": jobs,
        "unknown_rd_policy": "canonical sweep over decoder-visible quantization/selector/GTLI controls; not claimed as private camera RD search",
        "sony_basis": "#824 packet selectors, adaptive width, zero-run, 4-lane magnitude/sign syntax, final green/RB residual relation",
        "nikon_basis": "#826 GTLI/GCLI, bit-plane magnitude/sign syntax, dequantization, step1/step2 Bayer relation",
        "nikon_lut_column": "sample14",
        "nikon_midpoint_scale": "exact kMidpointScaleTable from LibRaw #826 nikon_he_dequantize.h",
        "nikon_quantizer": "nearest decoder dequantization lattice value for each 4-coefficient GCLI group under the selected GTLI row",
        "summary_files": {
            "target_request_summary": str(ns.out_dir / "target_request_summary.csv"),
            "rate_summary": str(ns.out_dir / "rate_summary.csv"),
        },
        "row_counts": {"encodes": len(encode_rows), "metrics": len(metric_rows), "syntax_summary": len(syntax_rows), "roundtrip_audit": len(roundtrip_rows)},
    }
    (ns.out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
