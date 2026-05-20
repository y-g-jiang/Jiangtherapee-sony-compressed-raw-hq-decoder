#!/usr/bin/env python3
"""Drive Imaging Edge's LLVCDecoder directly with hand-built buffers.

The probe I use when the pure decoder and the native path disagree: attach to
Edit.exe, build the smallest useful decoder object, and dump whatever the native
code writes into the output planes.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import frida


EDIT_EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
EDIT_MODULE = "Edit.exe"
RAW_STREAM_OFFSET = 0x200


JS = r"""
const RVAS = {
  initThunk: 0x1a50a0,
  decodeThunk: 0x1a5180,
  terminate: 0x1a5190,
  vtable: 0x4e7338
};

function hexdumpSafe(p, n) {
  try {
    return hexdump(p, { length: n, ansi: false });
  } catch (e) {
    return String(e);
  }
}

function checksum32(p, n) {
  let s = 0 >>> 0;
  const step = Math.max(1, Math.floor(n / 1048576));
  for (let i = 0; i < n; i += step) {
    s = (s + p.add(i).readU8()) >>> 0;
  }
  return s >>> 0;
}

function firstChanged(p, n, fill) {
  const limit = Math.min(n, 4 * 1024 * 1024);
  for (let i = 0; i < limit; i++) {
    if (p.add(i).readU8() !== fill) return i;
  }
  return -1;
}

function readU32Array(p, off, count) {
  const a = [];
  for (let i = 0; i < count; i++) a.push(p.add(off + 4 * i).readU32());
  return a;
}

