#! python3
"""Stale literal-pool references to relocated text leaves.

The defect
----------

Every text leaf this chain relocates is reached by the game through a root
field that the relocating layer rewrites (`s_text` root table, `words:*`
entry tables, the six `pages:*` root fields, the 27 `fx_text` root fields).
Four of those leaves are **also** named directly by literal-pool words in
code, and nothing rewrote those, so the consumers behind them still read the
pristine English leaf:

==============  ==============  ===========================================  =======================================
leaf            pristine leaf   stale literal words (US)                     what the consumer shows
==============  ==============  ===========================================  =======================================
pages:battle    ``0x08497418``  ``0x08027484`` ``0x080274DC``                battle result messages: ``16 EXP gained!``,
                                                                             ``JP received!``, ``leveled up!`` ...
pages:condi     ``0x084B83A0``  ``0x08013DE0`` ``0x08013E34`` ``0x08013E98``  mission conditions: ``Win battle``,
                                                                             ``N more day(s)``, ``No Cancellations`` ...
fx_text/1       ``0x084C3E60``  ``0x0805493C`` ``0x08065C18`` ``0x0806DF5C``  confirmation dialogs: ``Discard ...?``,
                                                                             ``Begin battle?``, ``Confirm action. OK?``
fx_text/9       ``0x084C3798``  ``0x0804ED3C`` ``0x0804FD14`` ``0x08050134``  mission detail panel: ``To Clear:``,
                                                                             ``Reward:``, ``Req.Items:``, ``Freed!`` ...
==============  ==============  ===========================================  =======================================

Found by playing RC6: the first Ivalice battle shows ``16 EXP gained!`` in
English although ``pages:battle/20`` on the image reads
``経験値[440]ポイントをゲット!`` through its root field
(``build/rc7_gameplay_audit/20260831_run``). The whole-ROM English-residual
scan could not see this: it re-parses records through the roots, and every
record is Japanese there. Same class as the month-sheet second consumer of
RC6: **a relocated block is only safe when every literal that names the old
address is found and rewritten**, so the scan below runs over every moved
`words:*` record, every `pages:*` / `fx_text` leaf and every `s_text` leaf
(3,314 candidates) against every aligned word of the code region, and the
result must be exactly these eleven words.

The fix
-------

The eleven literal words are rewritten to the relocated leaf pointer that the
matching root field already holds. Zero executable bytes, zero relocation;
the relocated leaves have the same page format (u16 offset table, same line
count) as the pristine ones, which the root consumers already prove.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import ffta_jp_action_window as prev
import ffta_jp_coverage_audit as coverage
import ffta_sect

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/stale_literals"
OUTROM = ROOT / "rom/build/ffta_us_jp_stale_literals.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_stale_literals_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_action_window.
BASELINE = "4D4D8A265A8DCBCB723BC34AAEBF8A86C9B59B4864379BB30297A1ED9C739673"
# Pinned after the first deterministic build of this layer.
EXPECTED_PRODUCTION = "F654E8640F2E200C8C3ECD1819BDBD1D266C75E2C9CBB9FB2D440DC8F867FBF0"

CODE_REGION_END = 0x400000   # every literal pool of the game code lies below this
PAGE_ROOTS = {"pages:battle": 0x237F4, "pages:choice": 0x3F6B8, "pages:condi": 0x13D98,
              "pages:config": 0x94B8, "pages:quest/0": 0x13CD8, "pages:quest/1": 0x13CC0}
S_TEXT_ROOT_PTR = 0x9A88

# The exact stale set the analysis found; the build recomputes it and fails
# on any difference in either direction.
EXPECTED_STALE = {
    "pages:battle": (0x08497418, (0x0027484, 0x00274DC)),
    "pages:condi": (0x084B83A0, (0x0013DE0, 0x0013E34, 0x0013E98)),
    "fx_text/1": (0x084C3E60, (0x005493C, 0x0065C18, 0x006DF5C)),
    "fx_text/9": (0x084C3798, (0x004ED3C, 0x004FD14, 0x0050134)),
}


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def u32(b, off):
    return struct.unpack_from("<I", b, off)[0]


def moved_candidates(us_raw: bytes, base: bytes, us):
    """Every pristine pointer whose root field the chain has rewritten."""
    cands = {}
    for fam in us.tabs["words"]:
        ro = us.tabs["words"][fam].real_offset
        for idx in range(4000):
            p = u32(us_raw, ro + idx * 4)
            if not (ROM <= p < ROM + len(us_raw)):
                break
            if u32(base, ro + idx * 4) != p:
                cands[p] = (f"words:{fam}/{idx}", ro + idx * 4)
    fx = us.tabs["fx_text"]
    for i in range(fx.tsize):
        f = fx.real_offset + i * 4
        p = u32(us_raw, f)
        if u32(base, f) != p:
            cands[p] = (f"fx_text/{i}", f)
    for fam, f in PAGE_ROOTS.items():
        p = u32(us_raw, f)
        if u32(base, f) != p:
            cands[p] = (fam, f)
    root = u32(us_raw, S_TEXT_ROOT_PTR) - ROM
    for top in range(64):
        f = root + top * 4
        rel = u32(us_raw, f)
        if u32(base, f) != rel:
            cands[ROM + root + rel] = (f"s_text/{top}", f)
    return cands


def page_line_count(img: bytes, leaf: int) -> int:
    n = 0
    while u32(img, leaf + 2 * n) & 0xFFFF != 0xFFFF and n < 4096:
        n += 1
    return n


def plan(us_raw: bytes, base: bytes):
    us = ffta_sect.load_rom_us(str(US))
    cands = moved_candidates(us_raw, base, us)
    words = {}
    for off in range(0, CODE_REGION_END, 4):
        v = u32(base, off)
        if ROM <= v < ROM + len(base):
            words.setdefault(v, []).append(off)
    stale = {}
    for p, (name, field) in cands.items():
        offs = tuple(o for o in words.get(p, []) if o != field)
        if offs:
            stale[name] = (p, offs, field)
    found = {k: (v[0], v[1]) for k, v in stale.items()}
    if found != EXPECTED_STALE:
        raise RuntimeError(f"SL_STALE_SET_CHANGED {found}")
    plan_rows = []
    for name, (p, offs, field) in stale.items():
        new = u32(base, field)
        if not (ROM <= new < ROM + len(base)) or new == p:
            raise RuntimeError(f"SL_ROOT_NOT_RELOCATED {name}")
        # the relocated leaf is a page of the same shape as the pristine one
        n_old = page_line_count(us_raw, p - ROM)
        n_new = page_line_count(base, new - ROM)
        if n_old != n_new:
            raise RuntimeError(f"SL_LINE_COUNT {name} {n_old} != {n_new}")
        for o in offs:
            if u32(base, o) != p:
                raise RuntimeError(f"SL_LITERAL_DRIFT {name} 0x{o:06X}")
        plan_rows.append({"leaf": name, "pristine_leaf": f"0x{p:08X}", "relocated_leaf": f"0x{new:08X}",
                          "root_field": f"0x{field:06X}", "lines": n_old,
                          "stale_literals": [f"0x{o:06X}" for o in offs]})
    return plan_rows, len(cands)


def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"SL_BASELINE_MISMATCH {sha(base)}")
    prev_block_end = previous[4]
    us_raw = US.read_bytes()
    rows, ncand = plan(us_raw, base)
    raw = bytearray(base)
    for r in rows:
        new = int(r["relocated_leaf"], 16)
        for o in r["stale_literals"]:
            off = int(o, 16)
            raw[off:off + 4] = new.to_bytes(4, "little")
    meta = {"rows": rows, "candidates_scanned": ncand,
            "words_rewritten": sum(len(r["stale_literals"]) for r in rows)}
    return bytes(raw), base, meta, prev_block_end, prev_block_end


def validate(product, base, meta, block_start, block_end):
    us_raw = US.read_bytes()
    expected = set()
    for r in meta["rows"]:
        new = int(r["relocated_leaf"], 16)
        for o in r["stale_literals"]:
            off = int(o, 16)
            expected.add(off)
            if u32(product, off) != new:
                raise RuntimeError(f"SL_NOT_REWRITTEN {o}")
    diff = {i & ~3 for i in range(len(base)) if product[i] != base[i]}
    if diff != expected:
        raise RuntimeError(f"SL_UNEXPLAINED_BYTES {sorted(hex(d) for d in diff ^ expected)}")
    # after the rewrite no code-region word names any pristine relocated leaf
    us = ffta_sect.load_rom_us(str(US))
    cands = moved_candidates(us_raw, product, us)
    for off in range(0, CODE_REGION_END, 4):
        v = u32(product, off)
        if v in cands and off != cands[v][1]:
            raise RuntimeError(f"SL_STALE_REMAINS 0x{off:06X} {cands[v][0]}")
    return {
        "rows": meta["rows"],
        "candidates_scanned": meta["candidates_scanned"],
        "binary_touch": {"changed_ranges": len(expected), "executable_bytes_changed": 0,
                         "literal_words_repointed": len(expected), "code_patches": 0,
                         "relocated_bytes": 0},
        "post_condition": "no aligned word below 0x400000 names a pristine relocated leaf except its own root field",
        "earlier_milestones_unchanged": True,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20260831_production")
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
        raise RuntimeError("SL_BUILD_NONDETERMINISTIC")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    audits = validate(product, base, meta, bs, be)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    summary = {
        "milestone": "stale-literals",
        "baseline_sha256": sha(base),
        "production_sha256": sha(product),
        "rom": str(OUTROM),
        "determinism": {"sha256_1": sha(product), "sha256_2": sha(second[0]),
                        "identical": True},
        "audits": audits,
    }
    write(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
