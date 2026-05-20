#!/usr/bin/env python3
"""Find likely CompRaw/ARW6 code paths inside Sony Imaging Edge binaries.

The workflow stays plain: string hits, RIP-relative xrefs, .pdata function
ranges, then small disassembly snippets that are easy to grep later.
"""

from __future__ import annotations

import argparse
import bisect
import json
import struct
from dataclasses import dataclass
from pathlib import Path

import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_64
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_REG_RIP


DEFAULT_TERMS = [
    "x-sony-arw",
    "Compression",
    "CompressedBitsPerPixel",
    "Cas table from ARW-param file",
    "ERR: raw2y_init",
    "ERR: raw2y_update_desc",
    "ERR: raw2y_exec",
    "C:\\Jenkins_git\\RAW_Private\\CompRaw\\CompRaw\\compraw_vulkan\\Compraw_NR_VK\\raw2y\\src\\raw2y.cpp",
    "C:\\Jenkins_git\\RAW_Private\\CompRaw\\CompRaw\\compraw_vulkan\\Compraw_NR_VK\\compraw_nr_top\\compraw_nr_top.cpp",
    "C:\\Jenkins_git\\RAW_Private\\CompRaw\\CompRaw\\compraw_vulkan\\Compraw_HDR_VK\\compraw_hdr_top\\compraw_hdr_top.cpp",
    "!!! Error Raw2yParam: cr_noise_b needs to be between [0-131072]",
    "!!! Error raw2y param %d",
    ".?AVZcTaskDemosaicCompRaw@sony_zhacai@@",
    ".?AVTileDevForARWCompRaw@sony_zhacai@@",
    ".?AVCompRawCompositer@@",
    ".?AVExportImageParamCompRawComposite@@",
    "Wavelet",
    "LLVCDecoder",
    "sony_llvc3_dec",
    "?Decode@LLVCDecoder@sony_llvc3_dec@@UEAAHPEAU_LLVCDecoderInitParam",
    "?Decode@LLVCDecoder@sony_llvc3_dec@@UEAAHPEAU_LLVCDecoderOutData",
]


@dataclass(frozen=True)
class Section:
    name: str
    rva: int
    vsize: int
    raw: int
    raw_size: int
    chars: int


@dataclass(frozen=True)
class FunctionRange:
    begin: int
    end: int
    unwind: int


class PEView:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = path.read_bytes()
        self.pe = pefile.PE(str(path), fast_load=False)
        self.image_base = self.pe.OPTIONAL_HEADER.ImageBase
        self.sections = [
            Section(
                s.Name.decode(errors="ignore").rstrip("\0"),
                s.VirtualAddress,
                s.Misc_VirtualSize,
                s.PointerToRawData,
                s.SizeOfRawData,
                s.Characteristics,
            )
            for s in self.pe.sections
        ]
        self.functions = self._read_pdata()
        self.function_starts = [f.begin for f in self.functions]

    def rva_to_off(self, rva: int) -> int | None:
        for s in self.sections:
            span = max(s.vsize, s.raw_size)
            if s.rva <= rva < s.rva + span:
                off = s.raw + (rva - s.rva)
                if 0 <= off < len(self.data):
                    return off
        return None

    def off_to_rva(self, off: int) -> int | None:
        for s in self.sections:
            if s.raw <= off < s.raw + s.raw_size:
                return s.rva + (off - s.raw)
        return None

    def bytes_at_rva(self, rva: int, size: int) -> bytes:
        off = self.rva_to_off(rva)
        if off is None:
            return b""
        return self.data[off : off + size]

    def _read_pdata(self) -> list[FunctionRange]:
        pdata = next((s for s in self.sections if s.name == ".pdata"), None)
        if not pdata:
            return []
        off = pdata.raw
        end = off + pdata.raw_size
        funcs = []
        for pos in range(off, end - 11, 12):
            begin, finish, unwind = struct.unpack_from("<III", self.data, pos)
            if begin and finish and begin < finish:
                funcs.append(FunctionRange(begin, finish, unwind))
        funcs.sort(key=lambda x: x.begin)
        return funcs

    def function_for_rva(self, rva: int) -> FunctionRange | None:
        i = bisect.bisect_right(self.function_starts, rva) - 1
        if i >= 0:
            f = self.functions[i]
            if f.begin <= rva < f.end:
                return f
        return None


