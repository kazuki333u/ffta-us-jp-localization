#! python3
"""Mission panel: JP retail's own page templates, with the US substitution slots.

The defect
----------

The pub's mission detail panel and the quest-accept confirmation panel print
their field labels in English on every build from RC8 to RC11 --
``Fee / Dispatch / Items / To Clear / Available for / Reward / Gil /
Req. Items / Req. Skills / Req. Jobs`` -- while every *value* is Japanese
(``支払いずみ``, ``バトルで勝利``, ``600 Gil``).  RC8 documented this as a
deliberate residual: the panel is drawn by its own template renderer, and
pointing root entry 9 of ``fx_text`` at the relocated JP leaf (RC1..RC7) made
the panel **blank** and close by itself, so RC8 pointed root 9 and the three
code literals back at the pristine English leaf ``US 0x084C3798``.

Root cause -- a substitution token that eats template characters
----------------------------------------------------------------

RC8's hypothesis ("the JP template lines carry JP-retail column positions
``0x40 0x3E xx`` laid out for the JP panel") is **wrong**, and so is the
follow-up guess that the trailing ``日`` glyph is the problem.  Driving the
panel on RC11 with a per-line and then per-token bisect of the leaf
(``build/rc12_gameplay_audit/20260901_run``) isolates it exactly:

* one JP line at a time in an otherwise pristine leaf: lines 1, 2, 4, 5, 6, 7,
  8, 10, 12, 13, 15, 16 all render, page and close normally.  **Only line 3
  (the page-4 template) kills the panel**, with the JP column positions
  (``t_e3``-style pads) *or* with the US ones (``P1``), so the pads are
  innocent -- the JP pads in fact align the colons exactly as JP retail does.
* inside line 3 the trigger is the substitution token ``{04 1D}`` (the
  "available for" field).  ``US`` writes ``{04 1D}`` followed by the four
  characters ``days``; JP retail writes ``{04 1D}`` followed by the single
  character ``日``.  The US consumer **consumes four character slots of the
  template** at that token and continues after them: with 4 slots the panel is
  correct, with 5 the fifth character survives and is printed after the value,
  with 3 it still renders, with 2 the page is drawn with corrupt rows and the
  panel stops accepting input, with 1 the panel renders once and then never
  pages or closes, and with 0 the page is discarded and the panel closes by
  itself -- exactly the RC1..RC7 symptom.
* the same census over every line finds a second, milder case: line 2 (the
  page-3 template) gives ``{04 18}`` four characters where the US template
  gives five, so the consumer eats one token too many -- the row break -- and
  the panel prints ``必要スキル ：－必要ジョブ ：－`` on one row.

Two template lines are therefore one and three character slots short for the
**US** consumer.  Nothing else in the ten JP page templates diverges: the
census of every ``{04 xx}`` token and its literal run (US vs JP, per line) is
clean apart from those two, and where JP has *more* literal characters than
the US (line 1's ``ギル``, line 8's four one-character runs) the extra
characters are printed, which is what JP retail intends.

The fix -- data only, ten template lines, 0 executable bytes
------------------------------------------------------------

This layer rebuilds ``fx_text`` leaf 9 from the **relocated JP leaf** the
localization chain already produced (still present in the RC11 image, left
unreferenced by RC8), pads the two short substitution runs to the slot count
the US consumer requires, and points root entry 9 and the three code literals
(``0x0804ED3C``, ``0x0804FD14``, ``0x08050134``) at the rebuilt leaf in the
ROM tail padding.  The padding characters are consumed by the substitution and
never drawn; the run's own last character is repeated so no new glyph is
introduced.  The trailing sub-page of the pristine leaf (offset ``+1464``,
the two-row grid the panel's other consumers read) is copied verbatim to the
same relative offset, so every byte any consumer can reach through root 9 at
or past that offset is what RC11 already served.

Runtime on the candidate: all four pages of the mission detail panel in
Japanese (``情報料 / 派遣 / アイテム / 終了条件 / 有効期限``,
``必要アイテム / 必要スキル / 必要ジョブ``, ``終了条件 / 報酬 ... ギル``),
paged 4/4 -> 1/4 and back 1/4 -> 4/4, closed with B, reopened and closed
again, the pub menu, the ``クエストをうける`` branch
(``とくにクエストはないようだな。``) and the world map after it all unchanged;
the mission route (deploy, ``バトルを開始しますか？``, ``TO WIN:`` banner,
GET READY!, the battle) unchanged; every EWRAM arena walk clean.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import ffta_jp_system_menu_geometry as prev
import ffta_jp_coverage_audit as coverage

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/mission_panel_templates"
OUTROM = ROOT / "rom/build/ffta_us_jp_mission_panel_templates.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_mission_panel_templates_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_system_menu_geometry (RC11).
BASELINE = "66FB6EFEDB7A7832C2E6DFBF0802190C217A840423B82E0E9AEDD2C95EA5EB1F"
# Pinned after the first deterministic build of this layer.
EXPECTED_PRODUCTION = "E019B74FBE7EC7C9F6759CABDFC3179162740108450F837BC0C3E59F29BACBED"

US_LEAF = 0x004C3798                 # pristine fx_text leaf 9 (RC8..RC11 serve this one)
US_LEAF_END = 0x004C3E60             # = root entry 1
US_TAIL_OFS = 1464                   # nested sub-page inside the leaf, kept at the same offset
NLINES = 17
ROOT9 = 0x0036D69C                   # fx_text root table + 9*4
LITERALS = (0x0004ED3C, 0x0004FD14, 0x00050134)
NEW_LEAF = 0x00B10000                # 0xFF padding inside the 5 MB free run 0xB019FC..0xFE0000
NEW_LEAF_GAP = 0x800
LEAF0_SIGNATURE = bytes.fromhex("31188100801c88808088908aff00")

# The two short runs, their US/JP lengths (a gate on the derived census) and how
# to lengthen them.  "after" repeats the run's last character at the end -- the
# whole run is a placeholder the substitution eats, as in the US template.
# "before" inserts the filler *in front* of the run, so JP retail's own trailing
# character (line 3's ``日``) survives the substitution and is printed after the
# value, which is what JP retail shows.
SLOT_FIXES = {
    (2, 0x18): {"us": 5, "jp": 4, "where": "after"},
    (3, 0x1D): {"us": 4, "jp": 1, "where": "before"},
}

# fx_text token codec (the same tables as ffta_sect.c_ffta_sect_text_buf).
TOKBASE = 0x21
TOKLEN = {}
for _c in (0x40, 0x41, 0x42, 0x4A, 0x4D, 0x4F, 0x52, 0x54, 0x56, 0x57, 0x58):
    TOKLEN[_c] = 0
for _c in (0x00, 0x1B, 0x1D, 0x46, 0x4B, 0x51, 0x53, 0x32, 0x04):
    TOKLEN[_c] = 1
TOKLEN[0x45] = 2
TOKSPEC = (0x32, 0x04)
CTR_SUBST = 0x04                     # "replace with a runtime value" -- the slot-eating token


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


# ---------------------------------------------------------------- codec ----

def _flip(d, ln, fl):
    for _ in range(ln):
        d.append(d[len(d) - fl - 1])


def decompress(src, si, dst_len):
    """The line codec's LZ (ffta_sect.c_ffta_sect_text_line._decompress)."""
    d = bytearray()
    while len(d) < dst_len:
        cmd = src[si]; si += 1
        if cmd & 0x80:
            c1 = src[si]; si += 1
            _flip(d, ((cmd >> 3) & 0xF) + 3, ((cmd & 7) << 8) | c1)
        elif cmd & 0x40:
            ln = (cmd & 0x3F) + 1
            d.extend(src[si:si + ln]); si += ln
        elif cmd & 0x20:
            d.extend(b"\x00" * ((cmd & 0x1F) + 2))
        elif cmd & 0x10:
            c1, c2 = src[si], src[si + 1]; si += 2
            _flip(d, (((c1 & 0xC0) >> 2) | (cmd & 0xF)) + 4, ((c1 & 0x3F) << 8) | c2)
        elif cmd == 0x2:
            c1 = src[si]; si += 1; d.extend(b"\x00" * (c1 + 3))
        elif cmd == 0x1:
            c1 = src[si]; si += 1; d.extend(b"\xff" * (c1 + 3))
        elif cmd == 0x0:
            c1, c2, c3 = src[si], src[si + 1], src[si + 2]; si += 3
            _flip(d, c1 + 5, (c2 << 8) | c3)
        else:
            raise RuntimeError("MPT_LZ_COMMAND %02X" % cmd)
    return bytes(d), si


