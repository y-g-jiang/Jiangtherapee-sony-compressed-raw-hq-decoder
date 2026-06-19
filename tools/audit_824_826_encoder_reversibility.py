#!/usr/bin/env python3
"""Audit which #824/#826 decoder-visible structures can be reversed for modeling.

The audit is intentionally conservative.  It separates:

* exact_reverse: a canonical encoder can write syntax/coefficients that the
  public decoder will read back through the same inverse path.
* canonical_choice: the decoder exposes the syntax, but the real camera encoder's
  rate-distortion search, mode choice, or byte packing choices are not uniquely
  determined by the decoder.  A simulation may pick a canonical rule, but must
  not call it the production encoder.
* not_decoder_determined: the public decoder is insufficient to infer the real
  encoder behavior.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_SONY = Path.home() / "Downloads" / "LibRaw-pr-sony-arw6-craw-hq" / "src" / "decoders" / "sony_arw6.cpp"
DEFAULT_NIKON_DIR = Path.home() / "AppData" / "Local" / "Temp" / "libraw_pr826_nikon_he" / "src" / "decoders" / "nikon_he"


@dataclass(frozen=True)
class AuditItem:
    codec: str
    item: str
    status: str
    source: str
    evidence: str
    consequence: str


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def require_patterns(text: str, patterns: list[str], source: str, errors: list[str]) -> bool:
    ok = True
    for pattern in patterns:
        if not re.search(pattern, text, re.MULTILINE):
            errors.append(f"{source} missing pattern: {pattern}")
            ok = False
    return ok


def audit_sony(path: Path, errors: list[str]) -> list[AuditItem]:
    text = read_text(path)
    src = str(path)
    require_patterns(
        text,
        [
            r"sony_arw6_parse_stream_header",
            r"sony_arw6_parse_directory",
            r"sony_arw6_decode_component",
            r"sony_arw6_decode_packet_arrays",
            r"sony_arw6_synthesize_level",
            r"sony_arw6_final_green",
            r"sony_arw6_clamp_signed_code",
            r"sony_arw6_sample_from_signed",
        ],
        src,
        errors,
    )
    return [
        AuditItem(
            "Sony #824 ARW6/LLVC3",
            "stream and tile directory syntax",
            "exact_reverse",
            src,
            "parse_stream_header/find_streams require A000/0000, coded width/height, stream directory, tile x/y/w/h.",
            "A canonical writer can emit the same header/directory fields for legal tiled or single-stream files.",
        ),
        AuditItem(
            "Sony #824 ARW6/LLVC3",
            "packet records, selectors, adaptive width, zero-run, magnitude and sign syntax",
            "exact_reverse",
            src,
            "parse_packet plus decode_component expose control count, extra count, type, block count, selectors, width update, zero-run, 4-lane magnitude and sign order.",
            "A writer can encode chosen coefficient rows into legal packet payloads; this is syntax-reversible.",
        ),
        AuditItem(
            "Sony #824 ARW6/LLVC3",
            "hierarchical inverse 5/3-like synthesis groups 1..3",
            "exact_reverse",
            src,
            "decode_stream_tile calls synthesize_level_stride or guarded group1/2/3 before final color relation.",
            "For aligned interiors, an encoder can choose subbands whose inverse reconstructs the target working planes; guarded edge rows are a separate boundary case.",
        ),
        AuditItem(
            "Sony #824 ARW6/LLVC3",
            "final green and R/B residual relation",
            "exact_reverse",
            src,
            "final_green plus clamp_signed_code and sample_from_signed define green prediction and R/B residual reconstruction.",
            "A canonical inverse can solve final-green code samples and R/B residuals in the decoder code domain, subject to LUT/clamp projection.",
        ),
        AuditItem(
            "Sony #824 ARW6/LLVC3",
            "camera encoder RD/selector/rate-control policy",
            "not_decoder_determined",
            src,
            "The decoder accepts selector nibbles and packet payloads but does not reveal how the camera chooses them from an input RAW at a target quality.",
            "Mathematical evaluation may use a canonical selector/quantization rule, but cannot claim Sony production encoder equivalence.",
        ),
        AuditItem(
            "Sony #824 ARW6/LLVC3",
            "guarded non-16-aligned edge behavior",
            "canonical_choice",
            src,
            "The decoder exposes guarded group synthesis; exact production encoder choices at tile edges still require real writer validation.",
            "Interior results can be strict; edge rows must be labeled boundary/compatibility rather than coding-performance evidence.",
        ),
    ]


def audit_nikon(nikon_dir: Path, errors: list[str]) -> list[AuditItem]:
    files = {
        "header": nikon_dir / "nikon_he_precinct_header.cpp",
        "gcli": nikon_dir / "nikon_he_gcli_decode.cpp",
        "coeff": nikon_dir / "nikon_he_coefficient_decode.cpp",
        "dequant": nikon_dir / "nikon_he_dequantize.cpp",
        "tile": nikon_dir / "nikon_he_tile.cpp",
        "bayer": nikon_dir / "nikon_he_bayer.cpp",
        "gtli": nikon_dir / "nikon_he_gtli_table.cpp",
    }
    texts = {name: read_text(path) for name, path in files.items()}
    require_patterns(
        "\n".join(texts.values()),
        [
            r"parse_precinct_header",
            r"decode_gcli_values",
            r"unpack_coefficient_magnitudes",
            r"apply_sign_bits",
            r"dequantize_coefficient",
            r"decode_tile",
            r"step1_merge_4_to_2",
            r"step2_bayer_rows",
            r"lookup_gtli_table",
        ],
        str(nikon_dir),
        errors,
    )
    src = str(nikon_dir)
    return [
        AuditItem(
            "Nikon #826 HE",
            "precinct header, Bp/Br/Dpb and LB substream sizes",
            "exact_reverse",
            src,
            "parse_precinct_header exposes total size, Bp, Br, 28 Dpb fields, and 8 LB sig/gcli/data/sign substreams.",
            "A canonical writer can emit legal precinct headers and substream byte counts for selected coding decisions.",
        ),
        AuditItem(
            "Nikon #826 HE",
            "GCLI/GTLI significance and predecessor prediction syntax",
            "exact_reverse",
            src,
            "decode_gcli_values and lookup_gtli_table expose sig bits, unary GCLI deltas, Bp/Br to GTLI tables, and vertical prediction modes.",
            "A writer can encode chosen GCLI arrays into the decoder-visible sig/GCLI streams.",
        ),
        AuditItem(
            "Nikon #826 HE",
            "coefficient magnitude/sign bit-plane syntax and dequantization",
            "exact_reverse",
            src,
            "unpack_coefficient_magnitudes, apply_sign_bits and dequantize_coefficient expose bit-plane order, implicit low bits, sign bits and midpoint scaling.",
            "A writer can encode chosen quantized coefficients and predict the exact inverse dequantization result.",
        ),
        AuditItem(
            "Nikon #826 HE",
            "tile orchestration and step1/step2 Bayer reconstruction",
            "exact_reverse",
            src,
            "decode_tile, step1_merge_4_to_2 and step2_bayer_rows expose the inverse tile, merge and tone-LUT Bayer reconstruction path.",
            "A canonical encoder can work in the same code domain and invert the visible Bayer equations except LUT projection and edge/tail handling.",
        ),
        AuditItem(
            "Nikon #826 HE",
            "camera encoder Bp/Br/Dpb/GTLI rate-control policy",
            "not_decoder_determined",
            src,
            "The decoder parses Bp/Br/Dpb and GTLI tables but does not reveal how the Nikon camera chooses them for a target image or mode.",
            "Mathematical evaluation may sweep or choose canonical Bp/Br rows, but cannot claim Nikon production encoder RD equivalence.",
        ),
        AuditItem(
            "Nikon #826 HE",
            "precinct/container writer and HE/HE* variant selection",
            "canonical_choice",
            src,
            "The decoder exposes precinct stream syntax and HE/HE* table rows; full NEF maker-note/container emission is outside the decoder core.",
            "Core coding math can be strict, while container/variant claims require writer validation against LibRaw/Nikon readers.",
        ),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sony-source", type=Path, default=DEFAULT_SONY)
    ap.add_argument("--nikon-dir", type=Path, default=DEFAULT_NIKON_DIR)
    ap.add_argument("--out", type=Path, default=Path("out/strict_824_826_encoder_reversibility/audit.json"))
    ns = ap.parse_args()

    errors: list[str] = []
    if not ns.sony_source.exists():
        errors.append(f"missing Sony #824 source: {ns.sony_source}")
        sony_items: list[AuditItem] = []
    else:
        sony_items = audit_sony(ns.sony_source, errors)

    if not ns.nikon_dir.exists():
        errors.append(f"missing Nikon #826 source dir: {ns.nikon_dir}")
        nikon_items: list[AuditItem] = []
    else:
        nikon_items = audit_nikon(ns.nikon_dir, errors)

    items = sony_items + nikon_items
    status_counts: dict[str, int] = {}
    for item in items:
        status_counts[item.status] = status_counts.get(item.status, 0) + 1

    result = {
        "kind": "strict #824/#826 decoder-visible encoder reversibility audit",
        "sony_source": str(ns.sony_source),
        "nikon_dir": str(ns.nikon_dir),
        "items": [asdict(item) for item in items],
        "status_counts": status_counts,
        "strict_gate": {
            "allows_decoder_visible_canonical_simulation": bool(items) and not errors,
            "allows_production_encoder_equivalence_claim": False,
            "reason": (
                "Both decoders expose syntax and inverse transforms, but neither decoder uniquely determines "
                "the camera encoder's RD search, mode selection, and target-rate policy."
            ),
        },
        "errors": errors,
    }
    ns.out.parent.mkdir(parents=True, exist_ok=True)
    ns.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
