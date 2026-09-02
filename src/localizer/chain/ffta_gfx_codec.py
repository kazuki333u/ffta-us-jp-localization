#!/usr/bin/env python3
"""FFTA graphics codecs, asset discovery and a 4bpp tile renderer.

Reusable per AGENTS.md.  ROM offsets are file offsets; CPU addresses are
``0x08000000 + offset``.  Always state which ROM (JP or US) an address
belongs to.

The two codecs
--------------

**Codec A** -- ``US 0x080051C4``.  Used for the sub-images of an ``A7``
sprite container.  The container is ``"A7" | u16be count | u32be total |
count * u32be sub_offset``; ``US 0x08005318`` computes ``base + total`` and
hands that to the decoder as the **codec block**: four descriptor bytes
(mode, shift, u16be output size) followed by a back-reference dictionary of
``(0xFFFF >> (shift + 1)) + 1`` bytes.  A back-reference reads the
*dictionary*, never the output, so the block travels with the container --
relocating one without the other silently produces garbage
(PROJECT_STATE section 6, learned in the name-entry milestone).

**Codec B** -- ``US 0x0800543C``.  Used for background tile sheets and
tilemaps.  Header is a ``u32be`` decompressed size; the stream then mixes
literal runs, zero/0xFF fills and self-referential back-references into the
output.

Usage
-----
    python ffta_gfx_codec.py --rom us --a7 0x083C1834 --png out.png
    python ffta_gfx_codec.py --rom jp --tiles 0x083A7004 --png out.png --cols 16
"""

from __future__ import annotations

import argparse
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ROMS = {
    "us": ROOT / "rom/original/FFTA_US.gba",
    "jp": ROOT / "rom/original/FFTA_JP.gba",
}
ROM_BASE = 0x08000000


def load(rom: str) -> bytes:
    p = ROMS[rom.lower()] if rom.lower() in ROMS else Path(rom)
    return p.read_bytes()


def off(addr: int) -> int:
    """Accept either a file offset or a CPU address."""
    return addr - ROM_BASE if addr >= ROM_BASE else addr


# ----------------------------------------------------------------- codec A ---

def codec_a_descriptor(rom: bytes, block: int):
    """(mode, shift, size, dictionary_offset, dictionary_bytes)."""
    mode, shift = rom[block], rom[block + 1]
    p = block + 2
    size = 0
    if mode <= 2:
        size = (rom[p] << 8) | rom[p + 1]
        p += 2
    mask = (0xFFFF >> (shift + 1)) & 0xFFFF
    return mode, shift, size, p, mask + 1


def decode_a(rom: bytes, src: int, block: int):
    """Decode one sub-image.  ``block`` is the container's codec block."""
    mode, shift, size, dic, _dlen = codec_a_descriptor(rom, block)
    mask = (0xFFFF >> (shift + 1)) & 0xFFFF
    s = src
    if mode == 0:
        total = size
    elif mode == 3:
        total = (rom[s] << 8) | rom[s + 1]
        s += 2
    else:
        raise ValueError(f"codec A: unsupported mode {mode}")
    out = bytearray()
    while len(out) != total:
        if len(out) > total:
            raise ValueError("codec A: output overflow")
        b = rom[s]
        if b & 0x80:                          # literal run
            n = (b & 0x7F) + 1
            s += 1
            out += rom[s:s + n]
            s += n
        else:                                 # dictionary back-reference
            n = ((b << 1) >> (8 - shift)) + 3
            o = ((b << 8) | rom[s + 1]) & mask
            s += 2
            out += rom[dic + o:dic + o + n]
    return bytes(out), s


def container(rom: bytes, base: int):
    """Parse an ``A7`` container header.  Returns (count, total, offsets)."""
    if rom[base:base + 2] != b"A7":
        raise ValueError(f"no A7 magic at 0x{base:06X}")
    count = struct.unpack_from(">H", rom, base + 2)[0]
    total = struct.unpack_from(">I", rom, base + 4)[0]
    offs = [struct.unpack_from(">I", rom, base + 8 + 4 * i)[0] for i in range(count)]
    return count, total, offs


def container_subs(rom: bytes, base: int):
    count, total, offs = container(rom, base)
    block = base + total
    return [decode_a(rom, base + o, block)[0] for o in offs]


def container_dictionary_high_water(rom: bytes, base: int) -> int:
    """Highest dictionary byte any sub-image of this container reads."""
    count, total, offs = container(rom, base)
    block = base + total
    mode, shift, size, _dic, _dlen = codec_a_descriptor(rom, block)
    mask = (0xFFFF >> (shift + 1)) & 0xFFFF
    high = 0
    for o in offs:
        s = base + o
        produced, total_out = 0, size
        if mode == 3:
            total_out = (rom[s] << 8) | rom[s + 1]
            s += 2
        while produced != total_out:
            b = rom[s]
            if b & 0x80:
                n = (b & 0x7F) + 1
                s += 1 + n
            else:
                n = ((b << 1) >> (8 - shift)) + 3
                high = max(high, (((b << 8) | rom[s + 1]) & mask) + n)
                s += 2
            produced += n
    return high