rpc.exports = {
  probe: function (options, data) {
    const mod = Process.getModuleByName("Edit.exe");
    const base = mod.base;
    const nativeOptions = options.stealExceptions ? { exceptions: "steal" } : {};
    const initThunk = new NativeFunction(base.add(RVAS.initThunk), "int", ["pointer", "pointer"], nativeOptions);
    const decodeThunk = new NativeFunction(base.add(RVAS.decodeThunk), "int", ["pointer", "pointer"], nativeOptions);
    const terminate = new NativeFunction(base.add(RVAS.terminate), "int", ["pointer"]);
    const bytes = new Uint8Array(data);
    const n = bytes.length;
    const inbuf = Memory.alloc(n);
    inbuf.writeByteArray(bytes);

    const requested = options.variants && options.variants.length ? options.variants : [0, 1, 2, 3, 4];
    const batchLines = options.batchLines || 1;
    const lineLimit = options.lineLimit || 2344;
    const initKind = options.kind || 1;
    const focusBlockOnly = !!options.focusBlockOnly;
    const focusEntropyOnly = !!options.focusEntropyOnly;
    const focusRowdecodeOnly = !!options.focusRowdecodeOnly;
    const focusWaveletOnly = !!options.focusWaveletOnly;
    const rowTraceStores = !!options.rowTraceStores;
    const enableTrace = !!options.trace && !focusBlockOnly && !focusEntropyOnly && !focusRowdecodeOnly && !focusWaveletOnly;
    const enableDeepTrace = !!options.deepTrace;
    const dumpBytes = options.dumpBytes || 0;
    const rowTraceSampleLimit = options.rowTraceSampleLimit || 24;
    const waveletSampleCount = options.waveletSampleCount || 24;
    const results = [];
    const traceEvents = [];
    const blockSummaries = [];
    const traceLimit = options.traceLimit || 5000;
    let traceSeq = 0;
    let traceWrapped = false;
    function pushTrace(ev) {
      ev.seq = traceSeq++;
      if (traceEvents.length < traceLimit) {
        traceEvents.push(ev);
      } else {
        traceEvents[ev.seq % traceLimit] = ev;
        traceWrapped = true;
      }
    }
    const exceptions = [];
    let currentVariant = -1;
    Process.setExceptionHandler(function (details) {
      const ev = {
        name: "exception",
        variant: currentVariant,
        type: details.type,
        address: details.address ? details.address.toString() : null,
        memory: details.memory ? {
          operation: details.memory.operation,
          address: details.memory.address ? details.memory.address.toString() : null
        } : null
      };
      try {
        const c = details.context;
        ev.rip = c.rip.toString();
        ev.rax = c.rax.toString();
        ev.rbx = c.rbx.toString();
        ev.rcx = c.rcx.toString();
        ev.rdx = c.rdx.toString();
        ev.rdi = c.rdi.toString();
        ev.rsi = c.rsi.toString();
        ev.r8 = c.r8.toString();
        ev.r9 = c.r9.toString();
        ev.r10 = c.r10.toString();
        ev.r11 = c.r11.toString();
        ev.r12 = c.r12.toString();
        ev.r13 = c.r13.toString();
        ev.r14 = c.r14.toString();
        ev.r15 = c.r15.toString();
      } catch (e) {
        ev.contextError = String(e);
      }
      exceptions.push(ev);
      pushTrace(ev);
      return false;
    });
    function reg32(x) {
      return x.and(0xffffffff).toUInt32();
    }
    function stateFields(p) {
      try {
        if (p.isNull()) return null;
        return {
          p: p.toString(),
          nComp: p.add(0x10).readS32(),
          nImg: p.add(0x04).readS32(),
          outLinesMax: p.add(0x4c).readS32(),
          batchLines: p.add(0x50).readS32(),
          curY: p.add(0x384).readS32(),
          yChunk: p.add(0x388).readS32(),
          xChunk: p.add(0x38c).readS32(),
          curX: p.add(0x390).readS32()
        };
      } catch (e) {
        return { error: String(e) };
      }
    }
    function paramFields(p) {
      try {
        if (p.isNull()) return null;
        return {
          p: p.toString(),
          inPtr: p.readPointer().toString(),
          inLen: p.add(0x08).readU32(),
          out0: p.add(0x10).readPointer().toString(),
          out1: p.add(0x18).readPointer().toString(),
          out2: p.add(0x20).readPointer().toString(),
          size0: p.add(0x28).readU32(),
          size1: p.add(0x2c).readU32(),
          size2: p.add(0x30).readU32(),
          q0: p.add(0x34).readFloat(),
          q1: p.add(0x38).readFloat(),
          q2: p.add(0x3c).readFloat(),
          white: p.add(0x40).readU32(),
          line0: p.add(0x44).readU32(),
          line1: p.add(0x48).readU32(),
          line2: p.add(0x4c).readU32(),
          consumed: p.add(0x50).readU32()
        };
      } catch (e) {
        return { error: String(e) };
      }
    }
    function lineFields(p) {
      try {
        if (p.isNull()) return null;
        const o = { p: p.toString() };
        const i32Offsets = [
          0x08, 0x0c, 0x10, 0x14, 0x30, 0x34,
          0x50, 0x54, 0x58, 0x60, 0x68, 0x6c,
          0x70, 0x74, 0x78, 0x7c, 0x88, 0x8c
        ];
        const ptrOffsets = [0x18, 0x28, 0x38, 0x40, 0x48, 0x80];
        for (const off of i32Offsets) {
          try { o["i" + off.toString(16)] = p.add(off).readS32(); } catch (e) {}
        }
        for (const off of ptrOffsets) {
          try { o["p" + off.toString(16)] = p.add(off).readPointer().toString(); } catch (e) {}
        }
        try { o.f90 = p.add(0x90).readFloat(); } catch (e) {}
        try { o.f94 = p.add(0x94).readFloat(); } catch (e) {}
        return o;
      } catch (e) {
        return { error: String(e) };
      }
    }
    function i32ArraySafe(p, count) {
      const a = [];
      try {
        if (p.isNull()) return a;
        for (let i = 0; i < count; i++) a.push(p.add(i * 4).readS32());
      } catch (e) {
        a.push("ERR:" + String(e));
      }
      return a;
    }
    function i16ArraySafe(p, count) {
      const a = [];
      try {
        if (p.isNull()) return a;
        for (let i = 0; i < count; i++) a.push(p.add(i * 2).readS16());
      } catch (e) {
        a.push("ERR:" + String(e));
      }
      return a;
    }
    function ptrHeadI16(p, count) {
      try {
        if (p.isNull()) return { ptr: p.toString(), head: [] };
        if (p.toUInt32 && p.toUInt32() < 0x10000) return { ptr: p.toString(), small: true };
      } catch (e) {}
      return { ptr: p.toString(), head: i16ArraySafe(p, count) };
    }
    function compFields(p, sampleCount) {
      try {
        if (p.isNull()) return null;
        const data = p.add(0x28).readPointer();
        const count = p.add(0x38).readS32();
        const n = Math.max(0, Math.min(sampleCount || 0, count));
        return {
          p: p.toString(),
          data: data.toString(),
          count,
          head: i32ArraySafe(data, n)
        };
      } catch (e) {
        return { error: String(e) };
      }
    }
    function subbandFields(p) {
      try {
        if (p.isNull()) return null;
        const mode = p.add(0x18).readS32();
        const blockCount = p.add(0x1318).readS32();
        const out = {
          p: p.toString(),
          mode,
          blockCount,
          offset: p.add(0x131c).readS32(),
          length: p.add(0x1320).readS32(),
          comps: []
        };
        const compOffsets = mode === 0 ? [0x20] : [0x28, 0x30, 0x38];
        for (const off of compOffsets) {
          out.comps.push({ off, value: compFields(p.add(off).readPointer(), 12) });
        }
        const recs = [];
        const nrec = Math.max(0, Math.min(blockCount, 6));
        for (let i = 0; i < nrec; i++) {
          const r = p.add(0x60 + i * 0x10);
          try {
            const dataPtr = r.add(-8).readPointer();
            recs.push({
              index: i,
              data: dataPtr.toString(),
              len: r.readS32(),
              dataHead: hexdumpSafe(dataPtr, Math.min(24, Math.max(0, r.readS32())))
            });
          } catch (e) {
            recs.push({ index: i, error: String(e) });
          }
        }
        out.records = recs;
        return out;
      } catch (e) {
        return { error: String(e) };
      }
    }

    let activeBlock = null;
    let activeBlockSeq = 0;
    let rowTraceSamples = 0;
    function bitreaderFields(p) {
      try {
        if (p.isNull()) return null;
        return {
          p: p.toString(),
          cur: p.readU64().toString(),
          ptr: p.add(0x08).readPointer().toString(),
          bit: p.add(0x10).readS32(),
          wordsLeft: p.add(0x14).readS32(),
          status: p.add(0x1c).readS32()
        };
      } catch (e) {
        return { error: String(e) };
      }
    }
    function contextEvent(name, ctx) {
      return {
        name,
        variant: currentVariant,
        rip: ctx.rip.toString(),
        rax: ctx.rax.toString(),
        rbx: ctx.rbx.toString(),
        rcx: ctx.rcx.toString(),
        rdx: ctx.rdx.toString(),
        rdi: ctx.rdi.toString(),
        rsi: ctx.rsi.toString(),
        r8: ctx.r8.toString(),
        r9: ctx.r9.toString(),
        r10: ctx.r10.toString(),
        r11: ctx.r11.toString(),
        r12: ctx.r12.toString(),
        r13: ctx.r13.toString(),
        r14: ctx.r14.toString(),
        r15: ctx.r15.toString(),
        eax: reg32(ctx.rax),
        ebx: reg32(ctx.rbx),
        ecx: reg32(ctx.rcx),
        edx: reg32(ctx.rdx),
        edi: reg32(ctx.rdi),
        esi: reg32(ctx.rsi),
        r8d: reg32(ctx.r8),
        r9d: reg32(ctx.r9),
        r10d: reg32(ctx.r10),
        r11d: reg32(ctx.r11),
        r12d: reg32(ctx.r12),
        r13d: reg32(ctx.r13),
        r14d: reg32(ctx.r14),
        r15d: reg32(ctx.r15)
      };
    }
    function addHook(rva, name, extra) {
      Interceptor.attach(base.add(rva), {
        onEnter() {
          const ev = contextEvent(name, this.context);
          try {
            if (extra) extra.call(this, ev);
          } catch (e) {
            ev.extraError = String(e);
          }
          pushTrace(ev);
        }
      });
    }
    function entropyFocusAllows(name) {
      if (!focusEntropyOnly) return true;
      return [
        "block_decode",
        "entropy_decode",
        "entropy_run_update",
        "entropy_magnitude4",
        "entropy_sign4",
        "entropy_alt4"
      ].indexOf(name) >= 0;
    }
    function addDeepHook(rva, name, extra, leaveExtra) {
      if (!enableDeepTrace) return;
      if (focusBlockOnly && name !== "block_decode") return;
      if (focusRowdecodeOnly && ["line_decode_dispatch", "rowdecode"].indexOf(name) < 0) return;
      if (focusWaveletOnly && [
        "wavelet_output",
        "wavelet_merge",
        "line_helper_ab2b0",
        "line_helper_ab570",
        "line_helper_aafd0",
        "wavelet_horiz_add00",
        "wavelet_vert_ade80",
        "wavelet_vert_ae000",
        "simd_stage_ad530",
        "simd_stage_ac6e0",
        "simd_stage_ad830"
      ].indexOf(name) < 0) return;
      if (!entropyFocusAllows(name)) return;
      Interceptor.attach(base.add(rva), {
        onEnter() {
          const ev = contextEvent(name + "_enter", this.context);
          try {
            if (extra) extra.call(this, ev);
          } catch (e) {
            ev.extraError = String(e);
          }
          pushTrace(ev);
          this._deepName = name;
          this._deepArg0 = this.context.rcx;
          if (name === "block_decode") this._blockSummaryIndex = blockSummaries.length - 1;
        },
        onLeave(retval) {
          const leave = {
            name: this._deepName + "_leave",
            variant: currentVariant,
            ret: retval.toString()
          };
          try {
            if (this._deepName === "block_decode") {
              leave.obj = subbandFields(this._deepArg0);
              if (this._blockSummaryIndex !== undefined && blockSummaries[this._blockSummaryIndex]) {
                blockSummaries[this._blockSummaryIndex].ret = retval.toString();
                blockSummaries[this._blockSummaryIndex].leaveObj = leave.obj;
              }
            }
            if (this._deepName === "entropy_decode") {
              leave.outPtr = this._entropyOut ? this._entropyOut.toString() : null;
              leave.outHead = this._entropyOut ? hexdumpSafe(this._entropyOut, this._entropyBytes) : null;
              leave.outI32 = this._entropyOut ? i32ArraySafe(this._entropyOut, Math.min(16, this._entropyBytes / 4)) : null;
              leave.count = this._entropyCount;
              leave.bytes = this._entropyBytes;
              leave.bitreaderAfter = bitreaderFields(this._entropyBitreader);
            }
            if (leaveExtra) leaveExtra.call(this, leave, retval);
          } catch (e) {
            leave.extraError = String(e);
          }
          pushTrace(leave);
        }
      });
    }
    let zeroRowScratch = NULL;
    const lastAafd0R9 = {};
    function maybePatchAafd0R9(ev) {
      const key = this.context.rcx.toString();
      if (this.context.r9.isNull()) {
        if (options.patchZeroRow) {
          let repl = lastAafd0R9[key];
          if (repl === undefined) {
            if (zeroRowScratch.isNull()) {
              zeroRowScratch = Memory.alloc(2 * 1024 * 1024);
              zeroRowScratch.writeByteArray(new Uint8Array(2 * 1024 * 1024));
            }
            repl = zeroRowScratch;
          }
          ev.patchedR9From = "0x0";
          ev.patchedR9To = repl.toString();
          this.context.r9 = repl;
        } else {
          ev.nullR9 = true;
        }
      }
      if (!this.context.r9.isNull()) {
        lastAafd0R9[key] = this.context.r9;
      }
    }
    if (enableTrace) {
      addHook(0x1a5e70, "after_parse_ret", function (ev) {
        ev.parseRet = ev.eax;
        ev.consumed = this.context.rsp.add(0x70).readU32();
        ev.decParamPtr = this.context.rsp.add(0x78).readPointer().toString();
        ev.extraNeed = this.context.rsp.add(0x80).readU32();
        ev.param = paramFields(this.context.rsp.add(0x78).readPointer());
        ev.state = stateFields(this.context.rbx);
      });
      addHook(0x1a5f82, "check_out_ptr", function (ev) {
        ev.ptrAtRax = this.context.rax.readPointer().toString();
        ev.param = paramFields(this.context.r15);
        ev.state = stateFields(this.context.rbx);
      });
      addHook(0x1a5fb0, "divide_y_chunk", function (ev) {
        ev.dividend = ev.eax;
        ev.divisor = ev.ecx;
        ev.param = paramFields(this.context.r15);
        ev.state = stateFields(this.context.rbx);
      });
      addHook(0x1a6033, "cmp_x_chunk", function (ev) {
        ev.computed = ev.eax;
        ev.expected = ev.edi;
        ev.param = paramFields(this.context.r15);
        ev.state = stateFields(this.context.rbx);
      });
      addHook(0x1a608f, "cmp_state_lines", function (ev) {
        ev.stateLines = this.context.rbx.add(0x4c).readS32();
        ev.requestLines = ev.edi;
        ev.param = paramFields(this.context.r15);
        ev.state = stateFields(this.context.rbx);
      });
      addHook(0x1a6479, "return_minus_9", function (ev) {
        ev.param = paramFields(this.context.r15);
        ev.state = stateFields(this.context.rbx);
      });
      addHook(0x1a6480, "return_minus_4", function (ev) {
        ev.param = paramFields(this.context.r15);
        ev.state = stateFields(this.context.rbx);
      });
    }
    if (enableDeepTrace) {
      addDeepHook(0x1aa3f0, "block_decode", function (ev) {
        activeBlockSeq += 1;
        activeBlock = {
          seq: activeBlockSeq,
          srcPtr: this.context.rdx.toString(),
          obj: lineFields(this.context.rcx)
        };
        blockSummaries.push({
          seq: activeBlockSeq,
          variant: currentVariant,
          srcPtr: activeBlock.srcPtr,
          enterObj: activeBlock.obj,
          srcHead: hexdumpSafe(this.context.rdx, 32)
        });
        ev.block = activeBlock;
        ev.obj = lineFields(this.context.rcx);
        ev.srcHead = hexdumpSafe(this.context.rdx, 32);
      });
      addDeepHook(0x1aa9f0, "entropy_decode", function (ev) {
        this._entropyBitreader = this.context.rdx;
        this._entropyOut = this.context.rsp.add(0x28).readPointer();
        this._entropyCount = reg32(this.context.r9);
        this._entropyBytes = Math.max(0, Math.min(256, this._entropyCount * 16));
        ev.objFlags48 = this.context.rcx.add(0x48).readU32();
        ev.bitreader = bitreaderFields(this.context.rdx);
        ev.widthOrStep = reg32(this.context.r8);
        ev.count = this._entropyCount;
        ev.outPtr = this._entropyOut.toString();
        ev.activeBlock = activeBlock;
      });
      addDeepHook(0x1a9080, "entropy_run_update", function (ev) {
        this._runBitreader = this.context.rcx;
        this._runStatePtr = this.context.rdx;
        ev.bitreader = bitreaderFields(this.context.rcx);
        ev.statePtr = this.context.rdx.toString();
        ev.stateBefore = this.context.rdx.readS32();
      }, function (leave) {
        leave.bitreaderAfter = bitreaderFields(this._runBitreader);
        leave.statePtr = this._runStatePtr ? this._runStatePtr.toString() : null;
        leave.stateAfter = this._runStatePtr ? this._runStatePtr.readS32() : null;
      });
      addDeepHook(0x1a8b00, "entropy_magnitude4", function (ev) {
        this._magBitreader = this.context.rcx;
        this._magOut = this.context.rdx;
        ev.bitreader = bitreaderFields(this.context.rcx);
        ev.outPtr = this.context.rdx.toString();
        ev.outBefore = i32ArraySafe(this.context.rdx, 4);
        ev.widthBits = reg32(this.context.r8);
        ev.shiftParam = reg32(this.context.r9);
      }, function (leave) {
        leave.bitreaderAfter = bitreaderFields(this._magBitreader);
        leave.outPtr = this._magOut ? this._magOut.toString() : null;
        leave.outAfter = this._magOut ? i32ArraySafe(this._magOut, 4) : null;
      });
      addDeepHook(0x1a8dd0, "entropy_sign4", function (ev) {
        this._signBitreader = this.context.rcx;
        this._signOut = this.context.rdx;
        ev.bitreader = bitreaderFields(this.context.rcx);
        ev.outPtr = this.context.rdx.toString();
        ev.outBefore = i32ArraySafe(this.context.rdx, 4);
      }, function (leave) {
        leave.bitreaderAfter = bitreaderFields(this._signBitreader);
        leave.outPtr = this._signOut ? this._signOut.toString() : null;
        leave.outAfter = this._signOut ? i32ArraySafe(this._signOut, 4) : null;
      });
      addDeepHook(0x1ac060, "entropy_alt4", function (ev) {
        this._altBitreader = this.context.rcx;
        this._altOut = this.context.rdx;
        ev.bitreader = bitreaderFields(this.context.rcx);
        ev.outPtr = this.context.rdx.toString();
        ev.outBefore = i32ArraySafe(this.context.rdx, 4);
        ev.widthBits = reg32(this.context.r8);
        ev.shiftParam = reg32(this.context.r9);
        ev.hasNext = this.context.rsp.add(0x28).readU8();
      }, function (leave, retval) {
        leave.bitreaderAfter = bitreaderFields(this._altBitreader);
        leave.outPtr = this._altOut ? this._altOut.toString() : null;
        leave.outAfter = this._altOut ? i32ArraySafe(this._altOut, 4) : null;
        leave.nextRunOrWidth = retval.toInt32();
      });
      addDeepHook(0x1a9cc0, "wavelet_output", function (ev) {
        ev.obj = lineFields(this.context.rcx);
      });
      addDeepHook(0x1a9a40, "wavelet_merge", function (ev) {
        ev.obj = lineFields(this.context.rcx);
      });
      addDeepHook(0x1a9550, "line_object_init", function (ev) {
        ev.obj = lineFields(this.context.rcx);
      });
      addDeepHook(0x1ab2b0, "line_helper_ab2b0", function (ev) {
        ev.obj = lineFields(this.context.rcx);
        ev.baseHead = i16ArraySafe(this.context.rdx, Math.min(waveletSampleCount, 256));
        ev.rowIndex = reg32(this.context.r8);
      });
      addDeepHook(0x1ab570, "line_helper_ab570", function (ev) {
        ev.obj = lineFields(this.context.rcx);
        ev.detailHead = i16ArraySafe(this.context.r8, 16);
        ev.src0Head = i16ArraySafe(this.context.rdx, 16);
        ev.src1Head = i16ArraySafe(this.context.r9, 16);
        ev.widthA = this.context.rsp.add(0x28).readS32();
        ev.widthB = this.context.rsp.add(0x30).readS32();
      });
      addDeepHook(0x1aafd0, "line_helper_aafd0", function (ev) {
        ev.obj = lineFields(this.context.rcx);
        ev.srcI16 = i16ArraySafe(this.context.rdx, Math.min(waveletSampleCount, 256));
        ev.rowIndex = reg32(this.context.r8);
        maybePatchAafd0R9.call(this, ev);
      });
      addDeepHook(0x1add00, "wavelet_horiz_add00", function (ev) {
        this._add00Args = {
          rcx: this.context.rcx,
          rdx: this.context.rdx,
          r8: this.context.r8,
          r9: this.context.r9
        };
        ev.args = {
          rcx: ptrHeadI16(this.context.rcx, waveletSampleCount),
          rdx: ptrHeadI16(this.context.rdx, waveletSampleCount),
          r8: ptrHeadI16(this.context.r8, waveletSampleCount),
          r9: ptrHeadI16(this.context.r9, waveletSampleCount),
          rsp28: ptrHeadI16(this.context.rsp.add(0x28).readPointer(), waveletSampleCount),
          rsp30: this.context.rsp.add(0x30).readS32(),
          rsp38: this.context.rsp.add(0x38).readS32()
        };
      }, function (leave) {
        const a = this._add00Args || {};
        leave.after = {
          rcx: a.rcx ? ptrHeadI16(a.rcx, 24) : null,
          rdx: a.rdx ? ptrHeadI16(a.rdx, waveletSampleCount) : null,
          r8: a.r8 ? ptrHeadI16(a.r8, waveletSampleCount) : null,
          r9: a.r9 ? ptrHeadI16(a.r9, waveletSampleCount) : null
        };
      });
      addDeepHook(0x1ade80, "wavelet_vert_ade80", function (ev) {
        this._ade80Args = {
          rcx: this.context.rcx,
          rdx: this.context.rdx,
          r8: this.context.r8,
          r9: this.context.r9
        };
        ev.args = {
          rcx: ptrHeadI16(this.context.rcx, waveletSampleCount),
          rdx: ptrHeadI16(this.context.rdx, waveletSampleCount),
          r8: ptrHeadI16(this.context.r8, waveletSampleCount),
          r9: ptrHeadI16(this.context.r9, waveletSampleCount),
          rsp28: ptrHeadI16(this.context.rsp.add(0x28).readPointer(), waveletSampleCount),
          rsp30: this.context.rsp.add(0x30).readS32(),
          rsp38: this.context.rsp.add(0x38).readS32()
        };
      }, function (leave) {
        const a = this._ade80Args || {};
        leave.after = {
          rcx: a.rcx ? ptrHeadI16(a.rcx, 24) : null,
          rdx: a.rdx ? ptrHeadI16(a.rdx, waveletSampleCount) : null,
          r8: a.r8 ? ptrHeadI16(a.r8, waveletSampleCount) : null,
          r9: a.r9 ? ptrHeadI16(a.r9, waveletSampleCount) : null
        };
      });
      addDeepHook(0x1ae000, "wavelet_vert_ae000", function (ev) {
        this._ae000Args = {
          rcx: this.context.rcx,
          rdx: this.context.rdx,
          r8: this.context.r8,
          r9: this.context.r9
        };
        ev.args = {
          rcx: ptrHeadI16(this.context.rcx, waveletSampleCount),
          rdx: ptrHeadI16(this.context.rdx, waveletSampleCount),
          r8: ptrHeadI16(this.context.r8, waveletSampleCount),
          r9: ptrHeadI16(this.context.r9, waveletSampleCount),
          rsp28: ptrHeadI16(this.context.rsp.add(0x28).readPointer(), waveletSampleCount),
          rsp30: this.context.rsp.add(0x30).readS32(),
          rsp38: this.context.rsp.add(0x38).readS32()
        };
      }, function (leave) {
        const a = this._ae000Args || {};
        leave.after = {
          rcx: a.rcx ? ptrHeadI16(a.rcx, 24) : null,
          rdx: a.rdx ? ptrHeadI16(a.rdx, waveletSampleCount) : null,
          r8: a.r8 ? ptrHeadI16(a.r8, waveletSampleCount) : null,
          r9: a.r9 ? ptrHeadI16(a.r9, waveletSampleCount) : null
        };
      });
      addDeepHook(0x1ad530, "simd_stage_ad530", function (ev) {});
      addDeepHook(0x1ac6e0, "simd_stage_ac6e0", function (ev) {});
      addDeepHook(0x1ad830, "simd_stage_ad830", function (ev) {});
    }
    if (enableDeepTrace && focusRowdecodeOnly) {
      addDeepHook(0x1a8130, "line_decode_dispatch", function (ev) {
        if (rowTraceSamples >= rowTraceSampleLimit) {
          ev.skippedBySampleLimit = true;
          return;
        }
        ev.obj = lineFields(this.context.rcx);
        ev.lineIndex = reg32(this.context.rdx);
      });
      addDeepHook(0x1a7ed0, "rowdecode", function (ev) {
        if (rowTraceSamples >= rowTraceSampleLimit) {
          ev.skippedBySampleLimit = true;
          return;
        }
        const arg5 = this.context.rsp.add(0x28).readPointer();
        const ret = this.context.rsp.readPointer();
        ev.returnRva = ret.sub(base).toString();
        ev.decoderFlags48 = this.context.rcx.add(0x48).readU32();
        ev.bitreader = bitreaderFields(this.context.rdx);
        ev.lineIndex = reg32(this.context.r8);
        ev.lineCount = reg32(this.context.r9);
        ev.lineObj = lineFields(arg5);
        ev.stackArg5 = arg5.toString();
        ev.stackFlag = this.context.rsp.add(0x30).readU8();
      });
      if (rowTraceStores) {
      addHook(0x1a8046, "rowdecode_store_integrated", function (ev) {
        if (rowTraceSamples >= rowTraceSampleLimit) {
          ev.skippedBySampleLimit = true;
          return;
        }
        rowTraceSamples += 1;
        const groups = reg32(this.context.rbp);
        const byteCount = groups * 8;
        const dst = this.context.rbx.sub(byteCount);
        ev.lineIndex = reg32(this.context.rdi);
        ev.groups = groups;
        ev.byteCount = byteCount;
        ev.dst = dst.toString();
        ev.dstI16 = i16ArraySafe(dst, Math.min(32, groups * 4));
        ev.tmp = this.context.rsi.toString();
        ev.tmpI32 = i32ArraySafe(this.context.rsi, Math.min(32, groups * 4));
        ev.lineObj = lineFields(this.context.r13);
      });
      addHook(0x1a80f4, "rowdecode_store_copy", function (ev) {
        if (rowTraceSamples >= rowTraceSampleLimit) {
          ev.skippedBySampleLimit = true;
          return;
        }
        rowTraceSamples += 1;
        const groups = reg32(this.context.rbp);
        const byteCount = groups * 8;
        const dst = this.context.rbx.sub(byteCount);
        ev.lineIndex = reg32(this.context.rdi);
        ev.groups = groups;
        ev.byteCount = byteCount;
        ev.dst = dst.toString();
        ev.dstI16 = i16ArraySafe(dst, Math.min(32, groups * 4));
        ev.tmp = this.context.rsi.toString();
        ev.tmpI32 = i32ArraySafe(this.context.rsi, Math.min(32, groups * 4));
        ev.lineObj = lineFields(this.context.r13);
      });
      }
    }
    if (options.patchZeroRow && !enableDeepTrace) {
      Interceptor.attach(base.add(0x1aafd0), {
        onEnter() {
          const ev = contextEvent("line_helper_aafd0_patch_enter", this.context);
          try {
            ev.obj = lineFields(this.context.rcx);
            maybePatchAafd0R9.call(this, ev);
          } catch (e) {
            ev.extraError = String(e);
          }
          if (ev.patchedR9To || ev.nullR9) pushTrace(ev);
        }
      });
    }

    for (const variant of requested) {
      currentVariant = variant;
      const obj = Memory.alloc(0x40);
      obj.writeByteArray(new Uint8Array(0x40));
      obj.writePointer(base.add(RVAS.vtable));

      const init = Memory.alloc(0x80);
      init.writeByteArray(new Uint8Array(0x80));
      init.add(0x00).writeU32(batchLines);
      init.add(0x04).writeU32(variant);
      init.add(0x10).writePointer(inbuf);
      init.add(0x18).writeU32(n);
      init.add(0x1c).writeU32(initKind);
      init.add(0x28).writeU32(lineLimit);
      const initRet = initThunk(obj, init);
      const statePtr = obj.add(0x08).readPointer();

      const comps = init.add(0x30).readU32();
      const bps = init.add(0x2c).readU32();
      const widths = readU32Array(init, 0x34, Math.max(3, comps));
      const heights = readU32Array(init, 0x40, Math.max(3, comps));
      const strides = readU32Array(init, 0x4c, Math.max(3, comps));
      const sizes = [];
      const outs = [];
      const fills = [0x55, 0x66, 0x77, 0x88];
      for (let i = 0; i < comps; i++) {
        const size = Math.max(0x1000, strides[i] * heights[i]);
        sizes.push(size);
        const p = Memory.alloc(size);
        p.writeByteArray(new Uint8Array(size).fill(fills[i] || 0xaa));
        outs.push(p);
      }

      const dec = Memory.alloc(0x80);
      dec.writeByteArray(new Uint8Array(0x80));
      dec.add(0x00).writePointer(inbuf);
      dec.add(0x08).writeU32(n);
      for (let i = 0; i < comps; i++) {
        dec.add(0x10 + 8 * i).writePointer(outs[i]);
        dec.add(0x28 + 4 * i).writeU32(sizes[i]);
        dec.add(0x34 + 4 * i).writeFloat(1.0);
      }
      dec.add(0x40).writeU32(16383);

      let decodeRet = 0;
      let decodeError = null;
      const t0 = Date.now();
      try {
        decodeRet = decodeThunk(obj, dec);
      } catch (e) {
        decodeRet = -999999;
        decodeError = String(e.stack || e);
      }
      const decodeMs = Date.now() - t0;

      const outStats = [];
      for (let i = 0; i < comps; i++) {
        if (dumpBytes > 0) {
          const dn = Math.min(sizes[i], dumpBytes);
          try {
            send({ type: "dump", variant, index: i, size: dn }, outs[i].readByteArray(dn));
          } catch (e) {
            pushTrace({ name: "dump_error", variant, index: i, error: String(e) });
          }
        }
        outStats.push({
          index: i,
          ptr: outs[i].toString(),
          size: sizes[i],
          firstChanged: firstChanged(outs[i], sizes[i], fills[i] || 0xaa),
          checksum32: checksum32(outs[i], sizes[i]),
          head: hexdumpSafe(outs[i], Math.min(128, sizes[i]))
        });
      }

      results.push({
        variant,
        initRet,
        decodeRet,
        decodeError,
        decodeMs,
        comps,
        bps,
        widths,
        heights,
        strides,
        sizes,
        initdump: hexdumpSafe(init, 0x70),
        statedump: hexdumpSafe(statePtr, 0x3b0),
        stateFields: stateFields(statePtr),
        decdump: hexdumpSafe(dec, 0x70),
        outStats
      });

      if (!options.noTerminate) {
        try { terminate(obj); } catch (e) {}
      }
    }
    const orderedTrace = traceEvents.slice().sort(function (a, b) {
      return (a.seq || 0) - (b.seq || 0);
    });
    return { base: base.toString(), n, options, head: hexdumpSafe(inbuf, 64), results, blockSummaries, traceEvents: orderedTrace, traceSeq, traceWrapped, exceptions };
  }
};
"""


def find_edit_process() -> int | None:
    dev = frida.get_local_device()
    for proc in dev.enumerate_processes():
        if proc.name.lower() == EDIT_MODULE.lower():
            return proc.pid
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="out/raw_strip.bin", help="raw strip containing the ARW6 stream")
    ap.add_argument("--out", default="out/reverse/frida_llvc_sized_probe.json")
    ap.add_argument("--variant", action="append", type=int, help="variant to try, repeatable")
    ap.add_argument("--batch-lines", type=int, default=1, help="init-param +0x00, clamped by decoder to 1..32")
    ap.add_argument("--line-limit", type=int, default=2344, help="init-param +0x28, copied to decoder state +0x4c")
    ap.add_argument("--kind", type=int, default=1, help="init-param +0x1c; Imaging Edge accepts at least 1 and 2")
    ap.add_argument("--trace", action="store_true", help="hook a few decode-core checkpoints")
    ap.add_argument("--deep-trace", action="store_true", help="hook line/wavelet/subband helpers around the crash")
    ap.add_argument("--trace-limit", type=int, default=5000, help="maximum trace events retained as a ring buffer")
    ap.add_argument("--steal-exceptions", action="store_true", help="ask Frida to steal native exceptions from NativeFunction calls")
    ap.add_argument("--no-terminate", action="store_true", help="skip LLVCDecoder::Terminate after probing")
    ap.add_argument("--patch-zero-row", action="store_true", help="try replacing a NULL final-row temp pointer with the previous row pointer")
    ap.add_argument("--focus-block-only", action="store_true", help="with --deep-trace, only retain subband block decode traces")
    ap.add_argument("--focus-entropy-only", action="store_true", help="with --deep-trace, only retain coefficient entropy decode traces")
    ap.add_argument("--focus-rowdecode-only", action="store_true", help="with --deep-trace, only retain row entropy/postprocess traces")
    ap.add_argument("--focus-wavelet-only", action="store_true", help="with --deep-trace, only retain wavelet synthesis helper traces")
    ap.add_argument("--row-trace-sample-limit", type=int, default=24, help="maximum sampled row store events for --focus-rowdecode-only")
    ap.add_argument("--wavelet-sample-count", type=int, default=24, help="number of i16 samples captured from wavelet helper buffers")
    ap.add_argument("--row-trace-stores", action="store_true", help="also hook row store sites; useful but intrusive on the hot path")
    ap.add_argument("--dump-prefix", default="", help="write output plane dumps with this filename prefix")
    ap.add_argument("--dump-bytes", type=int, default=0, help="bytes to dump from each output plane")
    ap.add_argument("--spawn", action="store_true", help="spawn Edit.exe instead of attaching")
    ns = ap.parse_args()

    raw = Path(ns.raw).read_bytes()[RAW_STREAM_OFFSET:]
    dev = frida.get_local_device()
    spawned = False
    if ns.spawn:
        pid = dev.spawn([EDIT_EXE])
        spawned = True
    else:
        pid = find_edit_process()
        if pid is None:
            pid = dev.spawn([EDIT_EXE])
            spawned = True

    sess = dev.attach(pid)
    script = sess.create_script(JS)
    def on_message(message, data):
        if message.get("type") == "send":
            payload = message.get("payload")
            if isinstance(payload, dict) and payload.get("type") == "dump" and ns.dump_prefix and data is not None:
                dump_path = Path(f"{ns.dump_prefix}_v{payload['variant']}_c{payload['index']}.bin")
                dump_path.parent.mkdir(parents=True, exist_ok=True)
                dump_path.write_bytes(bytes(data))
                print(json.dumps({"dump": str(dump_path), "bytes": len(data)}))
        elif message.get("type") == "error":
            print(json.dumps({"frida_error": message}, ensure_ascii=False))

    script.on("message", on_message)
    script.load()
    if spawned:
        dev.resume(pid)
        time.sleep(2)

    options = {
        "variants": ns.variant or [0, 1, 2, 3, 4],
        "batchLines": ns.batch_lines,
        "lineLimit": ns.line_limit,
        "kind": ns.kind,
        "trace": ns.trace,
        "deepTrace": ns.deep_trace,
        "traceLimit": ns.trace_limit,
        "stealExceptions": ns.steal_exceptions,
        "noTerminate": ns.no_terminate,
        "patchZeroRow": ns.patch_zero_row,
        "focusBlockOnly": ns.focus_block_only,
        "focusEntropyOnly": ns.focus_entropy_only,
        "focusRowdecodeOnly": ns.focus_rowdecode_only,
        "focusWaveletOnly": ns.focus_wavelet_only,
        "rowTraceSampleLimit": ns.row_trace_sample_limit,
        "rowTraceStores": ns.row_trace_stores,
        "dumpBytes": ns.dump_bytes,
        "waveletSampleCount": ns.wavelet_sample_count,
    }
    result = script.exports_sync.probe(options, raw)
    out = Path(ns.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"pid": pid, "out": str(out), "decodeRets": [r["decodeRet"] for r in result["results"]]}, indent=2))
    sess.detach()


if __name__ == "__main__":
    main()
