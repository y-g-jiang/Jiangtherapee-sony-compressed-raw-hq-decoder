#!/usr/bin/env python3
"""Pull the static LLVC3 code-to-sample LUT out of Imaging Edge Edit.exe."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
import pefile


LLVC3_LUT_RVA = 0x4B64E0
LLVC3_LUT_CODES = 4096


def extract_lut(edit_exe: Path) -> np.ndarray:
    pe = pefile.PE(str(edit_exe))
    off = pe.get_offset_from_rva(LLVC3_LUT_RVA)
    data = pe.__data__[off : off + LLVC3_LUT_CODES * 2]
    if len(data) != LLVC3_LUT_CODES * 2:
        raise ValueError(f"could not read {LLVC3_LUT_CODES} uint16 entries from RVA 0x{LLVC3_LUT_RVA:x}")
    return np.frombuffer(data, dtype="<u2").copy()


def padded_lut(lut4096: np.ndarray) -> np.ndarray:
    out = np.empty(65536, dtype="<u2")
    out[: lut4096.size] = lut4096
    out[lut4096.size :] = lut4096[-1]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edit-exe", default=r"C:\Program Files\Sony\Imaging Edge\Edit.exe")
    ap.add_argument("--out-bin", default="tools/data/sony_llvc3_static_lut4096_padded_u16.bin")
    ap.add_argument("--out-tsv", default="tools/data/sony_llvc3_static_lut4096.tsv")
    ns = ap.parse_args()

    lut = extract_lut(Path(ns.edit_exe))
    out_bin = Path(ns.out_bin)
    out_bin.parent.mkdir(parents=True, exist_ok=True)
    padded_lut(lut).tofile(out_bin)

    out_tsv = Path(ns.out_tsv)
    out_tsv.parent.mkdir(parents=True, exist_ok=True)
    with out_tsv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["code", "sample"])
        for code, sample in enumerate(lut.tolist()):
            writer.writerow([code, sample])

    print(f"wrote {out_bin} and {out_tsv}")


if __name__ == "__main__":
    main()