def find_containers(rom: bytes, max_count: int = 2048):
    """Every plausible ``A7`` container in the ROM.

    ``max_count`` was hard-coded to 64 until the world-map-labels milestone,
    and that cap was a real defect: the place-name container ``US 0x083AB42C``
    has **225** sub-images, the month sheet's neighbours have 165, and the
    largest has 522 -- so the inventory reported the world-map place labels as
    "not found as a divergent asset by either codec", and the milestone that
    read that report classified them ARCHITECTURAL.  They were a plain data
    port.  Never narrow this without re-reading that note.
    """
    out, pos = [], 0
    while True:
        pos = rom.find(b"A7", pos)
        if pos < 0:
            break
        o, pos = pos, pos + 1
        if o % 4:
            continue
        try:
            count, total, offs = container(rom, o)
        except Exception:
            continue
        if not (1 <= count <= max_count and 8 <= total <= 0x100000):
            continue
        if offs[0] != 8 + 4 * count or offs[-1] >= total:
            continue
        if any(offs[i] >= offs[i + 1] for i in range(count - 1)):
            continue
        out.append((o, count, total, offs))
    return out


# ----------------------------------------------------------------- codec B ---

def decode_b(rom: bytes, src: int):
    """Decode a background tile sheet / tilemap.  Returns (data, stream_end)."""
    total = int.from_bytes(rom[src:src + 4], "big")
    p = src + 4
    out = bytearray()
    while len(out) < total:
        b = rom[p]
        p += 1
        if b & 0x80:                                  # short back-reference
            n = ((b >> 3) & 0xF) + 3
            dist = ((b & 7) << 8) + rom[p] + 1
            p += 1
            for _ in range(n):
                out.append(out[len(out) - dist])
        elif b & 0x40:                                # literal run
            n = (b & 0x3F) + 1
            out += rom[p:p + n]
            p += n
        elif b & 0x20:                                # short zero fill
            out += b"\x00" * ((b & 0x1F) + 2)
        elif b & 0x10:                                # long back-reference
            b2 = rom[p]
            p += 1
            n = ((b & 0xF) | ((b2 >> 2) & 0x30)) + 4
            dist = (((b2 & 0x3F) << 8) | rom[p]) + 1
            p += 1
            for _ in range(n):
                out.append(out[len(out) - dist])
        elif b == 0:                                  # far back-reference
            n = rom[p] + 5
            dist = ((rom[p + 1] << 8) | rom[p + 2]) + 1
            p += 3
            for _ in range(n):
                out.append(out[len(out) - dist])
        elif b == 1:                                  # 0xFF fill
            out += b"\xff" * (rom[p] + 3)
            p += 1
        elif b == 2:                                  # long zero fill
            out += b"\x00" * (rom[p] + 3)
            p += 1
        else:
            raise ValueError(f"codec B: opcode {b} at 0x{p - 1:06X}")
    if len(out) != total:
        raise ValueError("codec B: output overrun")
    return bytes(out), p


def encode_b(data: bytes) -> bytes:
    """Re-encode a decoded codec-B payload, literal runs and zero fills only.

    ``decode_b`` is the authority for the opcode set; this writes the three
    opcodes that need no dictionary state, so the result is a pure function of
    ``data`` and round-trips through ``decode_b`` byte for byte:

    * ``0x40 | (n - 1)``  literal run, ``n`` in 1..64
    * ``0x20 | (n - 2)``  zero fill, ``n`` in 2..33
    * ``0x02``, ``n - 3`` zero fill, ``n`` in 3..258

    It never emits a back-reference, so it compresses far less than the
    retail encoder.  Use it only where the asset is relocated into free
    space and its stored length does not matter.
    """
    out = bytearray(len(data).to_bytes(4, "big"))
    i, n = 0, len(data)
    while i < n:
        if data[i] == 0:
            run = 0
            while run < 258 and i + run < n and data[i + run] == 0:
                run += 1
            if run >= 3:
                if run <= 33:
                    out.append(0x20 | (run - 2))
                else:
                    out += bytes((0x02, run - 3))
                i += run
                continue
        lit = 0
        while lit < 64 and i + lit < n:
            # stop a literal run just before a zero fill worth emitting
            if data[i + lit] == 0:
                z = 0
                while z < 3 and i + lit + z < n and data[i + lit + z] == 0:
                    z += 1
                if z >= 3:
                    break
            lit += 1
        if lit == 0:                                  # pragma: no cover
            lit = 1
        out.append(0x40 | (lit - 1))
        out += data[i:i + lit]
        i += lit
    return bytes(out)


