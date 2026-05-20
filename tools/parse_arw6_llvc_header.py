#!/usr/bin/env python3
"""Tiny ARW6/LLVC header dumper used for quick sanity checks."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

from llvc3_bitstream_probe import RAW_STREAM_OFFSET, find_raw_subifd


def u16be(b: bytes, off: int) -> int:
    return struct.unpack_from(">H", b, off)[0]


def u32le(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def parse_stream(stream: bytes) -> dict:
    h = stream[:0x40]
    word_c = u16be(h, 0x0C)
    word_e = u16be(h, 0x0E)
    return {
        "magic": h[:4].decode("ascii", "replace"),
        "sequence_or_version": u32le(h, 4),
        "coded_width": u16be(h, 0x08),
        "coded_half_height": u16be(h, 0x0A),
        "logical_height": u16be(h, 0x0A) * 2,
        "word_0c": f"0x{word_c:04x}",
        "decoded_bits": (word_c >> 4) & 0x3F,
        "word_0e": f"0x{word_e:04x}",
        "component_count": word_e >> 13,
        "mode": (word_e >> 10) & 0x03,
        "flags_low10": word_e & 0x03FF,
        "subband_header_first_16": stream[0x10:0x20].hex(" "),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default="out/raw_strip.bin")
    ap.add_argument("--offset", type=lambda x: int(x, 0), default=0x200)
    ns = ap.parse_args()

    path = Path(ns.path)
    data = path.read_bytes()
    source = "raw-strip"
    offset = ns.offset
    if data[:2] in (b"II", b"MM"):
        _raw_info, data = find_raw_subifd(path)
        offset = RAW_STREAM_OFFSET
        source = "arw-raw-subifd-strip"
    info = {
        "path": ns.path,
        "source": source,
        "offset": offset,
        "stream_length": len(data) - offset,
        "header": parse_stream(data[offset:]),
    }
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
