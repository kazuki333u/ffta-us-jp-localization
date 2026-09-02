#! python3
"""Mission detail panel: read ``fx_text`` leaf 9 from the pristine US leaf again.

The defect
----------

The pub's mission detail panel (``クエストの中止`` -> ``LIST`` -> A, and by the
user's report the confirmation panel of ``クエストをうける``) shows its title bar
and window and **no text**, then closes by itself -- on RC1 through RC7, i.e.
since ``fx_text`` was localized.  Pristine US shows the panel.  Bisecting
the RC7 image against pristine US (``build/rc7_diversity_audit/20260831_run``,
``pc_t_*``) isolates the cause to **the relocated ``fx_text/9`` leaf reached
through root entry 9** (``US 0x0836D69C``): with the pristine leaf's bytes
placed at the relocated address the panel works, with any JP page template
line (line 3 = the page-4 template) it does not.  The panel has its own
template renderer: the JP template lines carry JP-retail column positions
(``0x40 0x3E xx``) and a trailing ``日`` glyph laid out for the JP panel
geometry, and the US renderer discards the page when a row overruns
(line 3 with the US column positions renders every JP label -- ``情報料 /
派遣 / アイテム / 終了条件 / 有効期限`` -- correctly; the US line with only the
JP trailing row fails).  Porting all ten template lines with re-derived
positions needs every page and the accept flow verified; that is deferred.

The fix -- data only, four words
--------------------------------

Root entry 9 and the three code literals that RC7 pointed at the relocated
leaf (``0x0804ED3C``, ``0x0804FD14``, ``0x08050134``) are written back to the
pristine leaf ``0x084C3798``.  Every consumer of leaf 9 then behaves exactly
as pristine US: the panel renders on every page and in every flow, with the
pristine English labels (``Fee`` / ``Dispatch`` / ``Items`` / ``To Clear`` /
``Available for`` / ``Reward`` ...) and Japanese values and mission names
(``薬草とり``, ``支払いずみ``, ``バトルで勝利``).  The relocated JP leaf stays in the
tail, unreferenced.  Zero executable bytes change.  **Known residual:** the
mission panel labels are English until the JP templates are ported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import ffta_jp_action_window_min as prev
import ffta_jp_coverage_audit as coverage

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/mission_panel_leaf"
OUTROM = ROOT / "rom/build/ffta_us_jp_mission_panel_leaf.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_mission_panel_leaf_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_action_window_min.
BASELINE = "988CA7989A5386C2E3E20C9A088C9B86E19CA3142F0B4DFEDE6275E9084BCC73"
# Pinned after the first deterministic build of this layer.
EXPECTED_PRODUCTION = "3F3F05C2E6FA7519EB2033EB4B4DEF30A8DF2D51E46829D7668B9395C926ADD5"

FX_ROOT_TABLE = 0x0036D678
LEAF_INDEX = 9
PRISTINE_LEAF = 0x084C3798
FIELDS = (FX_ROOT_TABLE + LEAF_INDEX * 4, 0x0004ED3C, 0x0004FD14, 0x00050134)
PRISTINE_LINES = 17


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def u32(b, off):
    return struct.unpack_from("<I", b, off)[0]


def page_line_count(img: bytes, leaf: int) -> int:
    n = 0
    while struct.unpack_from("<H", img, leaf + 2 * n)[0] != 0xFFFF and n < 4096:
        n += 1
    return n


def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"MPL_BASELINE_MISMATCH {sha(base)}")
    us_raw = US.read_bytes()
    for f in FIELDS:
        if u32(us_raw, f) != PRISTINE_LEAF:
            raise RuntimeError(f"MPL_PRISTINE_FIELD 0x{f:06X}")
    relocated = u32(base, FIELDS[0])
    if relocated == PRISTINE_LEAF or not (ROM <= relocated < ROM + len(base)):
        raise RuntimeError("MPL_ROOT_NOT_RELOCATED")
    for f in FIELDS[1:]:
        if u32(base, f) != relocated:
            raise RuntimeError(f"MPL_LITERAL_DRIFT 0x{f:06X}")
    if page_line_count(us_raw, PRISTINE_LEAF - ROM) != PRISTINE_LINES:
        raise RuntimeError("MPL_PRISTINE_LEAF_SHAPE")
    # the pristine leaf bytes are untouched in the base image
    end = PRISTINE_LEAF - ROM + 0x600
    if base[PRISTINE_LEAF - ROM:end] != us_raw[PRISTINE_LEAF - ROM:end]:
        raise RuntimeError("MPL_PRISTINE_LEAF_MODIFIED")
    raw = bytearray(base)
    for f in FIELDS:
        raw[f:f + 4] = PRISTINE_LEAF.to_bytes(4, "little")
    meta = {"relocated_leaf": f"0x{relocated:08X}", "pristine_leaf": f"0x{PRISTINE_LEAF:08X}",
            "fields": [f"0x{f:06X}" for f in FIELDS]}
    return bytes(raw), base, meta, previous[3], previous[4]


def validate(product, base, meta, block_start, block_end):
    diff = {i & ~3 for i in range(len(base)) if product[i] != base[i]}
    if diff != set(FIELDS):
        raise RuntimeError(f"MPL_UNEXPLAINED_BYTES {sorted(hex(d) for d in diff ^ set(FIELDS))}")
    for f in FIELDS:
        if u32(product, f) != PRISTINE_LEAF:
            raise RuntimeError(f"MPL_NOT_WRITTEN 0x{f:06X}")
    return {
        "fields": meta["fields"], "leaf": meta["pristine_leaf"],
        "relocated_leaf_left_unreferenced": meta["relocated_leaf"],
        "binary_touch": {"changed_ranges": len(FIELDS), "executable_bytes_changed": 0,
                         "literal_words_repointed": len(FIELDS), "code_patches": 0, "relocated_bytes": 0},
        "known_residual": "mission detail panel labels are English (pristine fx_text/9)",
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
        raise RuntimeError("MPL_BUILD_NONDETERMINISTIC")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    audits = validate(product, base, meta, bs, be)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    summary = {
        "milestone": "mission-panel-leaf",
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
