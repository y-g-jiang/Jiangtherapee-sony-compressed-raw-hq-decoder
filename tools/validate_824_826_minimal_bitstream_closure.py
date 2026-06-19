#!/usr/bin/env python3
"""Write and decode minimal #824/#826 core syntax byte streams.

This is intentionally narrower than a full ARW/NEF writer.  It closes the
decoder-visible core syntax loop: bytes are emitted for Sony LLVC3 packet
records and Nikon HE precinct/GCLI/coefficient substreams, then decoded by
local ports of the same #824/#826 parsing logic and compared against the
canonical coefficients/dequantization expected by the strict simulator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

import strict_824_826_math_eval as strict
from llvc3_bitstream_probe import DirectoryEntry, parse_directory, parse_llvc_header, parse_packet
from llvc3_entropy import decode_record_components


DEFAULT_OUT_DIR = Path("out/strict_824_826_minimal_bitstream_closure")
DEFAULT_SONY = Path.home() / "Downloads" / "LibRaw-pr-sony-arw6-craw-hq" / "src" / "decoders" / "sony_arw6.cpp"
DEFAULT_NIKON_DIR = Path.home() / "AppData" / "Local" / "Temp" / "libraw_pr826_nikon_he" / "src" / "decoders" / "nikon_he"


class BitWriter:
    def __init__(self) -> None:
        self.bits: list[int] = []

    def write(self, value: int, nbits: int) -> None:
        if nbits < 0:
            raise ValueError("negative bit count")
        if value < 0 or value >= (1 << nbits):
            raise ValueError(f"value {value} does not fit in {nbits} bits")
        for shift in range(nbits - 1, -1, -1):
            self.bits.append((value >> shift) & 1)

    def write_bits(self, bits: list[int] | tuple[int, ...]) -> None:
        for bit in bits:
            if bit not in (0, 1):
                raise ValueError(f"invalid bit {bit}")
            self.bits.append(int(bit))

    def write_unary_zeros_plus_one(self, value: int) -> None:
        if value <= 0:
            raise ValueError("Sony unary width value must be positive")
        self.write_bits([0] * (value - 1) + [1])

    def write_nikon_unary(self, value: int) -> None:
        if value < 0:
            raise ValueError("Nikon unary value must be non-negative")
        self.write_bits([1] * value + [0])

    def to_bytes(self, min_len: int = 0) -> bytes:
        out = bytearray()
        bits = list(self.bits)
        if len(bits) % 8:
            bits.extend([0] * (8 - (len(bits) % 8)))
        for i in range(0, len(bits), 8):
            byte = 0
            for bit in bits[i : i + 8]:
                byte = (byte << 1) | bit
            out.append(byte)
        if len(out) < min_len:
            out.extend(b"\x00" * (min_len - len(out)))
        return bytes(out)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def put_be16(buf: bytearray, off: int, value: int) -> None:
    buf[off : off + 2] = int(value).to_bytes(2, "big")


def put_le32(buf: bytearray, off: int, value: int) -> None:
    buf[off : off + 4] = int(value).to_bytes(4, "little")


def put_3byte_length(buf: bytearray, off: int, value: int) -> None:
    if value % 16:
        raise ValueError("LLVC3 directory lengths must be 16-byte aligned")
    encoded = value >> 4
    if encoded >= (1 << 24):
        raise ValueError("directory length too large")
    buf[off : off + 3] = encoded.to_bytes(3, "big")


def sony_expand_magnitude(raw: int, shift: int) -> int:
    if raw <= 0:
        return 0
    if shift <= 0:
        return raw
    return ((2 * raw + 1) << (shift - 1)) - (raw & 1)


def sony_write_width_from_zero(bw: BitWriter, width: int) -> None:
    if width == 0:
        bw.write(0, 1)
        return
    bw.write_bits([1, 0])
    bw.write_unary_zeros_plus_one(width)


def sony_write_keep_width(bw: BitWriter) -> None:
    bw.write(0, 1)


def sony_write_decrease_to_zero(bw: BitWriter, width: int) -> None:
    if width <= 0:
        raise ValueError("width must be positive")
    bw.write_bits([1, 1])
    bw.write_bits([0] * max(0, width - 1))


def sony_write_zero_run_to_end(bw: BitWriter, remaining: int) -> None:
    if remaining <= 1:
        return
    bw.write_bits([0] * ((remaining - 1).bit_length()))


def sony_group_expected(raw_magnitudes: list[int], signs: list[int]) -> list[int]:
    if len(raw_magnitudes) != 4:
        raise ValueError("Sony groups have four lanes")
    out: list[int] = []
    sign_index = 0
    for raw in raw_magnitudes:
        mag = sony_expand_magnitude(raw, shift=1)
        if mag > 0:
            if sign_index >= len(signs):
                raise ValueError("not enough sign bits")
            sign = signs[sign_index]
            sign_index += 1
            out.append(-mag if sign else mag)
        else:
            out.append(0)
    if sign_index != len(signs):
        raise ValueError("unused sign bits")
    return out


def sony_write_group_magnitudes(bw: BitWriter, raw_magnitudes: list[int], width: int) -> None:
    if len(raw_magnitudes) != 4:
        raise ValueError("Sony groups have four lanes")
    for value in raw_magnitudes:
        bw.write(value, width)


def sony_write_signs(bw: BitWriter, signs: list[int]) -> None:
    for sign in signs:
        bw.write(sign, 1)


def sony_record_payload_nonzero_then_zero_run() -> tuple[bytes, list[int]]:
    bw = BitWriter()
    expected: list[int] = []
    sony_write_width_from_zero(bw, 2)
    sony_write_group_magnitudes(bw, [1, 2, 0, 3], width=2)
    sony_write_keep_width(bw)
    sony_write_signs(bw, [0, 1, 0])
    expected.extend(sony_group_expected([1, 2, 0, 3], [0, 1, 0]))
    sony_write_group_magnitudes(bw, [0, 1, 1, 2], width=2)
    sony_write_decrease_to_zero(bw, 2)
    sony_write_signs(bw, [1, 0, 1])
    expected.extend(sony_group_expected([0, 1, 1, 2], [1, 0, 1]))
    sony_write_zero_run_to_end(bw, remaining=2)
    expected.extend([0, 0, 0, 0] * 2)
    return bw.to_bytes(min_len=4), expected


def sony_record_payload_all_zero(groups: int) -> tuple[bytes, list[int]]:
    bw = BitWriter()
    sony_write_width_from_zero(bw, 0)
    sony_write_zero_run_to_end(bw, groups)
    return bw.to_bytes(min_len=1), [0] * (groups * 4)


def sony_packet_header(record_lengths: list[int], selectors: list[int], total_bytes: int) -> bytes:
    block_count = len(record_lengths)
    packet_type = 1
    control_count = ((packet_type + 4) * block_count * 4 + 0x7F) >> 7
    control_bytes = (control_count + 1) << 4
    if total_bytes % 16:
        raise ValueError("Sony packet total must be 16-byte aligned")
    extra_count = (total_bytes >> 4) - control_count - 1
    if extra_count < 0:
        raise ValueError("negative extra_count")
    bw = BitWriter()
    bw.write(control_count, 16)
    bw.write(extra_count, 24)
    bw.write(4, 4)
    bw.write(0, 4)
    bw.write(packet_type, 2)
    bw.write(0, 6)
    bw.write(block_count, 16)
    bw.write(0x10, 8)
    bw.write(0, 8)
    for _ in range(5):
        bw.write(0, 8)
    for length, selector in zip(record_lengths, selectors):
        bw.write(length, 16)
        bw.write(selector, 4)
    return bw.to_bytes(min_len=control_bytes)


def build_sony_stream(out_dir: Path) -> dict[str, Any]:
    groups_per_row = 4
    payload0, expected0 = sony_record_payload_nonzero_then_zero_run()
    payload1, expected1 = sony_record_payload_all_zero(groups_per_row)
    payload2 = b""
    expected2 = [0] * (groups_per_row * 4)
    payloads = [payload0, payload1, payload2]
    selectors = [1, 0, 0]
    control_count = ((1 + 4) * len(payloads) * 4 + 0x7F) >> 7
    control_bytes = (control_count + 1) << 4
    total_bytes = math.ceil((control_bytes + sum(len(p) for p in payloads)) / 16) * 16
    packet = bytearray(sony_packet_header([len(p) for p in payloads], selectors, total_bytes))
    packet.extend(b"".join(payloads))
    packet.extend(b"\x00" * (total_bytes - len(packet)))

    stream = bytearray(0x80 + total_bytes)
    stream[0:4] = b"A000"
    put_le32(stream, 0x04, 0)
    put_be16(stream, 0x08, 64)
    put_be16(stream, 0x0A, 8)
    put_be16(stream, 0x0C, 16 << 4)
    put_be16(stream, 0x0E, (3 << 13) | (3 << 10))
    group_lengths = [0, 0, 0, 0, total_bytes]
    cursor = 0x11
    for length in group_lengths:
        put_3byte_length(stream, cursor, length)
        cursor += 3
    for group in range(5):
        pos = 0x30 + group * 0x10
        if group == 4:
            stream[pos] = 1
            put_3byte_length(stream, pos + 1, total_bytes)
        else:
            stream[pos] = 0
    stream[0x80 : 0x80 + total_bytes] = packet

    stream_path = out_dir / "sony_minimal_llvc3_stream.bin"
    packet_path = out_dir / "sony_minimal_packet.bin"
    stream_path.write_bytes(bytes(stream))
    packet_path.write_bytes(bytes(packet))

    header = parse_llvc_header(bytes(stream))
    consumed, entries, groups = parse_directory(bytes(stream))
    entry = next(e for e in entries if e.group == 4 and e.index == 0)
    packet_info = parse_packet(bytes(stream), entry)
    decoded_records: list[dict[str, Any]] = []
    expected_records = [expected0, expected1, expected2]
    for rec, expected in zip(packet_info.records, expected_records):
        rows, states = decode_record_components(bytes(packet), asdict(rec), groups_per_row, components=1)
        decoded = rows[0]
        decoded_records.append(
            {
                "record": rec.index,
                "byte_length": rec.byte_length,
                "selector": rec.selectors[0] if rec.selectors else 0,
                "decoded_coefficients": decoded,
                "expected_coefficients": expected,
                "exact_match": decoded == expected,
                "reader_state": states[0],
            }
        )
    all_valid = all(packet_info.validation.values())
    exact_match = all(r["exact_match"] for r in decoded_records)
    return {
        "scope": "Sony #824 LLVC3 minimal stream header + directory + type-1 packet records",
        "full_arw_container": False,
        "stream_file": str(stream_path),
        "packet_file": str(packet_path),
        "stream_sha256": hashlib.sha256(bytes(stream)).hexdigest(),
        "packet_sha256": hashlib.sha256(bytes(packet)).hexdigest(),
        "header": asdict(header),
        "directory_consumed": consumed,
        "directory_groups": groups,
        "entry": asdict(entry),
        "packet_validation": packet_info.validation,
        "all_packet_validations_pass": all_valid,
        "decoded_records": decoded_records,
        "exact_roundtrip_match": exact_match,
    }


class NikonBitReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def read_bits(self, count: int) -> int:
        out = 0
        for _ in range(count):
            if self.pos >= len(self.data) * 8:
                out <<= 1
                continue
            byte = self.data[self.pos >> 3]
            bit = (byte >> (7 - (self.pos & 7))) & 1
            out = (out << 1) | bit
            self.pos += 1
        return out

    def read_unary(self) -> int:
        count = 0
        while self.pos < len(self.data) * 8:
            bit = self.read_bits(1)
            if bit == 0:
                return count
            count += 1
        return count


def nikon_predict(gtli: int, previous_gcli: int, unary_code: int) -> int:
    m_top = max(previous_gcli, gtli)
    threshold = m_top - gtli
    if unary_code == 0:
        delta = 0
    elif unary_code <= 2 * threshold:
        delta = -((unary_code + 1) // 2) if (unary_code & 1) else unary_code // 2
    else:
        delta = unary_code - threshold
    return m_top + delta


def nikon_decode_gcli(sig: bytes, gcli: bytes, mode: int, num_groups: int, gtli: int, previous: list[int] | None = None) -> list[int]:
    sig_reader = NikonBitReader(sig)
    gcli_reader = NikonBitReader(gcli)
    out = [0] * num_groups
    prev = previous or [0] * num_groups
    for block in range((num_groups + 7) // 8):
        base = block * 8
        block_size = min(8, num_groups - base)
        sig_bit = sig_reader.read_bits(1)
        if sig_bit == 1:
            for i in range(block_size):
                out[base + i] = gtli if mode == 0x71 else max(prev[base + i], gtli)
        else:
            for i in range(block_size):
                u = gcli_reader.read_unary()
                out[base + i] = gtli + u if mode == 0x71 else nikon_predict(gtli, prev[base + i], u)
    return out


def nikon_write_gcli_zero_prediction(gtli: int, gcli_values: list[int]) -> tuple[bytes, bytes]:
    sig = BitWriter()
    gcli = BitWriter()
    sig.write(0, 1)
    for value in gcli_values:
        gcli.write_nikon_unary(value - gtli)
    return sig.to_bytes(min_len=1), gcli.to_bytes()


def nikon_write_gcli_vertical(gtli: int, previous: list[int], unary_codes: list[int]) -> tuple[bytes, bytes, list[int]]:
    sig = BitWriter()
    gcli = BitWriter()
    sig.write(0, 1)
    out: list[int] = []
    for prev, unary in zip(previous, unary_codes):
        gcli.write_nikon_unary(unary)
        out.append(nikon_predict(gtli, prev, unary))
    return sig.to_bytes(min_len=1), gcli.to_bytes(), out


def nikon_write_coefficients(coefficients: list[int], gcli_values: list[int], gtli: int) -> tuple[bytes, bytes]:
    data = BitWriter()
    sign = BitWriter()
    for group_index, gcli in enumerate(gcli_values):
        bpc = max(0, gcli - gtli)
        mags = [abs(coefficients[group_index * 4 + lane]) >> gtli for lane in range(4)]
        for bp in range(bpc - 1, -1, -1):
            nibble = 0
            for lane, mag in enumerate(mags):
                nibble |= ((mag >> bp) & 1) << (3 - lane)
            data.write(nibble, 4)
        for lane in range(4):
            coeff = coefficients[group_index * 4 + lane]
            if coeff != 0:
                sign.write(1 if coeff < 0 else 0, 1)
    return data.to_bytes(), sign.to_bytes()


def nikon_decode_coefficients(data: bytes, sign: bytes, gcli_values: list[int], gtli: int) -> list[int]:
    data_reader = NikonBitReader(data)
    raw: list[int] = []
    for gcli in gcli_values:
        bpc = max(0, gcli - gtli)
        mags = [0, 0, 0, 0]
        for _ in range(bpc):
            nibble = data_reader.read_bits(4)
            mags[0] = (mags[0] << 1) | ((nibble >> 3) & 1)
            mags[1] = (mags[1] << 1) | ((nibble >> 2) & 1)
            mags[2] = (mags[2] << 1) | ((nibble >> 1) & 1)
            mags[3] = (mags[3] << 1) | (nibble & 1)
        raw.extend([m << gtli for m in mags])
    sign_reader = NikonBitReader(sign)
    for i, coeff in enumerate(raw):
        if coeff and sign_reader.read_bits(1):
            raw[i] = -coeff
    return raw


def nikon_parse_precinct_header(data: bytes, image_width: int) -> dict[str, Any]:
    f20 = ((image_width // 2) + 255) // 256
    if len(data) < 19:
        raise ValueError("precinct is too short")
    out: dict[str, Any] = {
        "total_size": int.from_bytes(data[:3], "big"),
        "Bp": data[3],
        "Br": data[4],
        "Dpb": [],
        "f20": f20,
        "line_blocks": [],
    }
    for b in data[5:12]:
        for j in range(4):
            out["Dpb"].append((b >> (6 - 2 * j)) & 3)
    cursor = 12
    for lb in range(8):
        val = int.from_bytes(data[cursor : cursor + 7], "big")
        f20_sign = (val >> 55) & 1
        data_bytes = (val >> 35) & 0xFFFFF
        gcli_bytes = (val >> 15) & 0xFFFFF
        sign_bytes = val & 0x7FFF
        cursor += 7
        sig_offset = cursor
        cursor += f20 + gcli_bytes + data_bytes + sign_bytes
        out["line_blocks"].append(
            {
                "lb": lb,
                "f20_sign": f20_sign,
                "sig_bytes": f20,
                "gcli_bytes": gcli_bytes,
                "data_bytes": data_bytes,
                "sign_bytes": sign_bytes,
                "sig_offset": sig_offset,
            }
        )
    out["parsed_bytes"] = cursor
    return out


def nikon_pack_dpb(values: list[int]) -> bytes:
    if len(values) != 28:
        raise ValueError("Dpb needs 28 entries")
    out = bytearray()
    for start in range(0, 28, 4):
        b = 0
        for j, value in enumerate(values[start : start + 4]):
            b |= (int(value) & 3) << (6 - 2 * j)
        out.append(b)
    return bytes(out)


def nikon_pack_lb_header(f20_sign: int, data_bytes: int, gcli_bytes: int, sign_bytes: int) -> bytes:
    val = ((f20_sign & 1) << 55) | ((data_bytes & 0xFFFFF) << 35) | ((gcli_bytes & 0xFFFFF) << 15) | (sign_bytes & 0x7FFF)
    return val.to_bytes(7, "big")


def build_nikon_precinct(out_dir: Path) -> dict[str, Any]:
    image_width = 512
    bp, br = 4, 0
    gtli = strict.GTLI_ROWS[(bp, br)][0]
    gcli_values = [gtli + 2, gtli + 1, gtli, gtli + 3, gtli, gtli + 1, gtli + 2, gtli]
    coefficients = [
        2, -4, 6, 0,
        0, 2, -2, 0,
        0, 0, 0, 0,
        -14, 0, 8, -6,
        0, 0, 0, 0,
        2, 0, -2, 0,
        6, -4, 2, 0,
        0, 0, 0, 0,
    ]
    sig_bytes, gcli_bytes = nikon_write_gcli_zero_prediction(gtli, gcli_values)
    data_bytes, sign_bytes = nikon_write_coefficients(coefficients, gcli_values, gtli)
    decoded_gcli = nikon_decode_gcli(sig_bytes, gcli_bytes, 0x71, len(gcli_values), gtli)
    decoded_coefficients = nikon_decode_coefficients(data_bytes, sign_bytes, decoded_gcli, gtli)
    dequant = strict.nikon_dequantize(np.asarray(decoded_coefficients, dtype=np.int64), np.asarray(decoded_gcli, dtype=np.int64), gtli)
    expected_dequant = strict.nikon_dequantize(np.asarray(coefficients, dtype=np.int64), np.asarray(gcli_values, dtype=np.int64), gtli)

    previous = [gtli + 3, gtli + 1, gtli, gtli + 2, gtli + 4, gtli + 1, gtli, gtli + 2]
    unary_codes = [0, 1, 2, 3, 4, 0, 1, 2]
    vertical_sig, vertical_gcli_bytes, expected_vertical = nikon_write_gcli_vertical(gtli, previous, unary_codes)
    decoded_vertical = nikon_decode_gcli(vertical_sig, vertical_gcli_bytes, 0x73, len(previous), gtli, previous)

    prefix = bytearray(b"\x00\x00\x00")
    prefix.extend(bytes([bp, br]))
    prefix.extend(nikon_pack_dpb([0] * 28))
    body = bytearray()
    for lb in range(8):
        if lb == 0:
            body.extend(nikon_pack_lb_header(1, len(data_bytes), len(gcli_bytes), len(sign_bytes)))
            body.extend(sig_bytes)
            body.extend(gcli_bytes)
            body.extend(data_bytes)
            body.extend(sign_bytes)
        else:
            body.extend(nikon_pack_lb_header(1, 0, 0, 0))
            body.extend(b"\xff")
    total_minus_12 = len(body)
    prefix[0:3] = total_minus_12.to_bytes(3, "big")
    precinct = bytes(prefix + body)
    precinct_path = out_dir / "nikon_minimal_precinct.bin"
    precinct_path.write_bytes(precinct)
    parsed = nikon_parse_precinct_header(precinct, image_width)
    lb0 = parsed["line_blocks"][0]
    lb0_sig = precinct[lb0["sig_offset"] : lb0["sig_offset"] + lb0["sig_bytes"]]
    lb0_gcli_start = lb0["sig_offset"] + lb0["sig_bytes"]
    lb0_data_start = lb0_gcli_start + lb0["gcli_bytes"]
    lb0_sign_start = lb0_data_start + lb0["data_bytes"]
    offset_slices_match = (
        lb0_sig == sig_bytes
        and precinct[lb0_gcli_start:lb0_data_start] == gcli_bytes
        and precinct[lb0_data_start:lb0_sign_start] == data_bytes
        and precinct[lb0_sign_start:lb0_sign_start + lb0["sign_bytes"]] == sign_bytes
    )
    return {
        "scope": "Nikon #826 HE minimal precinct header + LB0 GCLI/data/sign substreams",
        "full_nef_container": False,
        "precinct_file": str(precinct_path),
        "precinct_sha256": hashlib.sha256(precinct).hexdigest(),
        "image_width_for_f20": image_width,
        "bp": bp,
        "br": br,
        "subband": 0,
        "gtli": gtli,
        "parsed_header": parsed,
        "offset_slices_match": offset_slices_match,
        "gcli_zero_prediction": {
            "encoded_hex": {"sig": sig_bytes.hex(), "gcli": gcli_bytes.hex()},
            "expected": gcli_values,
            "decoded": decoded_gcli,
            "exact_match": decoded_gcli == gcli_values,
        },
        "coefficient_bitplanes_and_sign": {
            "encoded_hex": {"data": data_bytes.hex(), "sign": sign_bytes.hex()},
            "expected_coefficients": coefficients,
            "decoded_coefficients": decoded_coefficients,
            "exact_match": decoded_coefficients == coefficients,
        },
        "dequantization": {
            "expected": [int(x) for x in expected_dequant.tolist()],
            "decoded": [int(x) for x in dequant.tolist()],
            "exact_match": np.array_equal(dequant, expected_dequant),
        },
        "gcli_vertical_prediction": {
            "previous": previous,
            "unary_codes": unary_codes,
            "encoded_hex": {"sig": vertical_sig.hex(), "gcli": vertical_gcli_bytes.hex()},
            "expected": expected_vertical,
            "decoded": decoded_vertical,
            "exact_match": decoded_vertical == expected_vertical,
        },
    }


def source_fingerprints(sony_source: Path, nikon_dir: Path) -> dict[str, Any]:
    nikon_files = [
        "nikon_he_precinct_header.cpp",
        "nikon_he_bit_reader.cpp",
        "nikon_he_gcli_decode.cpp",
        "nikon_he_coefficient_decode.cpp",
        "nikon_he_dequantize.cpp",
        "nikon_he_gtli_table.cpp",
        "nikon_he_predict_lut.cpp",
    ]
    return {
        "sony_824_source": str(sony_source),
        "sony_824_source_sha256": sha256_file(sony_source) if sony_source.exists() else None,
        "nikon_826_dir": str(nikon_dir),
        "nikon_826_files": {
            name: {
                "path": str(nikon_dir / name),
                "sha256": sha256_file(nikon_dir / name) if (nikon_dir / name).exists() else None,
            }
            for name in nikon_files
        },
        "strict_math_eval_source": str(Path(strict.__file__).resolve()),
        "strict_math_eval_sha256": sha256_file(Path(strict.__file__).resolve()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--sony-source", type=Path, default=DEFAULT_SONY)
    ap.add_argument("--nikon-dir", type=Path, default=DEFAULT_NIKON_DIR)
    ns = ap.parse_args()

    ns.out_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    sony = build_sony_stream(ns.out_dir)
    nikon = build_nikon_precinct(ns.out_dir)
    if not sony["all_packet_validations_pass"]:
        errors.append("Sony packet parser validation failed")
    if not sony["exact_roundtrip_match"]:
        errors.append("Sony decoded coefficients differ from canonical payload")
    if not nikon["offset_slices_match"]:
        errors.append("Nikon parsed LB offsets do not recover emitted substreams")
    for key in ("gcli_zero_prediction", "coefficient_bitplanes_and_sign", "dequantization", "gcli_vertical_prediction"):
        if not nikon[key]["exact_match"]:
            errors.append(f"Nikon {key} roundtrip mismatch")

    result = {
        "kind": "strict #824/#826 minimal core bitstream closure",
        "generated_unix": int(time.time()),
        "evidence_boundary": {
            "decoder_visible_core_syntax_bytes": True,
            "full_sony_arw_container": False,
            "full_nikon_nef_container": False,
            "allows_production_encoder_equivalence_claim": False,
            "reason": "The emitted bytes close packet/precinct entropy syntax and dequantization, but do not emit complete camera RAW containers or private RD policy.",
        },
        "source_fingerprints": source_fingerprints(ns.sony_source, ns.nikon_dir),
        "sony": sony,
        "nikon": nikon,
        "summary": {
            "all_passed": not errors,
            "sony_records_checked": len(sony["decoded_records"]),
            "nikon_gcli_groups_checked": len(nikon["gcli_zero_prediction"]["decoded"]),
            "nikon_vertical_prediction_groups_checked": len(nikon["gcli_vertical_prediction"]["decoded"]),
        },
        "errors": errors,
    }
    out = ns.out_dir / "bitstream_closure.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