def find_codec_b(rom: bytes, lo: int = 0, hi: int | None = None,
                 min_size: int = 0x40, max_size: int = 0x8000):
    """Every 4-aligned offset that decodes cleanly as codec B."""
    hi = len(rom) - 8 if hi is None else hi
    out = []
    for o in range(lo + (-lo % 4), hi, 4):
        if rom[o] or rom[o + 1]:
            continue
        size = (rom[o + 2] << 8) | rom[o + 3]
        if not (min_size <= size <= max_size):
            continue
        try:
            _d, end = decode_b(rom, o)
        except Exception:
            continue
        out.append((o, size, end))
    return out


# ---------------------------------------------------------------- renderer ---

def tile_grid(data: bytes, cols: int = 32):
    """4bpp GBA tiles laid out linearly, ``cols`` tiles per row."""
    ntile = len(data) // 32
    rows = (ntile + cols - 1) // cols
    px = [[0] * (cols * 8) for _ in range(rows * 8)]
    for t in range(ntile):
        tx, ty = (t % cols) * 8, (t // cols) * 8
        for y in range(8):
            for x in range(0, 8, 2):
                b = data[t * 32 + y * 4 + x // 2]
                px[ty + y][tx + x] = b & 0xF
                px[ty + y][tx + x + 1] = b >> 4
    return px


def write_png(px, path, scale: int = 1, palette=None):
    H = len(px)
    W = len(px[0]) if H else 0
    if palette is None:
        palette = [(40, 40, 60)] + [(min(255, 20 + 17 * i),) * 3 for i in range(1, 16)]
    if scale > 1:
        px = [[v for v in row for _ in range(scale)] for row in px for _ in range(scale)]
        W, H = W * scale, H * scale
    raw = b"".join(b"\x00" + bytes(row) for row in px)

    def chunk(tag, d):
        c = tag + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c))

    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 3, 0, 0, 0))
        + chunk(b"PLTE", b"".join(bytes(c) for c in palette))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b""))
    return W, H


def tiles_png(data: bytes, path, cols: int = 32, scale: int = 1):
    return write_png(tile_grid(data, cols), path, scale=scale)


# -------------------------------------------------------------------- diff ---

def tile_diff(a: bytes, b: bytes):
    """Index-for-index tile comparison of two decoded sheets.

    Returns a dict describing whether ``b`` is a drop-in replacement for
    ``a``: same tile count, same blank-tile indices, and the divergence
    confined to whole tiles (never a partial re-layout).
    """
    na, nb = len(a) // 32, len(b) // 32
    info = {"tiles_a": na, "tiles_b": nb, "same_tile_count": na == nb}
    if na != nb:
        return info
    diff = [t for t in range(na) if a[t * 32:t * 32 + 32] != b[t * 32:t * 32 + 32]]
    runs = []
    for t in diff:
        if runs and runs[-1][1] == t - 1:
            runs[-1][1] = t
        else:
            runs.append([t, t])
    blank = lambda d: [t for t in range(na) if not any(d[t * 32:t * 32 + 32])]
    info.update({
        "differing_tiles": len(diff),
        "identical_tiles": na - len(diff),
        "divergent_runs": [tuple(r) for r in runs],
        "blank_tiles_match": blank(a) == blank(b),
        "identical_prefix_tiles": runs[0][0] if runs else na,
    })
    return info


# -------------------------------------------------------------------- main ---

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", default="us")
    ap.add_argument("--a7", type=lambda s: int(s, 0))
    ap.add_argument("--tiles", type=lambda s: int(s, 0))
    ap.add_argument("--png")
    ap.add_argument("--cols", type=int, default=16)
    ap.add_argument("--scale", type=int, default=4)
    ap.add_argument("--list-a7", action="store_true")
    args = ap.parse_args()
    rom = load(args.rom)

    if args.list_a7:
        for o, c, t, _ in find_containers(rom):
            subs = container_subs(rom, o)
            print(f"{args.rom.upper()} 0x{ROM_BASE + o:08X} subs={c:2d} "
                  f"bytes/sub={len(subs[0])} total=0x{t:X}")
        return

    if args.a7 is not None:
        base = off(args.a7)
        subs = container_subs(rom, base)
        print(f"{args.rom.upper()} 0x{ROM_BASE + base:08X}: {len(subs)} sub-images, "
              f"{len(subs[0])} bytes each, dictionary high-water "
              f"{container_dictionary_high_water(rom, base)}")
        if args.png:
            tiles_png(b"".join(subs), args.png, cols=args.cols, scale=args.scale)
    if args.tiles is not None:
        base = off(args.tiles)
        data, end = decode_b(rom, base)
        print(f"{args.rom.upper()} 0x{ROM_BASE + base:08X}: {len(data)} bytes "
              f"({len(data) // 32} tiles), compressed {end - base} bytes")
        if args.png:
            tiles_png(data, args.png, cols=args.cols, scale=args.scale)


if __name__ == "__main__":
    raise SystemExit(main())