def tokens(buf):
    """(kind, value, offset, length) for one uncompressed token stream."""
    out = []
    i = 0
    while i < len(buf):
        c = buf[i]
        if c == 0:
            out.append(("EOS", 0, i, 1)); i += 1; break
        if c == 1:
            out.append(("CHR", buf[i + 1], i, 2)); i += 2; continue
        if c == 0x40:
            k = buf[i + 1] - TOKBASE
            if k in TOKSPEC:
                out.append(("CTR", (k << 8) | buf[i + 2], i, 3)); i += 3; continue
            if k not in TOKLEN:
                raise RuntimeError("MPT_CTR_UNKNOWN %02X" % buf[i + 1])
            n = TOKLEN[k]
            v = k
            for j in range(n):
                v = (v << 8) | buf[i + 2 + j]
            out.append(("CTR", v, i, 2 + n)); i += 2 + n; continue
        if c & 0x80:
            out.append(("CHR", ((c & 0x7F) << 8) | buf[i + 1], i, 2)); i += 2; continue
        raise RuntimeError("MPT_TOKEN_UNKNOWN %02X at %d" % (c, i))
    return out


def token_bytes(raw, start):
    """Length of an uncompressed token stream, including its terminating 0x00."""
    j = start
    while True:
        c = raw[j]
        if c == 0:
            return j + 1 - start
        if c == 0x40:
            k = raw[j + 1] - TOKBASE
            j += 3 if k in TOKSPEC else 2 + TOKLEN[k]
        elif c == 1 or c & 0x80:
            j += 2
        else:
            raise RuntimeError("MPT_TOKEN_UNKNOWN %02X" % c)