def find_string_rvas(view: PEView, terms: list[str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for term in terms:
        needle = term.encode("utf-8")
        hits = []
        pos = view.data.find(needle)
        while pos != -1:
            rva = view.off_to_rva(pos)
            if rva is not None:
                hits.append(rva)
            pos = view.data.find(needle, pos + 1)
        if hits:
            out[term] = hits
    return out


def disassemble_section(view: PEView, section_name: str = ".text"):
    sec = next(s for s in view.sections if s.name == section_name)
    code = view.data[sec.raw : sec.raw + sec.raw_size]
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    return list(md.disasm(code, view.image_base + sec.rva))


def rip_target(insn) -> int | None:
    for op in insn.operands:
        if op.type == X86_OP_MEM and op.mem.base == X86_REG_RIP:
            return insn.address + insn.size + op.mem.disp
    return None


def immediate_target(insn) -> int | None:
    for op in insn.operands:
        if op.type == X86_OP_IMM:
            return op.imm
    return None


def format_insn(insn, image_base: int) -> str:
    rva = insn.address - image_base
    return f"{rva:08x}: {insn.mnemonic:<7} {insn.op_str}"


def read_u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def read_u64(data: bytes, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]


def find_msvc_rtti_vftables(view: PEView, type_name: str) -> list[dict]:
    """Find MSVC x64 vftables for a mangled type descriptor name.

    MSVC x64 stores RVAs in the CompleteObjectLocator. The vftable[-1] slot
    holds a VA pointing at that COL, and the table entries are VA function
    pointers.
    """

    out = []
    name = type_name.encode("ascii")
    name_off = view.data.find(name)
    while name_off != -1:
        name_rva = view.off_to_rva(name_off)
        if name_rva is None:
            name_off = view.data.find(name, name_off + 1)
            continue
        type_desc_rva = name_rva - 16
        td_le = struct.pack("<I", type_desc_rva)
        pos = view.data.find(td_le)
        while pos != -1:
            col_off = pos - 12
            if col_off >= 0:
                col_rva = view.off_to_rva(col_off)
                if col_rva is not None and col_off + 24 <= len(view.data):
                    sig, offset, cd_offset, td, chd, self_rva = struct.unpack_from("<IIIIII", view.data, col_off)
                    plausible = (
                        sig in (0, 1)
                        and td == type_desc_rva
                        and self_rva == col_rva
                        and view.rva_to_off(chd) is not None
                    )
                    if plausible:
                        col_va = view.image_base + col_rva
                        col_va_le = struct.pack("<Q", col_va)
                        vpos = view.data.find(col_va_le)
                        while vpos != -1:
                            vt_rva = view.off_to_rva(vpos + 8)
                            if vt_rva is not None:
                                entries = []
                                eoff = vpos + 8
                                for idx in range(128):
                                    if eoff + 8 > len(view.data):
                                        break
                                    ptr = read_u64(view.data, eoff)
                                    ptr_rva = ptr - view.image_base
                                    if view.rva_to_off(ptr_rva) is None:
                                        break
                                    sec = next((s for s in view.sections if s.rva <= ptr_rva < s.rva + max(s.vsize, s.raw_size)), None)
                                    if not sec or sec.name != ".text":
                                        break
                                    fn = view.function_for_rva(ptr_rva)
                                    entries.append(
                                        {
                                            "index": idx,
                                            "entry_rva": vt_rva + idx * 8,
                                            "target_rva": ptr_rva,
                                            "function_begin": fn.begin if fn else None,
                                            "function_end": fn.end if fn else None,
                                        }
                                    )
                                    eoff += 8
                                if entries:
                                    out.append(
                                        {
                                            "type_name": type_name,
                                            "name_rva": name_rva,
                                            "type_descriptor_rva": type_desc_rva,
                                            "complete_object_locator_rva": col_rva,
                                            "class_hierarchy_descriptor_rva": chd,
                                            "vftable_rva": vt_rva,
                                            "entries": entries,
                                        }
                                    )
                            vpos = view.data.find(col_va_le, vpos + 1)
            pos = view.data.find(td_le, pos + 1)
        name_off = view.data.find(name, name_off + 1)
    # Deduplicate by vftable.
    dedup = {}
    for item in out:
        dedup[(item["type_descriptor_rva"], item["complete_object_locator_rva"], item["vftable_rva"])] = item
    return list(dedup.values())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("exe", type=Path)
    ap.add_argument("--out", type=Path, default=Path("out/reverse"))
    ap.add_argument("--context", type=int, default=48, help="instructions around each xref")
    ns = ap.parse_args()

    view = PEView(ns.exe)
    ns.out.mkdir(parents=True, exist_ok=True)
    stem = ns.exe.stem.lower()

    strings = find_string_rvas(view, DEFAULT_TERMS)
    insns = disassemble_section(view)
    text_rva_min = min(i.address - view.image_base for i in insns)
    text_rva_max = max(i.address - view.image_base for i in insns)

    string_rvas = {rva for hits in strings.values() for rva in hits}
    xrefs = []
    for idx, insn in enumerate(insns):
        target_va = rip_target(insn)
        if target_va is None:
            continue
        target_rva = target_va - view.image_base
        for term, hits in strings.items():
            if any(r <= target_rva < r + len(term) + 1 for r in hits):
                fn = view.function_for_rva(insn.address - view.image_base)
                xrefs.append(
                    {
                        "term": term,
                        "string_rva": next(r for r in hits if r <= target_rva < r + len(term) + 1),
                        "xref_rva": insn.address - view.image_base,
                        "function_begin": fn.begin if fn else None,
                        "function_end": fn.end if fn else None,
                        "instruction": f"{insn.mnemonic} {insn.op_str}",
                        "insn_index": idx,
                    }
                )

    functions: dict[int, dict] = {}
    for xr in xrefs:
        fb = xr["function_begin"]
        fe = xr["function_end"]
        if fb is None or fe is None:
            continue
        item = functions.setdefault(
            fb,
            {
                "begin": fb,
                "end": fe,
                "size": fe - fb,
                "xrefs": [],
                "calls": [],
                "nearby_strings": [],
            },
        )
        item["xrefs"].append({k: xr[k] for k in ["term", "string_rva", "xref_rva", "instruction"]})

    # Add call-target hints and nearby string refs for every candidate function.
    by_addr = {insn.address - view.image_base: idx for idx, insn in enumerate(insns)}
    for fb, item in functions.items():
        f_insns = [insn for insn in insns if fb <= insn.address - view.image_base < item["end"]]
        calls = []
        refs = []
        for insn in f_insns:
            rva = insn.address - view.image_base
            if insn.mnemonic == "call":
                imm = immediate_target(insn)
                if imm is not None:
                    target_rva = imm - view.image_base
                    if text_rva_min <= target_rva <= text_rva_max:
                        calls.append({"at": rva, "target": target_rva})
            tgt = rip_target(insn)
            if tgt is not None:
                trva = tgt - view.image_base
                for term, hits in strings.items():
                    if any(r <= trva < r + len(term) + 1 for r in hits):
                        refs.append({"at": rva, "term": term, "target": trva})
        item["calls"] = calls[:200]
        item["nearby_strings"] = refs[:200]

        # Disassembly file per candidate.
        text_lines = []
        text_lines.append(f"{view.path}")
        text_lines.append(f"function RVA {fb:#x}-{item['end']:#x} size {item['size']}")
        text_lines.append("string refs:")
        for ref in item["nearby_strings"]:
            text_lines.append(f"  {ref['at']:#x} -> {ref['term']}")
        text_lines.append("")
        for insn in f_insns:
            text_lines.append(format_insn(insn, view.image_base))
        (ns.out / f"{stem}_func_{fb:08x}.asm").write_text("\n".join(text_lines), encoding="utf-8")

    report = {
        "exe": str(view.path),
        "image_base": view.image_base,
        "sections": [s.__dict__ for s in view.sections],
        "strings": strings,
        "xrefs": xrefs,
        "functions": list(functions.values()),
    }

    rtti_types = [
        ".?AVLLVCDecoder@sony_llvc3_dec@@",
        ".?AVImage@sony_llvc3_dec@@",
        ".?AVLinebasedWavelet@sony_llvc3_dec@@",
        ".?AVWavelet@sony_llvc3_dec@@",
        ".?AVSubband@sony_llvc3_dec@@",
        ".?AVTileDevForARWCompRaw@sony_zhacai@@",
        ".?AVZcTaskDemosaicCompRaw@sony_zhacai@@",
        ".?AVARWParser@sony_zhacai@@",
        ".?AVZcARW@sony_zhacai@@",
        ".?AVARW10toSR2Converter@sony_zhacai@@",
    ]
    rtti = []
    for type_name in rtti_types:
        rtti.extend(find_msvc_rtti_vftables(view, type_name))
    report["rtti_vftables"] = rtti

    for item in rtti:
        lines = [
            f"{view.path}",
            f"type {item['type_name']}",
            f"type_descriptor_rva {item['type_descriptor_rva']:#x}",
            f"col_rva {item['complete_object_locator_rva']:#x}",
            f"vftable_rva {item['vftable_rva']:#x}",
            "",
        ]
        for ent in item["entries"]:
            lines.append(
                f"[{ent['index']:02d}] entry {ent['entry_rva']:#x} -> target {ent['target_rva']:#x} "
                f"func {ent['function_begin'] if ent['function_begin'] is not None else None:#x}"
                if ent["function_begin"] is not None
                else f"[{ent['index']:02d}] entry {ent['entry_rva']:#x} -> target {ent['target_rva']:#x}"
            )
        lines.append("")
        # Disassemble vtable targets. Constructors can be large; cap each.
        for ent in item["entries"]:
            fb = ent["function_begin"] or ent["target_rva"]
            fe = ent["function_end"] or (fb + 0x300)
            f_insns = [insn for insn in insns if fb <= insn.address - view.image_base < fe]
            lines.append(f"--- vfunc {ent['index']} target {ent['target_rva']:#x} function {fb:#x}-{fe:#x}")
            for insn in f_insns[:260]:
                lines.append(format_insn(insn, view.image_base))
            if len(f_insns) > 260:
                lines.append(f"... truncated {len(f_insns) - 260} instructions")
            lines.append("")
        safe = item["type_name"].replace(".", "_").replace("?", "_").replace("@", "_").replace("$", "_")
        (ns.out / f"{stem}_rtti_{safe}_{item['vftable_rva']:08x}.asm").write_text("\n".join(lines), encoding="utf-8")
    (ns.out / f"{stem}_compraw_xrefs.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = []
    summary.append(f"{view.path}")
    summary.append(f"strings: {sum(len(v) for v in strings.values())}, xrefs: {len(xrefs)}, candidate functions: {len(functions)}")
    for fn in sorted(functions.values(), key=lambda x: (-(len(x["xrefs"])), x["begin"])):
        terms = sorted({x["term"] for x in fn["xrefs"]})
        summary.append(f"RVA {fn['begin']:#x}-{fn['end']:#x} size {fn['size']}: {len(fn['xrefs'])} refs")
        for term in terms[:8]:
            summary.append(f"  - {term}")
    summary.append("")
    summary.append(f"RTTI vftables: {len(rtti)}")
    for item in rtti:
        summary.append(
            f"RVA {item['vftable_rva']:#x} {item['type_name']} entries={len(item['entries'])}"
        )
        for ent in item["entries"][:12]:
            summary.append(
                f"  [{ent['index']:02d}] -> {ent['target_rva']:#x} func={ent['function_begin'] if ent['function_begin'] is not None else None}"
            )
    (ns.out / f"{stem}_compraw_xrefs.txt").write_text("\n".join(summary), encoding="utf-8")
    print("\n".join(summary[:120]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
