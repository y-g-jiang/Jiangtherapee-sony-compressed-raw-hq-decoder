#!/usr/bin/env python3
"""Trace the LLVCDecoder calls made by the real Imaging Edge UI.

Run it, open or refresh an ARW6 in Viewer/Edit, and save the captured parameter
blocks for checking the standalone probes.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import frida


MODULES = {
    "Edit.exe": {
        "exe": r"C:\Program Files\Sony\Imaging Edge\Edit.exe",
        "init": 0x1A50A0,
        "decode": 0x1A5180,
        "decode_core": 0x1A5D10,
        "init_core": 0x1A6610,
        "parse": 0x1A7BA0,
        "block": 0x1AA3F0,
    },
    "Viewer.exe": {
        "exe": r"C:\Program Files\Sony\Imaging Edge\Viewer.exe",
        "init": 0x193BB0,
        "decode": 0x193C90,
        "decode_core": 0x194820,  # decode thunk moves [this+8] then jumps here in Viewer
        "init_core": 0x195120,
        "parse": 0x1966B0,
        "block": 0x198F00,
    },
}


JS = r"""
const moduleName = __MODULE__;
const rvas = __RVAS__;
const logs = [];
let seq = 0;

function hx(p, n) {
  try { return hexdump(p, { length: n, ansi: false }); } catch (e) { return String(e); }
}

function u32(p, off) {
  try { return p.add(off).readU32(); } catch (e) { return null; }
}

function pptr(p, off) {
  try { return p.add(off).readPointer().toString(); } catch (e) { return null; }
}

function param(p) {
  if (!p || p.isNull()) return null;
  return {
    p: p.toString(),
    p00: pptr(p, 0x00), u08: u32(p, 0x08), u0c: u32(p, 0x0c),
    p10: pptr(p, 0x10), p18: pptr(p, 0x18), p20: pptr(p, 0x20),
    u28: u32(p, 0x28), u2c: u32(p, 0x2c), u30: u32(p, 0x30),
    u34: u32(p, 0x34), u38: u32(p, 0x38), u3c: u32(p, 0x3c),
    u40: u32(p, 0x40), u44: u32(p, 0x44), u48: u32(p, 0x48),
    u4c: u32(p, 0x4c), u50: u32(p, 0x50), u54: u32(p, 0x54),
    dump: hx(p, 0x80)
  };
}

function state(p) {
  if (!p || p.isNull()) return null;
  return {
    p: p.toString(),
    initFlag: u32(p, 0),
    nImg: u32(p, 4),
    imgPtrs: pptr(p, 8),
    nComp: u32(p, 0x10),
    compPtrs: pptr(p, 0x18),
    kindA: u32(p, 0x28),
    width: u32(p, 0x2c),
    halfHeight: u32(p, 0x30),
    mode: u32(p, 0x34),
    bits: u32(p, 0x38),
    flags: u32(p, 0x3c),
    lineLimit: u32(p, 0x4c),
    batchLines: u32(p, 0x50),
    curY: u32(p, 0x384),
    yChunk: u32(p, 0x388),
    xChunk: u32(p, 0x38c),
    curX: u32(p, 0x390),
    dumpHead: hx(p, 0x90)
  };
}

function add(name, data) {
  data.name = name;
  data.seq = seq++;
  data.t = Date.now();
  logs.push(data);
  send(data);
}

rpc.exports = {
  getlogs() { return logs; }
};

const base = Process.getModuleByName(moduleName).base;

Interceptor.attach(base.add(rvas.init), {
  onEnter(args) {
    this.obj = args[0];
    this.par = args[1];
    add("init_enter", { obj: this.obj.toString(), param: param(this.par) });
  },
  onLeave(retval) {
    let st = NULL;
    try { st = this.obj.add(8).readPointer(); } catch (e) {}
    add("init_leave", { ret: retval.toInt32(), obj: this.obj.toString(), state: state(st), param: param(this.par) });
  }
});

Interceptor.attach(base.add(rvas.decode), {
  onEnter(args) {
    this.obj = args[0];
    this.par = args[1];
    add("decode_enter", { obj: this.obj.toString(), param: param(this.par) });
  },
  onLeave(retval) {
    let st = NULL;
    try { st = this.obj.add(8).readPointer(); } catch (e) {}
    add("decode_leave", { ret: retval.toInt32(), obj: this.obj.toString(), state: state(st), param: param(this.par) });
  }
});

Interceptor.attach(base.add(rvas.decode_core), {
  onEnter(args) {
    this.st = args[0];
    this.par = args[1];
    add("decode_core_enter", { state: state(this.st), param: param(this.par) });
  },
  onLeave(retval) {
    add("decode_core_leave", { ret: retval.toInt32(), state: state(this.st), param: param(this.par) });
  }
});

Interceptor.attach(base.add(rvas.parse), {
  onEnter(args) {
    this.st = args[0];
    this.outConsumed = args[3];
    this.outNeedPtr = this.context.rsp.add(0x28).readPointer();
  },
  onLeave(retval) {
    let desc = NULL;
    let consumed = null;
    let need = null;
    try {
      consumed = this.outConsumed.readU32();
      desc = this.outConsumed.add(8).readPointer();
      need = this.outNeedPtr.readU32();
    } catch (e) {}
    add("parse_leave", { ret: retval.toInt32(), consumed, need, desc: param(desc), state: state(this.st) });
  }
});
"""


def find_process(name: str) -> int | None:
    dev = frida.get_local_device()
    for proc in dev.enumerate_processes():
        if proc.name.lower() == name.lower():
            return proc.pid
    return None


def on_message(message, data):
    if message.get("type") == "send":
        payload = message.get("payload")
        if isinstance(payload, dict):
            print(json.dumps({"event": payload.get("name"), "seq": payload.get("seq")}, ensure_ascii=False))
    elif message.get("type") == "error":
        print(json.dumps(message, indent=2, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", choices=MODULES, default="Edit.exe")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--spawn", action="store_true")
    ap.add_argument("--file", default="")
    ap.add_argument("--out", default="out/reverse/frida_llvc_real_calls.json")
    ns = ap.parse_args()

    info = MODULES[ns.module]
    dev = frida.get_local_device()
    if ns.spawn:
        argv = [info["exe"]]
        if ns.file:
            argv.append(ns.file)
        pid = dev.spawn(argv)
        spawned = True
    else:
        pid = find_process(ns.module)
        if pid is None:
            argv = [info["exe"]]
            if ns.file:
                argv.append(ns.file)
            pid = dev.spawn(argv)
            spawned = True
        else:
            spawned = False

    sess = dev.attach(pid)
    code = JS.replace("__MODULE__", json.dumps(ns.module)).replace("__RVAS__", json.dumps({k: v for k, v in info.items() if isinstance(v, int)}))
    script = sess.create_script(code)
    script.on("message", on_message)
    script.load()
    if spawned:
        dev.resume(pid)
    print(json.dumps({"pid": pid, "module": ns.module, "spawned": spawned, "seconds": ns.seconds}, ensure_ascii=False))
    time.sleep(ns.seconds)
    logs = script.exports_sync.getlogs()
    out = Path(ns.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(logs, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"out": str(out), "events": len(logs)}, ensure_ascii=False))
    sess.detach()


if __name__ == "__main__":
    main()