def line_segments(raw, base, leaf_end=None):
    """The 17 raw line segments of a leaf, in leaf order."""
    if u16(raw, base + 2 * NLINES) != 0xFFFF:
        raise RuntimeError("MPT_LEAF_NOT_17_LINES 0x%08X" % (base + ROM))
    ofs = [u16(raw, base + 2 * i) for i in range(NLINES)]
    if ofs[0] != 2 * (NLINES + 1) or any(ofs[i + 1] <= ofs[i] for i in range(NLINES - 1)):
        raise RuntimeError("MPT_LEAF_TABLE_SHAPE 0x%08X" % (base + ROM))
    out = []
    for i, o in enumerate(ofs):
        if i + 1 < NLINES:
            ln = ofs[i + 1] - o
        else:
            s = base + o
            flags = u16(raw, s)
            if flags & 2:
                dst_len = int.from_bytes(raw[s + 2:s + 6], "big")
                _, si = decompress(raw, s + 6, dst_len)
                ln = si - s
            elif flags & 1:
                if leaf_end is None:
                    raise RuntimeError("MPT_LEAF_END_UNKNOWN")
                ln = leaf_end - o
            else:
                ln = 2 + token_bytes(raw, s + 2)
            ln += ln & 1
        out.append(bytes(raw[base + o:base + o + ln]))
    return out


def line_tokens(seg):
    """(flags, token list, payload offset) -- None for the 'ya' class lines."""
    flags = u16(seg, 0)
    if flags & 1:
        return flags, None, None
    if flags & 2:
        dst_len = int.from_bytes(seg[2:6], "big")
        buf, _ = decompress(seg, 6, dst_len)
        return flags, tokens(buf), None
    return flags, tokens(seg[2:]), 2


def substitution_runs(toks):
    """[(field id, literal characters that follow, index of the last of them)]."""
    out = []
    for i, (kind, value, _o, _l) in enumerate(toks):
        if kind != "CTR" or (value >> 8) != CTR_SUBST:
            continue
        n = 0
        j = i + 1
        while j < len(toks) and toks[j][0] == "CHR":
            n += 1; j += 1
        out.append((value & 0xFF, n, j - 1))
    return out


# ---------------------------------------------------------------- build ----

def find_relocated_leaf(img: bytes) -> int:
    """The relocated JP leaf 9, derived from the image (never a magic address)."""
    cands = []
    i = -1
    while True:
        i = img.find(LEAF0_SIGNATURE, i + 1)
        if i < 0:
            break
        base = i - 2 * (NLINES + 1)
        if base < 0 or base & 1:
            continue
        try:
            line_segments(img, base)
        except RuntimeError:
            continue
        cands.append(base)
    if len(cands) != 2 or US_LEAF not in cands:
        raise RuntimeError("MPT_LEAF_CANDIDATES %s" % [hex(c + ROM) for c in cands])
    return [c for c in cands if c != US_LEAF][0]


