#!/usr/bin/env python3
"""Reader / writer for the FFTA name-entry keyboard table.

The name-entry keyboard is **not** a raw grid: it is an indexed table of ordinary
FFTA text records, one per keyboard page, and the engine builds the 6x10 cell
grid by walking that record's token stream.  This module is the single place
that knows the layout, so every consumer (analysis, production, audit) reads it
the same way.

Table layout (identical in both ROMs; ``0x08013E9C`` is the consumer)::

    base + page*2 : u16 offset of the record, relative to ``base``
    ...
    0xFFFF        : sentinel after the last offset
    base + offset : u16 flags, then the token stream (payload)

Token grammar is the shared text grammar of ``ffta_sect.c_ffta_sect_text_buf``:

* ``0x80..0xFF`` + 1 byte  -- CHR_FULL, font slot ``((b0 << 8) | b1) & 0x7FFF``
* ``0x40`` + cmd byte      -- CTR_FUNC; ``cmd - 0x21`` selects the operand count
* ``0x00``                 -- end of string

Only three control tokens occur in a keyboard record:

* ``40 6E`` (CTR_FUNC 0x4D) -- next row
* ``40 73`` (CTR_FUNC 0x52) -- blank cell (also the name buffer's pad token)
* ``40 63`` (CTR_FUNC 0x42) -- end of the page
* ``40 3E xx`` (CTR_FUNC 0x1D..) -- horizontal position, JP only; it separates
  the two five-column groups of a kana row and consumes no grid cell.

ROM addresses (always stated per ROM):

* JP keyboard table ``0x004A6920``, 6 pages, 0x2FC bytes
* US keyboard table ``0x004C3D50``, 2 pages, 0x10C bytes
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
JP_ROM = ROOT / "rom/original/FFTA_JP.gba"
US_ROM = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
CHARSET = HERE / "charset_us.json"

ROM = 0x08000000

JP_TABLE = 0x004A6920
JP_PAGES = 6
JP_TABLE_BYTES = 0x2FC
US_TABLE = 0x004C3D50
US_PAGES = 2
US_TABLE_BYTES = 0x10C

# CTR_FUNC command values (already decoded, i.e. raw byte minus 0x21)
CTR_ROW = 0x4D          # 40 6E -- start the next keyboard row
CTR_BLANK = 0x52        # 40 73 -- one blank cell
CTR_END = 0x42          # 40 63 -- end of page
CTR_XPOS = 0x1D         # 40 3E xx -- absolute column, JP kana pages only

CTR_OPERANDS = {  # cmd -> extra operand bytes, from c_ffta_sect_text_buf
    **{c: 0 for c in (0x40, 0x41, 0x42, 0x4A, 0x4D, 0x4F, 0x52, 0x54, 0x56, 0x57, 0x58)},
    **{c: 1 for c in (0x00, 0x1B, 0x1D, 0x46, 0x4B, 0x51, 0x53, 0x32, 0x04)},
    0x45: 2,
}

ROWS, COLS = 6, 10

# The one font slot that is NOT shared between the ROMs.  US slot 0xA5 is the
# US dash; the JP 長音符 lives at the high slot the A5 production layer
# installed.  See ffta_jp_chr_half_a5.HIGH_SLOT and PROJECT_STATE section 4.
US_DASH_SLOT = 0x00A5
JP_DASH_SLOT = 0x06D9


def read_u16(raw: bytes, off: int) -> int:
    return int.from_bytes(raw[off:off + 2], "little")


def parse_tokens(raw: bytes, off: int):
    """Decode one payload into ``[('CHR', slot) | ('CTR', cmd, operands)]``."""
    out = []
    i = off
    while True:
        c = raw[i]
        i += 1
        if c == 0:
            return out, i
        if c == 0x40:
            cmd = raw[i] - 0x21
            i += 1
            n = CTR_OPERANDS.get(cmd)
            if n is None:
                raise ValueError(f"unknown CTR_FUNC 0x{cmd:02X} at 0x{i - 2:06X}")
            ops = tuple(raw[i:i + n])
            i += n
            out.append(("CTR", cmd, ops))
            if cmd == CTR_END:
                return out, i
            continue
        if c & 0x80:
            slot = ((c & 0x7F) << 8) | raw[i]
            i += 1
            out.append(("CHR", slot))
            continue
        raise ValueError(f"unknown token 0x{c:02X} at 0x{i - 1:06X}")


def encode_tokens(tokens) -> bytes:
    buf = bytearray()
    for tok in tokens:
        if tok[0] == "CHR":
            slot = tok[1]
            buf.append(0x80 | ((slot >> 8) & 0x7F))
            buf.append(slot & 0xFF)
        else:
            buf.append(0x40)
            buf.append(tok[1] + 0x21)
            buf.extend(tok[2])
    return bytes(buf)


def read_table(raw: bytes, base: int, pages: int):
    """Return ``[(flags, tokens, payload_bytes)]`` for every page."""
    out = []
    for p in range(pages):
        off = read_u16(raw, base + p * 2)
        if off == 0xFFFF:
            raise ValueError(f"page {p} offset is the 0xFFFF sentinel")
        rec = base + off
        flags = read_u16(raw, rec)
        tokens, end = parse_tokens(raw, rec + 2)
        out.append((flags, tokens, raw[rec + 2:end]))
    return out


def build_table(pages) -> bytes:
    """Serialise ``[(flags, tokens)]`` into a table blob (header + records)."""
    header = 2 * (len(pages) + 1)
    body, offsets = bytearray(), []
    for flags, tokens in pages:
        offsets.append(header + len(body))
        body.extend(flags.to_bytes(2, "little"))
        body.extend(encode_tokens(tokens))
        body.append(0)                       # payload terminator
        if len(body) & 1:                    # keep every record u16-aligned
            body.append(0)
    out = bytearray()
    for off in offsets:
        out.extend(off.to_bytes(2, "little"))
    out.extend(b"\xff\xff")
    out.extend(body)
    return bytes(out)


def grid(tokens):
    """Replay a page's token stream into the engine's 6x10 cell grid.

    The engine fills cells left to right, ``CTR_ROW`` starts the next row and
    every cell not reached stays 0 (an unselectable hole).  ``CTR_XPOS`` sets the
    column explicitly.  Returns a ``ROWS x COLS`` list of the **stored 16-bit
    cell words**, exactly as the engine writes them: ``0x8000 | slot`` for a
    character, ``0x4073`` for a blank cell and ``0x0000`` for an empty one.
    Storing the whole token matters -- slot 0 is ``ぁ``, so a bare slot number
    would collide with the empty-cell sentinel.
    """
    cells = [[0] * COLS for _ in range(ROWS)]
    r = c = 0
    for tok in tokens:
        if tok[0] == "CHR":
            if r < ROWS and c < COLS:
                cells[r][c] = 0x8000 | tok[1]
            c += 1
        elif tok[1] == CTR_BLANK:
            if r < ROWS and c < COLS:
                cells[r][c] = 0x4000 | (CTR_BLANK + 0x21)
            c += 1
        elif tok[1] == CTR_ROW:
            r, c = r + 1, 0
        elif tok[1] == CTR_XPOS:
            pass          # visual only: the cell index keeps running
        elif tok[1] == CTR_END:
            break
    return cells


def charset():
    decode, _ = json.loads(CHARSET.read_text(encoding="utf-8"))
    return {int(k): v for k, v in decode.items()}


def render(cells, table) -> str:
    def one(v):
        if v == 0:
            return "."
        if v == 0x4073:
            return "_"
        slot = v & 0x7FFF
        if slot == JP_DASH_SLOT:
            return "ー"
        return table.get(slot, f"<{v:04X}>")
    return "\n".join("  " + " ".join(f"{one(v):<3}" for v in row) for row in cells)


def main() -> int:
    table = charset()
    for name, path, base, pages in (("JP", JP_ROM, JP_TABLE, JP_PAGES),
                                    ("US", US_ROM, US_TABLE, US_PAGES)):
        raw = path.read_bytes()
        print(f"===== {name} keyboard table 0x{base:06X} ({pages} pages)")
        for i, (flags, tokens, payload) in enumerate(read_table(raw, base, pages)):
            ctr = sorted({t[1] for t in tokens if t[0] == "CTR"})
            print(f"-- page {i}  flags=0x{flags:04X}  payload={len(payload)} bytes  "
                  f"controls={[f'0x{c:02X}' for c in ctr]}")
            print(render(grid(tokens), table))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
