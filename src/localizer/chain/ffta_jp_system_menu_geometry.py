#! python3
"""World-map system menu: JP retail's own geometry (12 cells, 10 text columns).

The defect
----------

The world-map ``システム`` menu (``セーブ / ロード / オプション / タイトルへ戻る``)
is drawn 8 cells wide with 6 text columns on every build up to RC9:
``オプション`` (51 px, 7 columns) loses its last glyph and ``タイトルへ戻る``
(71 px, 9 columns) shows as ``タイトルへ`` -- reported by the user from play
(``build/rc7_diversity_audit/20260831_run/rc8_sys``).

Root cause (not a flow layout)
------------------------------

RC9's note that the menu "flows its items across the box width" was wrong.
The menu is a one-column grid whose items are rendered one per row into
per-item tile rectangles of ``columns x 2`` tiles.  Every geometry value of
this menu is a **US-side constant** spread over the world-map module's request
function ``US 0x0804393C`` and its definition builder ``US 0x08043ECC``:

* layout entry 5 (IWRAM ``0x030009E0 + 5*0x4C``): x = 11, y = 3, w = 8,
  h = 11 passed on the stack to the layout-init ``0x0808A268`` at
  ``0x080439E0`` (x and h share the register ``r4 = 0xB``);
* ``0x08043A1E``  ``movs r0, #6``  -> layout entry +0x4A, the text columns
  (the per-item tile stride);
* ``0x08043A3A``  ``movs r1, #8``  -> the definition builder's width
  (RAM definition +0xE -> window struct +0x12 -> box columns);
* ``0x08043A8E``  ``movs r1, #8``  -> the text area width handed to
  ``0x08016560`` (JP passes 12 there too).

JP retail keeps the same code but with x = 10, w = 12, columns = 10
(``JP 0x08041CA2..0x08041D18``; its definition builder copies a ROM template
``JP 0x08384D9C`` = ``.. 00 00 0C 0B ..``).  The RC9 experiment that floored
every 6-column box to 7 at the common box function re-wrapped this menu only
because it widened the *map* (the box descriptor) without widening the item
stride (+0x4A) -- the mismatch, not a flow layout, produced
``セーブ ロ / ード オプ / ...``.

The fix (4 immediates, one ``bl`` and a 20-byte stub in free padding)
--------------------------------------------------------------------

====================  ==========================  ==============================
US address            pristine / RC9              this layer
====================  ==========================  ==============================
``0x080439CA``        ``movs r0, #8``  (w)        ``movs r0, #0xC``
``0x080439E0``        ``bl 0x0808A268``           ``bl 0x0836D380`` (stub below)
``0x08043A1E``        ``movs r0, #6``  (columns)  ``movs r0, #0xA``
``0x08043A3A``        ``movs r1, #8``  (def w)    ``movs r1, #0xC``
``0x08043A8E``        ``movs r1, #8``  (text w)   ``movs r1, #0xC``
``0x0836D380``        ``FF`` padding              ``movs r0,#0xA / str r0,[sp,#0x10] /
                                                  str r0,[sp,#0x20] / movs r4,#0xA /
                                                  movs r0,#5 / ldr r7,=0x0808A269 /
                                                  bx r7``
====================  ==========================  ==============================

The stub replaces the layout-init call: it overwrites the two x slots of the
stack frame with JP's x = 10 (the caller stored ``r4 = 0xB`` there because x
and h coincide in the US), sets ``r4`` to 10 so that the caller's later
``strb r4, [layout+0x34]`` (the screen x of the entry) also stores 10, restores
``r0 = 5`` (the layout index) and tail-calls the real layout-init through
``r7``, which the caller reloads right after the call.  ``lr`` still points
into the caller, so the callee returns there.  ``r4`` is read only for that x
store after the call, then reloaded.  The stub lives in the 0xFF padding
right after RC9's stub (``0x0836D370..0x0836D37F``): all 0xFF in the pristine
ROM and in RC9, named by no aligned ROM word.

Runtime on the candidate (byte-identical to this build): the system menu is
12 cells (10..21) with 10 text columns, ``オプション`` and ``タイトルへ戻る``
complete, the cursor and the save-slot flow unchanged, the world-map MENU /
place list / town and pub menus untouched, the OPTIONS submenu and the
``タイトルへ戻る`` NOTICE reachable and unchanged
(``build/rc7_diversity_audit/20260831_run/r10d_sys``, ``r10d_sub``; the
regression suite boot 24/24, clan menu 15/15, enemy status 7/7, save/load
14/14, rumour list 24/24 frames pixel-identical to RC9, ``regression_r10.json``).  The tile run of the
items (``0x24B..0x29A`` at charbase ``0x06008000``) stays below the next used
tile (``0x2B0``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import ffta_jp_battle_menu_geometry as prev
import ffta_jp_coverage_audit as coverage

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/system_menu_geometry"
OUTROM = ROOT / "rom/build/ffta_us_jp_system_menu_geometry.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_system_menu_geometry_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_battle_menu_geometry (RC11 battle MENU geometry).
BASELINE = "A0715F8ACD4E4A66F79BC86A31344FBDDFE9A71D2D01194073206F32365C1F38"
# Pinned after the first deterministic build of this layer.
EXPECTED_PRODUCTION = "66FB6EFEDB7A7832C2E6DFBF0802190C217A840423B82E0E9AEDD2C95EA5EB1F"

LAYOUT_INIT = 0x0008A268                     # generic layout-entry init (10 stack args)
IMMEDIATES = {                               # offset: (old bytes, new bytes)
    0x000439CA: (bytes.fromhex("0820"), bytes.fromhex("0C20")),   # movs r0,#8  -> #0xC  (w)
    0x00043A1E: (bytes.fromhex("0620"), bytes.fromhex("0A20")),   # movs r0,#6  -> #0xA  (columns)
    0x00043A3A: (bytes.fromhex("0821"), bytes.fromhex("0C21")),   # movs r1,#8  -> #0xC  (definition w)
    0x00043A8E: (bytes.fromhex("0821"), bytes.fromhex("0C21")),   # movs r1,#8  -> #0xC  (text area w, 0x08016560)
}
SITE = 0x000439E0                            # bl 0x0808A268 in US 0x0804393C
SITE_OLD = bytes.fromhex("46F042FC")
STUB = 0x0036D380                            # 0xFF padding after RC9's stub (0x0836D370..7F)
STUB_GAP = 0x20
STUB_CODE = bytes.fromhex("0A20 0490 0890 0A24 0520 014F 3847 C046".replace(" ", "")) + (ROM + LAYOUT_INIT + 1).to_bytes(4, "little")
FUNC_START, FUNC_END = 0x0004393C, 0x00043AB0   # request function US 0x0804393C (checked unchanged apart from the sites)
JP_REFERENCE = {                             # JP retail constants this layer ports (documentation + gate)
    "x": 0x0A, "w": 0x0C, "h": 0x0B, "columns": 0x0A,
    "jp_request_function": "0x08041C1C", "jp_template": "0x08384D9C",
}


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def thumb_bl(site: int, target: int) -> bytes:
    off = (ROM + target) - (ROM + site + 4)
    if not (-0x400000 <= off < 0x400000):
        raise RuntimeError("SMG_BL_OUT_OF_RANGE")
    hi = 0xF000 | ((off >> 12) & 0x7FF)
    lo = 0xF800 | ((off >> 1) & 0x7FF)
    return bytes([hi & 0xFF, hi >> 8, lo & 0xFF, lo >> 8])


def words_naming(img: bytes, lo: int, hi: int):
    hits = []
    for off in range(0, len(img) & ~3, 4):
        v = int.from_bytes(img[off:off + 4], "little")
        if ROM + lo <= v < ROM + hi:
            hits.append(off)
    return hits


def jp_gate(jp_raw: bytes):
    """Re-verify the JP retail constants this layer ports (never trust the docstring)."""
    tmpl = jp_raw[0x00384D9C:0x00384D9C + 0x10]
    if tmpl[0xE] != JP_REFERENCE["w"] or tmpl[0xF] != JP_REFERENCE["h"]:
        raise RuntimeError(f"SMG_JP_TEMPLATE_DRIFT {tmpl.hex()}")
    # JP 0x08041CA2: movs r5,#0xA (x) ; 0x08041CAA: movs r0,#0xC (w) ; 0x08041CAE: movs r0,#0xB (h)
    if jp_raw[0x00041CA2:0x00041CA4] != bytes.fromhex("0A25") or jp_raw[0x00041CAA:0x00041CAC] != bytes.fromhex("0C20") \
            or jp_raw[0x00041CAE:0x00041CB0] != bytes.fromhex("0B20"):
        raise RuntimeError("SMG_JP_REQUEST_CONSTANTS_DRIFT")


def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"SMG_BASELINE_MISMATCH {sha(base)}")
    us_raw = US.read_bytes()
    jp_gate(JP.read_bytes())
    if base[SITE:SITE + 4] != SITE_OLD or us_raw[SITE:SITE + 4] != SITE_OLD:
        raise RuntimeError("SMG_SITE_DRIFT")
    for off, (old, _new) in IMMEDIATES.items():
        if base[off:off + 2] != old or us_raw[off:off + 2] != old:
            raise RuntimeError(f"SMG_IMMEDIATE_DRIFT 0x{ROM + off:08X}")
    if base[FUNC_START:FUNC_END] != us_raw[FUNC_START:FUNC_END]:
        raise RuntimeError("SMG_FUNCTION_MODIFIED_EARLIER")
    if set(base[STUB:STUB + STUB_GAP]) != {0xFF} or set(us_raw[STUB:STUB + STUB_GAP]) != {0xFF}:
        raise RuntimeError("SMG_GAP_NOT_FREE")
    if words_naming(base, STUB, STUB + STUB_GAP):
        raise RuntimeError("SMG_GAP_REFERENCED")
    raw = bytearray(base)
    for off, (_old, new) in IMMEDIATES.items():
        raw[off:off + 2] = new
    raw[SITE:SITE + 4] = thumb_bl(SITE, STUB)
    raw[STUB:STUB + len(STUB_CODE)] = STUB_CODE
    meta = {"site": f"0x{ROM + SITE:08X}", "stub": f"0x{ROM + STUB:08X}",
            "immediates": {f"0x{ROM + o:08X}": f"{old.hex()} -> {new.hex()}" for o, (old, new) in IMMEDIATES.items()},
            "jp_reference": JP_REFERENCE,
            "rule": "world-map system menu: x 11 -> 10, width 8 -> 12 cells, text columns 6 -> 10 (JP retail geometry)"}
    return bytes(raw), base, meta, previous[3], previous[4]


def validate(product, base, meta, block_start, block_end):
    diff = {i for i in range(len(base)) if product[i] != base[i]}
    expected = set(range(SITE, SITE + 4)) | set(range(STUB, STUB + len(STUB_CODE)))
    for off in IMMEDIATES:
        expected |= {off, off + 1}
    if diff - expected:
        raise RuntimeError(f"SMG_UNEXPLAINED_BYTES {sorted(hex(d) for d in diff - expected)[:8]}")
    from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    bl = list(md.disasm(bytes(product[SITE:SITE + 4]), ROM + SITE))
    if len(bl) != 1 or bl[0].mnemonic != "bl" or int(bl[0].op_str.lstrip("#"), 16) != ROM + STUB:
        raise RuntimeError("SMG_BL_DECODE")
    stub = [(i.mnemonic, i.op_str) for i in md.disasm(bytes(product[STUB:STUB + 16]), ROM + STUB)]
    want = [("movs", "r0, #0xa"), ("str", "r0, [sp, #0x10]"), ("str", "r0, [sp, #0x20]"), ("movs", "r4, #0xa"),
            ("movs", "r0, #5"), ("ldr", "r7, [pc, #4]"), ("bx", "r7"), ("mov", "r8, r8")]
    if stub != want:
        raise RuntimeError(f"SMG_STUB_DECODE {stub}")
    if int.from_bytes(product[STUB + 16:STUB + 20], "little") != ROM + LAYOUT_INIT + 1:
        raise RuntimeError("SMG_STUB_LITERAL")
    imm = {}
    for off in IMMEDIATES:
        ins = list(md.disasm(bytes(product[off:off + 2]), ROM + off))[0]
        imm[f"0x{ROM + off:08X}"] = f"{ins.mnemonic} {ins.op_str}"
    want_imm = {"0x080439CA": "movs r0, #0xc", "0x08043A1E": "movs r0, #0xa", "0x08043A3A": "movs r1, #0xc",
                "0x08043A8E": "movs r1, #0xc"}
    if imm != want_imm:
        raise RuntimeError(f"SMG_IMMEDIATE_DECODE {imm}")
    return {
        "patch": meta,
        "binary_touch": {"changed_ranges": 6, "executable_bytes_changed": 4 * 2 + 4 + len(STUB_CODE),
                         "literal_words_repointed": 0, "code_patches": 6, "relocated_bytes": 0},
        "request_function": {"start": f"0x{ROM + FUNC_START:08X}", "layout_init": f"0x{ROM + LAYOUT_INIT:08X}"},
        "earlier_milestones_unchanged": True,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20260901_production")
    ap.add_argument("--print-sha", action="store_true")
    args = ap.parse_args()
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError("ORIGINAL_ROM_HASH_MISMATCH")
    first = build()
    if args.print_sha:
        print(sha(first[0]))
        return
    second = build()
    product, base, meta, bs, be = first
    if sha(product) != sha(second[0]):
        raise RuntimeError("SMG_BUILD_NONDETERMINISTIC")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    audits = validate(product, base, meta, bs, be)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    summary = {
        "milestone": "system-menu-geometry",
        "baseline_sha256": sha(base),
        "production_sha256": sha(product),
        "rom": str(OUTROM),
        "determinism": {"sha256_1": sha(product), "sha256_2": sha(second[0]), "identical": True},
        "audits": audits,
    }
    write(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