def pad_runs(us_segs, jp_segs):
    """Lengthen every JP substitution run the US consumer expects to be longer."""
    padded = list(jp_segs)
    applied = {}
    for i in range(NLINES):
        _fu, tu, _p = line_tokens(us_segs[i])
        fj, tj, payload = line_tokens(jp_segs[i])
        if tu is None or tj is None:
            continue
        ru, rj = substitution_runs(tu), substitution_runs(tj)
        if len(ru) != len(rj) or sorted(f for f, _n, _k in ru) != sorted(f for f, _n, _k in rj):
            raise RuntimeError("MPT_FIELD_SHAPE line %d" % i)
        if payload is None:
            if any(n_us > n_jp for (_f, n_us, _k), (_g, n_jp, _l) in zip(ru, rj)):
                raise RuntimeError("MPT_COMPRESSED_LINE_NEEDS_PADDING line %d" % i)
            continue
        seg = bytearray(padded[i])
        for (fu, n_us, _ku), (fj_id, n_jp, k_jp) in sorted(zip(ru, rj),
                                                           key=lambda p: p[1][2], reverse=True):
            if n_us <= n_jp:
                continue
            if fu != fj_id:
                raise RuntimeError("MPT_FIELD_ID_MISMATCH line %d %02X %02X" % (i, fu, fj_id))
            rule = SLOT_FIXES.get((i, fj_id))
            if rule is None or rule["us"] != n_us or rule["jp"] != n_jp:
                raise RuntimeError("MPT_UNEXPECTED_SHORT_RUN line %d field 0x%02X %d<%d"
                                   % (i, fj_id, n_jp, n_us))
            kind, _v, off, ln = tj[k_jp]
            if kind != "CHR" or ln != 2:
                raise RuntimeError("MPT_RUN_TAIL line %d" % i)
            if rule["where"] == "after":
                at = payload + off + 2
                filler = bytes(seg[payload + off:payload + off + 2]) * (n_us - n_jp)
            else:
                first = tj[k_jp - n_jp + 1]
                at = payload + first[2]
                filler = bytes(seg[at:at + 2]) * n_us
            seg[at:at] = filler
            applied[(i, fj_id)] = len(filler) // 2
        padded[i] = bytes(seg)
    want = {k: (v["us"] if v["where"] == "before" else v["us"] - v["jp"]) for k, v in SLOT_FIXES.items()}
    if applied != want:
        raise RuntimeError("MPT_PADDING_CENSUS %s" % {f"{k[0]}:{k[1]:02X}": v for k, v in applied.items()})
    return padded, applied


def assemble(segs, tail):
    body = bytearray()
    ofs = []
    pos = 2 * (NLINES + 1)
    for s in segs:
        ofs.append(pos)
        body.extend(s)
        pos += len(s)
    leaf = b"".join(struct.pack("<H", o) for o in ofs) + b"\xff\xff" + bytes(body)
    if len(leaf) > US_TAIL_OFS:
        raise RuntimeError("MPT_LEAF_OVER_TAIL %d" % len(leaf))
    return leaf + b"\x00" * (US_TAIL_OFS - len(leaf)) + tail


