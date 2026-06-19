#!/usr/bin/env python3
"""Pure Python path for the ARW6/LLVC3 sample stream I am reconstructing."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from llvc3_bitstream_probe import find_llvc_streams, find_raw_subifd
from llvc3_entropy import decode_packet_arrays, integrate_type1_coefficients
from llvc3_math import (
    INTERNAL_BIAS,
    apply_sample_lut,
    clamp_signed_to_code_range,
    finalize_llvc3_color_planes,
    recombine_rggb,
    signed_to_sample,
    synthesize_llvc3_final_green,
    synthesize_llvc3_guard_group1,
    synthesize_llvc3_guard_group2,
    synthesize_llvc3_guard_group3,
    synthesize_llvc3_level_stride,
)
from recombine_llvc_planes import robust_preview, write_dng


DEFAULT_SAMPLE_LUT = Path(__file__).with_name("data") / "sony_llvc3_static_lut4096_padded_u16.bin"
DEFAULT_TILE_EDGE_ROWS = 4
DEFAULT_TILE_EDGE_MODE = "average"


def align_up(value: int, multiple: int) -> int:
    return ((value + multiple - 1) // multiple) * multiple


def stats(arr: np.ndarray) -> dict[str, Any]:
    return {
        "shape": list(arr.shape),
        "dtype": str(arr.dtype),
        "min": int(arr.min()),
        "max": int(arr.max()),
        "mean": float(arr.mean()),
        "std": float(arr.std()),
    }


def compare_plane(pred: np.ndarray, native_path: Path, shape: tuple[int, int]) -> dict[str, Any]:
    native = np.fromfile(native_path, dtype="<u2")
    if native.size != shape[0] * shape[1]:
        raise ValueError(f"{native_path} has {native.size} samples, expected {shape[0] * shape[1]}")
    native = native.reshape(shape)
    diff = pred.astype(np.int32) - native.astype(np.int32)
    nonzero = np.flatnonzero(diff)
    out: dict[str, Any] = {
        "native_path": str(native_path),
        "exact": bool(nonzero.size == 0),
        "nonzero": int(nonzero.size),
        "max_abs": int(np.max(np.abs(diff))) if diff.size else 0,
    }
    if nonzero.size:
        y, x = np.unravel_index(int(nonzero[0]), diff.shape)
        out["first_mismatch"] = {
            "y": int(y),
            "x": int(x),
            "pred": int(pred[y, x]),
            "native": int(native[y, x]),
            "diff": int(diff[y, x]),
        }
    return out


def load_sample_lut(path: Path) -> np.ndarray:
    """Load a 16-bit code-to-sample LUT from binary or two-column text."""

    suffix = path.suffix.lower()
    if suffix in {".bin", ".raw", ".lut"}:
        lut = np.fromfile(path, dtype="<u2")
    else:
        values: list[int] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(",", "\t").split()
            if len(parts) < 2:
                continue
            if parts[0].lower() in {"code", "input", "src"}:
                continue
            values.append(int(parts[1]))
        lut = np.asarray(values, dtype=np.uint16)
    if lut.size < 65536:
        pad_value = int(lut[-1]) if lut.size else 0
        lut = np.pad(lut, (0, 65536 - lut.size), constant_values=pad_value)
    return lut[:65536].astype(np.uint16)


def build_inverse_sample_lut(lut: np.ndarray) -> np.ndarray:
    """Map Sony LUT-expanded samples back to the nearest LLVC3 code value."""

    table = np.asarray(lut[:4096], dtype=np.int32).reshape(-1)
    if table.size == 0:
        raise ValueError("sample LUT is empty")
    inv = np.zeros(int(table.max()) + 1, dtype=np.int32)
    idx = 0
    for sample in range(inv.size):
        while idx + 1 < table.size and abs(int(table[idx + 1]) - sample) <= abs(int(table[idx]) - sample):
            idx += 1
        inv[sample] = idx
    return inv


def inverse_lut_samples(samples: np.ndarray, inv_lut: np.ndarray) -> np.ndarray:
    return inv_lut[np.clip(np.asarray(samples, dtype=np.int32), 0, inv_lut.size - 1)]


def combine_tiled_arrays_by_position(
    tiles: list[np.ndarray],
    streams: list[Any],
    axis_name: str,
    x_divisor: int = 1,
    fill: int = 0,
) -> np.ndarray:
    if len(tiles) != len(streams):
        raise ValueError(f"{axis_name}: {len(tiles)} tiles do not match {len(streams)} streams")
    ys = sorted({stream.tile_y for stream in streams})
    y_rank = {y: i for i, y in enumerate(ys)}
    placements: list[tuple[int, int, np.ndarray]] = []
    max_x = 0
    max_y = 0
    for tile, stream in zip(tiles, streams):
        x = stream.tile_x // x_divisor
        # Tile y coordinates are in raw-strip pixels, while c0/c1/c2 arrays
        # are half-height. Rank-based placement works for both raw mosaics and
        # component planes after each tile has been cropped to its own height.
        y = y_rank[stream.tile_y] * tile.shape[0]
        placements.append((x, y, tile))
        max_x = max(max_x, x + tile.shape[1])
        max_y = max(max_y, y + tile.shape[0])
    out = np.full((max_y, max_x), fill, dtype=tiles[0].dtype)
    for x, y, tile in placements:
        out[y : y + tile.shape[0], x : x + tile.shape[1]] = tile
    return out


def _blend_signed_rows(a: np.ndarray, b: np.ndarray, i: int, total: int) -> np.ndarray:
    """Integer row blend used only for tile edge mitigation."""

    ai = np.asarray(a, dtype=np.int64)
    bi = np.asarray(b, dtype=np.int64)
    return (((total - i) * ai + i * bi + total // 2) // total).astype(np.int32)


def mitigate_tiled_signed_edge_rows(
    signed_tiles: list[tuple[np.ndarray, np.ndarray, np.ndarray]],
    streams: list[Any],
    edge_rows: int,
    mode: str,
) -> tuple[list[tuple[np.ndarray, np.ndarray, np.ndarray]], dict[str, Any]]:
    """Replace currently unreversed LLVC3 guard rows at tiled stream borders.

    The pure wavelet path is exact against Sony's native output through the
    tile interior, but the first/last few half-height rows still use imperfect
    guard-row prediction. Keep that repair explicit and reversible instead of
    hiding it inside the core synthesis helpers.
    """

    report: dict[str, Any] = {
        "enabled": False,
        "edge_half_rows": int(edge_rows),
        "mode": mode,
        "method": "copy outer image edges; bridge vertical tile boundaries in signed LLVC3 domain",
        "planes": {},
    }
    if mode not in {"average", "linear", "copy"}:
        raise ValueError(f"unknown tile edge mitigation mode {mode!r}")
    if edge_rows <= 0:
        report["reason"] = "edge_rows <= 0"
        return signed_tiles, report
    if len(signed_tiles) <= 1:
        report["reason"] = "single LLVC3 stream"
        return signed_tiles, report

    patched_by_plane: list[list[np.ndarray]] = []
    plane_names = ("c0", "c1", "c2")
    for plane_index, plane_name in enumerate(plane_names):
        planes = [tile[plane_index].copy() for tile in signed_tiles]
        rows_touched = 0
        vertical_boundaries = 0
        outer_top_edges = 0
        outer_bottom_edges = 0

        by_x: dict[int, list[int]] = {}
        for i, stream in enumerate(streams):
            by_x.setdefault(int(stream.tile_x), []).append(i)

        for indices in by_x.values():
            indices.sort(key=lambda i: int(streams[i].tile_y))
            for pos, tile_index in enumerate(indices):
                plane = planes[tile_index]
                n = min(edge_rows, max(0, (plane.shape[0] - 1) // 2))
                if n <= 0:
                    continue
                if pos == 0:
                    plane[:n] = plane[n : n + 1]
                    rows_touched += n
                    outer_top_edges += 1
                if pos == len(indices) - 1:
                    plane[-n:] = plane[-n - 1 : -n]
                    rows_touched += n
                    outer_bottom_edges += 1

            for top_index, bottom_index in zip(indices, indices[1:]):
                top_plane = planes[top_index]
                bottom_plane = planes[bottom_index]
                n = min(edge_rows, max(0, (top_plane.shape[0] - 1) // 2), max(0, (bottom_plane.shape[0] - 1) // 2))
                if n <= 0:
                    continue
                if top_plane.shape[1] != bottom_plane.shape[1]:
                    raise ValueError(
                        f"cannot bridge {plane_name} tile edge with widths "
                        f"{top_plane.shape[1]} and {bottom_plane.shape[1]}"
                    )
                top_anchor = top_plane[-n - 1].copy()
                bottom_anchor = bottom_plane[n].copy()
                total = 2 * n + 1
                if mode == "average":
                    average = _blend_signed_rows(top_anchor, bottom_anchor, 1, 2)
                    top_plane[-n:] = average
                    bottom_plane[:n] = average
                elif mode == "copy":
                    top_plane[-n:] = top_anchor
                    bottom_plane[:n] = bottom_anchor
                else:
                    for k in range(n):
                        top_plane[-n + k] = _blend_signed_rows(top_anchor, bottom_anchor, k + 1, total)
                        bottom_plane[k] = _blend_signed_rows(top_anchor, bottom_anchor, n + k + 1, total)
                rows_touched += 2 * n
                vertical_boundaries += 1

        patched_by_plane.append(planes)
        report["planes"][plane_name] = {
            "rows_touched": rows_touched,
            "outer_top_edges": outer_top_edges,
            "outer_bottom_edges": outer_bottom_edges,
            "vertical_boundaries": vertical_boundaries,
        }

    patched = [
        (patched_by_plane[0][i], patched_by_plane[1][i], patched_by_plane[2][i])
        for i in range(len(signed_tiles))
    ]
    report["enabled"] = True
    return patched, report


def find_native_edge_plane(path: Path, stem: str, tile_index: int, plane_name: str) -> Path:
    candidates = [
        path / f"native_tile{tile_index}_v0_{plane_name}.bin",
        path / f"{stem}_native_tile{tile_index}_v0_{plane_name}.bin",
        path / f"{stem}_tile{tile_index}_v0_{plane_name}.bin",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    tried = ", ".join(str(p) for p in candidates)
    raise FileNotFoundError(f"native edge plane for tile {tile_index} {plane_name} not found; tried {tried}")


def apply_native_edge_oracle_rows(
    signed_tiles: list[tuple[np.ndarray, np.ndarray, np.ndarray]],
    streams: list[Any],
    native_edge_dir: Path,
    stem: str,
    edge_rows: int,
    sample_lut: np.ndarray,
) -> tuple[list[tuple[np.ndarray, np.ndarray, np.ndarray]], dict[str, Any]]:
    """Replace tile guard rows with Sony-native decoded rows for validation.

    This is intentionally an oracle path: it consumes output from Sony's native
    LLVCDecoder and only patches the rows where the pure Python guard-row
    synthesis is known not to match yet.
    """

    report: dict[str, Any] = {
        "enabled": False,
        "edge_half_rows": int(edge_rows),
        "native_edge_dir": str(native_edge_dir),
        "method": "replace first/last tile rows from Sony-native LLVCDecoder dumps",
        "tiles": [],
    }
    if edge_rows <= 0:
        report["reason"] = "edge_rows <= 0"
        return signed_tiles, report
    if not native_edge_dir.exists():
        raise FileNotFoundError(f"native edge dir does not exist: {native_edge_dir}")

    inv_lut = build_inverse_sample_lut(sample_lut)
    plane_names = ("c0", "c1", "c2")
    patched: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for tile_index, (stream, planes_in) in enumerate(zip(streams, signed_tiles)):
        tile_planes: list[np.ndarray] = []
        tile_report: dict[str, Any] = {"tile_index": tile_index, "planes": {}}
        for plane_index, plane_name in enumerate(plane_names):
            plane = planes_in[plane_index].copy()
            expected_shape = plane.shape
            native_path = find_native_edge_plane(native_edge_dir, stem, int(stream.index), plane_name)
            native_samples = np.fromfile(native_path, dtype="<u2")
            if native_samples.size != expected_shape[0] * expected_shape[1]:
                raise ValueError(
                    f"{native_path} has {native_samples.size} samples, "
                    f"expected {expected_shape[0] * expected_shape[1]} for {expected_shape}"
                )
            native_code = inverse_lut_samples(native_samples.reshape(expected_shape), inv_lut)
            native_signed = native_code.astype(np.int32) - INTERNAL_BIAS
            n = min(edge_rows, plane.shape[0])
            plane[:n] = native_signed[:n]
            plane[-n:] = native_signed[-n:]
            tile_planes.append(plane)
            tile_report["planes"][plane_name] = {
                "native_path": str(native_path),
                "shape": list(expected_shape),
                "rows_replaced": int(2 * n),
            }
        patched.append((tile_planes[0], tile_planes[1], tile_planes[2]))
        report["tiles"].append(tile_report)

    report["enabled"] = True
    return patched, report


def decode_signed_planes(
    arw: Path, blue_edge_fix: bool = True, stream_index: int = 0
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    """Decode to signed internal c0/c1/c2 planes; LUT mapping happens later."""

    timings: dict[str, float] = {}
    packet_meta: dict[str, Any] = {}
    blue_fix_pixels = 0

    t0 = time.perf_counter()
    raw_info, strip = find_raw_subifd(arw)
    streams = find_llvc_streams(strip)
    if not streams:
        raise ValueError("no LLVC3 stream found in ARW6 raw strip")
    if stream_index < 0 or stream_index >= len(streams):
        raise ValueError(f"stream_index {stream_index} out of range for {len(streams)} LLVC3 streams")
    header = streams[stream_index].header
    coded_height = header.logical_height
    padded_height = align_up(coded_height, 16)
    guarded_height = coded_height != padded_height
    low_rows = padded_height // 16
    low_start = 1 if guarded_height else 0
    low_count = low_rows - low_start

    g0, meta = decode_packet_arrays(arw, 0, 0, stream_index=stream_index)
    packet_meta["g0i0"] = meta
    green = integrate_type1_coefficients(g0[0][low_start : low_start + low_count], 2048) - 2048

    r0, meta = decode_packet_arrays(arw, 0, 1, stream_index=stream_index)
    packet_meta["g0i1"] = meta
    red_residual = integrate_type1_coefficients(r0[0][low_start : low_start + low_count], 0)

    b0, meta = decode_packet_arrays(arw, 0, 2, stream_index=stream_index)
    packet_meta["g0i2"] = meta
    blue_residual = integrate_type1_coefficients(b0[0][low_start : low_start + low_count], 0)
    timings["group0_entropy_and_integrate_s"] = time.perf_counter() - t0

    for group, edge_rows in ((1, 0), (2, 1), (3, 2)):
        t_group = time.perf_counter()
        old_green = green
        old_red_residual = red_residual
        old_blue_residual = blue_residual

        planes, meta = decode_packet_arrays(arw, group, 0, stream_index=stream_index)
        packet_meta[f"g{group}i0"] = meta
        if guarded_height:
            if group == 1:
                green = synthesize_llvc3_guard_group1(old_green, planes[0], planes[1], planes[2])
            elif group == 2:
                green = synthesize_llvc3_guard_group2(old_green, planes[0], planes[1], planes[2])
            else:
                green = synthesize_llvc3_guard_group3(old_green, planes[0], planes[1], planes[2])
        else:
            green = synthesize_llvc3_level_stride(old_green, planes[0], planes[1], planes[2], edge_rows)

        planes, meta = decode_packet_arrays(arw, group, 1, stream_index=stream_index)
        packet_meta[f"g{group}i1"] = meta
        edge_mode = "odd" if group == 3 else "even"
        if guarded_height:
            if group == 1:
                red_residual = synthesize_llvc3_guard_group1(old_red_residual, planes[0], planes[1], planes[2])
            elif group == 2:
                red_residual = synthesize_llvc3_guard_group2(old_red_residual, planes[0], planes[1], planes[2])
            else:
                red_residual = synthesize_llvc3_guard_group3(
                    old_red_residual, planes[0], planes[1], planes[2], edge_mode=edge_mode
                )
        else:
            red_residual = synthesize_llvc3_level_stride(
                old_red_residual, planes[0], planes[1], planes[2], edge_rows, edge_mode=edge_mode
            )

        planes, meta = decode_packet_arrays(arw, group, 2, stream_index=stream_index)
        packet_meta[f"g{group}i2"] = meta
        if guarded_height:
            if group == 1:
                blue_residual = synthesize_llvc3_guard_group1(old_blue_residual, planes[0], planes[1], planes[2])
            elif group == 2:
                blue_residual = synthesize_llvc3_guard_group2(old_blue_residual, planes[0], planes[1], planes[2])
            else:
                blue_residual = synthesize_llvc3_guard_group3(
                    old_blue_residual, planes[0], planes[1], planes[2], edge_mode=edge_mode
                )
        else:
            blue_residual = synthesize_llvc3_level_stride(
                old_blue_residual, planes[0], planes[1], planes[2], edge_rows, edge_mode=edge_mode
            )
        timings[f"group{group}_entropy_and_synthesis_s"] = time.perf_counter() - t_group

    t_final = time.perf_counter()
    g4, meta = decode_packet_arrays(arw, 4, 0, stream_index=stream_index)
    packet_meta["g4i0"] = meta
    full_green = synthesize_llvc3_final_green(green, g4[0], top_rows=2 if guarded_height else 4)
    v1_red = green + 2 * red_residual
    v1_blue = green + 2 * blue_residual
    c0, c1, c2 = finalize_llvc3_color_planes(green, v1_red, v1_blue, full_green)
    crop_top = 0
    crop_bottom = 0
    if c0.shape[0] != header.coded_half_height:
        extra_rows = c0.shape[0] - header.coded_half_height
        if extra_rows < 0:
            raise ValueError(
                f"stream {stream_index} decoded only {c0.shape[0]} half-height rows, "
                f"expected {header.coded_half_height}"
            )
        crop_top = 0 if guarded_height else extra_rows // 2
        crop_bottom = extra_rows - crop_top
        bottom = crop_top + header.coded_half_height
        c0 = c0[crop_top:bottom]
        c1 = c1[crop_top:bottom]
        c2 = c2[crop_top:bottom]
    timings["group4_entropy_and_final_cfa_s"] = time.perf_counter() - t_final

    meta_out = {
        "timings": timings,
        "packet_meta": packet_meta,
        "coded_decode_size": {
            "width": header.coded_width,
            "height": coded_height,
            "padded_height": padded_height,
            "half_rows": header.coded_half_height,
            "crop_top_half_rows": crop_top,
            "crop_bottom_half_rows": int(padded_height // 2 - crop_top - header.coded_half_height)
            if guarded_height
            else crop_bottom,
            "guarded_height_path": guarded_height,
            "group0_low_start_row": low_start,
        },
        "blue_residual_bottom_edge_fix_pixels": blue_fix_pixels,
        "signed_plane_stats": {"c0": stats(c0), "c1": stats(c1), "c2": stats(c2)},
    }
    return c0, c1, c2, meta_out


def decode_to_files(
    arw: Path,
    out_dir: Path,
    verify_native_prefix: Path | None = None,
    sample_lut_path: Path | None = None,
    blue_edge_fix: bool = True,
    black: int = 1024,
    white: int = 16383,
    shifted_black: int = 512,
    use_sample_lut: bool = True,
    tile_edge_rows: int = DEFAULT_TILE_EDGE_ROWS,
    tile_edge_mode: str = DEFAULT_TILE_EDGE_MODE,
    native_edge_dir: Path | None = None,
    native_edge_rows: int = 6,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = arw.stem

    raw_info, strip = find_raw_subifd(arw)
    streams = find_llvc_streams(strip)
    if not streams:
        raise ValueError("no LLVC3 stream found in ARW6 raw strip")
    if any(s.header.component_count != 3 for s in streams):
        raise ValueError(f"unexpected ARW6/LLVC3 component count in streams: {streams}")
    header = streams[0].header
    if raw_info.width % 16 or raw_info.height % 16:
        raise ValueError(f"decoder expects dimensions divisible by 16, got {raw_info.width}x{raw_info.height}")
    for s in streams:
        if s.tile_x < 0 or s.tile_y < 0:
            raise ValueError(f"stream {s.index} has negative tile position {s.tile_x},{s.tile_y}")
        if s.tile_width != s.header.coded_width or s.tile_height != s.header.logical_height:
            raise ValueError(
                f"stream {s.index} tile entry {s.tile_width}x{s.tile_height} "
                f"does not match coded {s.header.coded_width}x{s.header.logical_height}"
            )
        if s.tile_x + s.tile_width > raw_info.width or s.tile_y + s.tile_height > raw_info.height:
            raise ValueError(
                f"stream {s.index} tile {s.tile_x},{s.tile_y} "
                f"{s.tile_width}x{s.tile_height} exceeds raw {raw_info.width}x{raw_info.height}"
            )
    ys = sorted({s.tile_y for s in streams})
    for y in ys:
        row_streams = sorted((s for s in streams if s.tile_y == y), key=lambda s: s.tile_x)
        cursor = 0
        row_height = row_streams[0].tile_height if row_streams else 0
        for s in row_streams:
            if s.tile_x != cursor or s.tile_height != row_height:
                raise ValueError(f"unsupported ARW6/LLVC3 stream tiling near stream {s.index}")
            cursor += s.tile_width
        if cursor != raw_info.width:
            raise ValueError(f"tile row y={y} covers width {cursor}, expected {raw_info.width}")
    tile_cols = max((sum(1 for s in streams if s.tile_y == y) for y in ys), default=0)
    tile_rows = len(ys)

    t0 = time.perf_counter()
    if use_sample_lut and sample_lut_path is None and DEFAULT_SAMPLE_LUT.exists():
        sample_lut_path = DEFAULT_SAMPLE_LUT
    sample_lut = load_sample_lut(sample_lut_path) if use_sample_lut and sample_lut_path is not None else None

    signed_tiles: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    sample_tiles: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    code_tiles: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    tile_raws: list[np.ndarray] = []
    stream_metas: list[dict[str, Any]] = []
    for stream_index, stream_info in enumerate(streams):
        signed_c0, signed_c1, signed_c2, stream_meta = decode_signed_planes(
            arw, blue_edge_fix=blue_edge_fix, stream_index=stream_index
        )
        if signed_c0.shape != (stream_info.header.coded_half_height, stream_info.header.coded_width):
            raise ValueError(
                f"stream {stream_index} c0 decoded to {signed_c0.shape}, expected "
                f"{stream_info.header.coded_half_height}x{stream_info.header.coded_width}"
            )
        expected_chroma_shape = (stream_info.header.coded_half_height, stream_info.header.coded_width // 2)
        if signed_c1.shape != expected_chroma_shape or signed_c2.shape != expected_chroma_shape:
            raise ValueError(
                f"stream {stream_index} chroma decoded to {signed_c1.shape}/{signed_c2.shape}, "
                f"expected {expected_chroma_shape}"
            )
        signed_tiles.append((signed_c0, signed_c1, signed_c2))
        stream_metas.append(stream_meta)

    all_streams_guarded = all(
        bool(meta.get("coded_decode_size", {}).get("guarded_height_path")) for meta in stream_metas
    )
    native_edge_oracle: dict[str, Any] = {"enabled": False}
    if native_edge_dir is not None:
        native_lut_path = sample_lut_path if sample_lut_path is not None else DEFAULT_SAMPLE_LUT
        if not native_lut_path.exists():
            raise FileNotFoundError(f"native edge oracle needs Sony sample LUT: {native_lut_path}")
        native_lut = sample_lut if sample_lut is not None and sample_lut_path == native_lut_path else load_sample_lut(native_lut_path)
        signed_tiles, native_edge_oracle = apply_native_edge_oracle_rows(
            signed_tiles, streams, native_edge_dir, stem, native_edge_rows, native_lut
        )
        tile_edge_mitigation = {"enabled": False, "reason": "native edge oracle active"}
    elif all_streams_guarded:
        tile_edge_mitigation = {"enabled": False, "reason": "guarded height path active"}
    else:
        signed_tiles, tile_edge_mitigation = mitigate_tiled_signed_edge_rows(
            signed_tiles, streams, tile_edge_rows, tile_edge_mode
        )

    for stream_index, stream_info in enumerate(streams):
        signed_c0, signed_c1, signed_c2 = signed_tiles[stream_index]
        code_c0 = signed_to_sample(signed_c0)
        code_c1 = signed_to_sample(signed_c1)
        code_c2 = signed_to_sample(signed_c2)
        if sample_lut is not None:
            sample_c0 = apply_sample_lut(signed_to_sample(clamp_signed_to_code_range(signed_c0)), sample_lut)
            sample_c1 = apply_sample_lut(signed_to_sample(clamp_signed_to_code_range(signed_c1)), sample_lut)
            sample_c2 = apply_sample_lut(signed_to_sample(clamp_signed_to_code_range(signed_c2)), sample_lut)
        else:
            sample_c0 = code_c0
            sample_c1 = code_c1
            sample_c2 = code_c2
        tile_raw = recombine_rggb(sample_c0, sample_c1, sample_c2)
        expected_tile_height = stream_info.header.logical_height
        if tile_raw.shape != (expected_tile_height, stream_info.header.coded_width):
            raise ValueError(
                f"stream {stream_index} decoded to {tile_raw.shape}, expected "
                f"{expected_tile_height}x{stream_info.header.coded_width}"
            )
        code_tiles.append((code_c0, code_c1, code_c2))
        sample_tiles.append((sample_c0, sample_c1, sample_c2))
        tile_raws.append(tile_raw)

    raw = combine_tiled_arrays_by_position(tile_raws, streams, "raw", x_divisor=1, fill=black)
    sample_c0 = combine_tiled_arrays_by_position([t[0] for t in sample_tiles], streams, "sample_c0", x_divisor=1, fill=black)
    sample_c1 = combine_tiled_arrays_by_position([t[1] for t in sample_tiles], streams, "sample_c1", x_divisor=2, fill=black)
    sample_c2 = combine_tiled_arrays_by_position([t[2] for t in sample_tiles], streams, "sample_c2", x_divisor=2, fill=black)
    code_c0 = combine_tiled_arrays_by_position([t[0] for t in code_tiles], streams, "code_c0", x_divisor=1, fill=black)
    code_c1 = combine_tiled_arrays_by_position([t[1] for t in code_tiles], streams, "code_c1", x_divisor=2, fill=black)
    code_c2 = combine_tiled_arrays_by_position([t[2] for t in code_tiles], streams, "code_c2", x_divisor=2, fill=black)
    if raw.shape[1] != raw_info.width:
        raise ValueError(f"decoded width {raw.shape[1]} does not match TIFF raw width {raw_info.width}")
    if raw.shape[0] > raw_info.height:
        raise ValueError(f"decoded height {raw.shape[0]} exceeds TIFF raw height {raw_info.height}")
    container_raw = raw
    container_crop_origin = tuple(raw_info.default_crop_origin or [12, 8])
    container_crop_size = tuple(raw_info.default_crop_size or [raw.shape[1], raw.shape[0]])
    if raw.shape[0] != raw_info.height:
        top_pad = container_crop_origin[1] if raw.shape[0] + container_crop_origin[1] <= raw_info.height else 0
        padded = np.zeros((raw_info.height, raw_info.width), dtype=np.uint16)
        padded[:] = black
        padded[top_pad : top_pad + raw.shape[0], :] = raw
        container_raw = padded
        container_crop_origin = (container_crop_origin[0], top_pad)
        container_crop_size = (
            min(container_crop_size[0], raw.shape[1] - container_crop_origin[0]),
            min(container_crop_size[1], raw.shape[0]),
        )
    meta = stream_metas[0]
    if len(stream_metas) > 1:
        meta = {
            "streams": stream_metas,
            "timings": {
                "total_stream_decode_s": sum(sum(m["timings"].values()) for m in stream_metas)
            },
        }
    meta["timings"]["total_decode_and_recombine_s"] = time.perf_counter() - t0

    c0_path = out_dir / f"{stem}_llvc3_pure_v0_c0.bin"
    c1_path = out_dir / f"{stem}_llvc3_pure_v0_c1.bin"
    c2_path = out_dir / f"{stem}_llvc3_pure_v0_c2.bin"
    raw_path = out_dir / f"{stem}_llvc3_pure_rggb_{raw_info.width}x{raw_info.height}_u16.raw"
    preview_path = out_dir / f"{stem}_llvc3_pure_preview.png"
    tiff_path = out_dir / f"{stem}_llvc3_pure_bl1024_wl16383_rggb.tiff"
    shifted_tiff_path = out_dir / f"{stem}_llvc3_pure_bl512_wl15871_rggb_shifted.tiff"

    sample_c0.tofile(c0_path)
    sample_c1.tofile(c1_path)
    sample_c2.tofile(c2_path)
    container_raw.tofile(raw_path)
    robust_preview(container_raw, black, white).save(preview_path)
    write_dng(tiff_path, container_raw, black, white, container_crop_origin, container_crop_size)
    shifted = np.clip(container_raw.astype(np.int32) - (black - shifted_black), 0, white).astype(np.uint16)
    write_dng(shifted_tiff_path, shifted, shifted_black, white - (black - shifted_black), container_crop_origin, container_crop_size)

    summary: dict[str, Any] = {
        "input": str(arw),
        "raw_subifd": raw_info.__dict__,
        "llvc_header": header.__dict__,
        "llvc_streams": [
            {
                "index": s.index,
                "offset": s.offset,
                "length": s.length,
                "tile_x": s.tile_x,
                "tile_y": s.tile_y,
                "tile_width": s.tile_width,
                "tile_height": s.tile_height,
                "header": s.header.__dict__,
            }
            for s in streams
        ],
        "tile_layout": {
            "columns": tile_cols,
            "rows": tile_rows,
            "tile_widths": sorted({int(s.tile_width) for s in streams}),
            "tile_header_heights": sorted({int(s.tile_height) for s in streams}),
            "decoded_tile_height": raw.shape[0] // tile_rows,
        },
        "blue_edge_fix": blue_edge_fix,
        "decoded_output_levels": {
            "black_level": int(black),
            "white_level": int(white),
            "source_sony_tag_0x7310_black_level": raw_info.black_level_tag_0x7310,
            "shifted_compat_black_level": int(shifted_black),
        },
        "tile_edge_mitigation": tile_edge_mitigation,
        "native_edge_oracle": native_edge_oracle,
        "sample_lut": {
            "path": str(sample_lut_path) if sample_lut_path is not None else None,
            "enabled": sample_lut is not None,
            "size": int(sample_lut.size) if sample_lut is not None else 0,
        },
        "outputs": {
            "c0_green_u16": str(c0_path),
            "c1_red_u16": str(c1_path),
            "c2_blue_u16": str(c2_path),
            "rggb_raw_u16": str(raw_path),
            "preview_png": str(preview_path),
            "dng_like_bl1024_tiff": str(tiff_path),
            "dng_like_bl512_shifted_tiff": str(shifted_tiff_path),
        },
        "code_plane_stats": {"c0": stats(code_c0), "c1": stats(code_c1), "c2": stats(code_c2)},
        "sample_plane_stats": {
            "c0": stats(sample_c0),
            "c1": stats(sample_c1),
            "c2": stats(sample_c2),
            "raw": stats(container_raw),
            "decoded_tile_mosaic": stats(raw),
        },
        **meta,
    }

    if verify_native_prefix is not None:
        summary["native_verification"] = {
            "c0": compare_plane(sample_c0, Path(f"{verify_native_prefix}_c0.bin"), sample_c0.shape),
            "c1": compare_plane(sample_c1, Path(f"{verify_native_prefix}_c1.bin"), sample_c1.shape),
            "c2": compare_plane(sample_c2, Path(f"{verify_native_prefix}_c2.bin"), sample_c2.shape),
        }
        summary["native_verification"]["all_exact"] = all(
            summary["native_verification"][name]["exact"] for name in ("c0", "c1", "c2")
        )

    summary_path = out_dir / f"{stem}_llvc3_pure_summary.json"
    summary["outputs"]["summary_json"] = str(summary_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("arw", nargs="?", default="DSC00089.ARW")
    ap.add_argument("--out-dir", default="out/pure_decode")
    ap.add_argument("--verify-native-prefix", default="")
    ap.add_argument("--sample-lut", default="", help="optional 65536-entry uint16 code-to-sample LUT")
    ap.add_argument("--no-sample-lut", action="store_true", help="emit unsigned LLVC3 code-domain samples without Sony LUT expansion")
    ap.add_argument("--no-blue-edge-fix", action="store_true")
    ap.add_argument("--no-tile-edge-mitigation", action="store_true")
    ap.add_argument("--tile-edge-rows", type=int, default=DEFAULT_TILE_EDGE_ROWS)
    ap.add_argument("--tile-edge-mode", choices=("average", "linear", "copy"), default=DEFAULT_TILE_EDGE_MODE)
    ap.add_argument("--native-edge-dir", default="", help="directory with native_tileN_v0_c*.bin dumps for edge-row oracle output")
    ap.add_argument("--native-edge-rows", type=int, default=6)
    ap.add_argument("--black", type=int, default=1024)
    ap.add_argument("--white", type=int, default=16383)
    ap.add_argument("--shifted-black", type=int, default=512)
    ns = ap.parse_args()

    summary = decode_to_files(
        Path(ns.arw),
        Path(ns.out_dir),
        Path(ns.verify_native_prefix) if ns.verify_native_prefix else None,
        Path(ns.sample_lut) if ns.sample_lut else None,
        blue_edge_fix=not ns.no_blue_edge_fix,
        black=ns.black,
        white=ns.white,
        shifted_black=ns.shifted_black,
        use_sample_lut=not ns.no_sample_lut,
        tile_edge_rows=0 if ns.no_tile_edge_mitigation else ns.tile_edge_rows,
        tile_edge_mode=ns.tile_edge_mode,
        native_edge_dir=Path(ns.native_edge_dir) if ns.native_edge_dir else None,
        native_edge_rows=ns.native_edge_rows,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