def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"MPT_BASELINE_MISMATCH {sha(base)}")
    us_raw = US.read_bytes()
    if base[US_LEAF:US_LEAF_END] != us_raw[US_LEAF:US_LEAF_END]:
        raise RuntimeError("MPT_PRISTINE_LEAF_MODIFIED")
    for field in (ROOT9,) + LITERALS:
        if int.from_bytes(base[field:field + 4], "little") != ROM + US_LEAF:
            raise RuntimeError("MPT_FIELD_NOT_PRISTINE 0x%08X" % (ROM + field))
    if set(base[NEW_LEAF:NEW_LEAF + NEW_LEAF_GAP]) != {0xFF} or \
            set(us_raw[NEW_LEAF:NEW_LEAF + NEW_LEAF_GAP]) != {0xFF}:
        raise RuntimeError("MPT_GAP_NOT_FREE")
    n_words = len(base) // 4
    for off, v in enumerate(struct.unpack_from("<%dI" % n_words, base, 0)):
        if ROM + NEW_LEAF <= v < ROM + NEW_LEAF + NEW_LEAF_GAP:
            raise RuntimeError("MPT_GAP_REFERENCED 0x%08X" % (ROM + off * 4))

    relocated = find_relocated_leaf(base)
    us_segs = line_segments(base, US_LEAF, US_LEAF_END - US_LEAF)
    jp_segs = line_segments(base, relocated)
    padded, applied = pad_runs(us_segs, jp_segs)
    tail = bytes(base[US_LEAF + US_TAIL_OFS:US_LEAF_END])
    leaf = assemble(padded, tail)

    raw = bytearray(base)
    raw[NEW_LEAF:NEW_LEAF + len(leaf)] = leaf
    for field in (ROOT9,) + LITERALS:
        struct.pack_into("<I", raw, field, ROM + NEW_LEAF)
    meta = {
        "relocated_source_leaf": f"0x{ROM + relocated:08X}",
        "pristine_leaf": f"0x{ROM + US_LEAF:08X}",
        "new_leaf": f"0x{ROM + NEW_LEAF:08X}",
        "new_leaf_bytes": len(leaf),
        "fields_repointed": [f"0x{ROM + f:08X}" for f in (ROOT9,) + LITERALS],
        "substitution_slots_added": {f"line {k[0]} field 0x{k[1]:02X}": v for k, v in applied.items()},
        "tail_offset": US_TAIL_OFS,
    }
    return bytes(raw), base, meta, previous[3], previous[4]


def validate(product, base, meta, block_start, block_end):
    leaf_len = meta["new_leaf_bytes"]
    diff = {i for i in range(len(base)) if product[i] != base[i]}
    expected = set(range(NEW_LEAF, NEW_LEAF + leaf_len))
    for field in (ROOT9,) + LITERALS:
        expected |= set(range(field, field + 4))
    if diff - expected:
        raise RuntimeError(f"MPT_UNEXPLAINED_BYTES {sorted(hex(d) for d in diff - expected)[:8]}")
    for field in (ROOT9,) + LITERALS:
        if int.from_bytes(product[field:field + 4], "little") != ROM + NEW_LEAF:
            raise RuntimeError("MPT_NOT_REPOINTED 0x%08X" % (ROM + field))
    # every consumer that reaches the tail through root 9 sees RC11's bytes
    if product[NEW_LEAF + US_TAIL_OFS:NEW_LEAF + leaf_len] != base[US_LEAF + US_TAIL_OFS:US_LEAF_END]:
        raise RuntimeError("MPT_TAIL_NOT_PRESERVED")
    # the rebuilt leaf parses, and its substitution runs now match the US ones
    us_segs = line_segments(base, US_LEAF, US_LEAF_END - US_LEAF)
    new_segs = line_segments(product, NEW_LEAF)
    census = []
    for i in range(NLINES):
        _fu, tu, _p = line_tokens(us_segs[i])
        _fj, tj, _q = line_tokens(new_segs[i])
        if tu is None or tj is None:
            continue
        ru, rj = substitution_runs(tu), substitution_runs(tj)
        for (fu, n_us, _a), (fj, n_jp, _b) in zip(ru, rj):
            if n_jp < n_us:
                raise RuntimeError("MPT_RUN_STILL_SHORT line %d field 0x%02X %d<%d" % (i, fj, n_jp, n_us))
        census.append({"line": i, "us": [[f, n] for f, n, _k in ru], "new": [[f, n] for f, n, _k in rj]})
    # the pristine leaf is left exactly where RC11 had it
    if product[US_LEAF:US_LEAF_END] != base[US_LEAF:US_LEAF_END]:
        raise RuntimeError("MPT_PRISTINE_LEAF_TOUCHED")
    return {
        "patch": meta,
        "substitution_census": census,
        "binary_touch": {"changed_ranges": 1 + len(LITERALS) + 1, "executable_bytes_changed": 0,
                         "literal_words_repointed": 1 + len(LITERALS), "code_patches": 0,
                         "relocated_bytes": leaf_len},
        "known_residual": "window title bars (MISSION / RANK / PAGE) stay JP-retail English",
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
        raise RuntimeError("MPT_BUILD_NONDETERMINISTIC")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    audits = validate(product, base, meta, bs, be)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    summary = {
        "milestone": "mission-panel-templates",
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
