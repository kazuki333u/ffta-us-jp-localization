#! python3
# coding: utf-8
"""FFTA US->JP localization -- public builder.

Builds a Japanese-localized Final Fantasy Tactics Advance (US) ROM from two
ROMs you already own:

    python build.py --us FFTA_US.gba --jp FFTA_JP.gba --output FFTA_US_JP.gba

No ROM is included with this project, and this tool never downloads one.
Both inputs are verified by exact SHA-256 before anything is built, and the
finished ROM is verified against the pinned release hash before it is written
to the output path.

This file is part of the FFTA US->JP localization project.
Licensed under the GNU General Public License v3 (see LICENSE).
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import time
import zlib
from pathlib import Path

# The primary audience runs Windows in a Japanese locale, where the console
# codec is cp932 and cannot encode every character this tool prints.  Never
# let a build fail on its own progress output.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

REPO = Path(__file__).resolve().parent
CHAIN = REPO / "src/localizer/chain"
TERMINAL = "ffta_jp_item_job_strip.py"

RELEASE = "Public Beta 2 (RC23-based)"

# --- the only ROMs this build supports --------------------------------------

PRISTINE_US = "43FC8204C6DCEEE58828AEBC7AF0C72EB807E99F35AD641C8BB0A4FA8B6EDC19"
PRISTINE_JP = "B13DD536808EF5D0FD4494386A9499F6FEB8310835D3F867CD17CC340D82BF9A"

# The localized ROM this project produces from those two.
EXPECTED_OUTPUT = "6C78CDEE7914056CEBF6A39354A6A82C8DA132C2D6A0D88928B4B6A2CDB717E6"
EXPECTED_OUTPUT_CRC32 = "8262D569"

ROM_SIZE = 16 * 1024 * 1024

# Hashes worth recognising so the error message can say what the file actually
# is instead of only that it is wrong.  Output ROMs of this project's earlier
# release candidates, so "you passed a already-patched ROM" is named as such.
KNOWN = {
    PRISTINE_US: "pristine US ROM (correct --us input)",
    PRISTINE_JP: "pristine JP ROM (correct --jp input)",
    EXPECTED_OUTPUT: "an already-localized ROM built by this project (Public Beta 2 / RC23)",
    "6A9A686F1D281AEF0B5F81A337EE6339C8B862EC7732A23329B08F9EF8969D3D":
        "an already-localized ROM from an earlier internal build (RC22)",
    "F3B0F990B416C0AEFBEB44EE468A16B2D7FA0FDDDA19F9E7CF5E99F91EEEE49B":
        "an already-localized ROM from an earlier internal build (RC21)",
    "9F5EBA6C4408CAA419DE1DC96AE20B6B8A43785230C1B14C21BA14D9C5D4195E":
        "an already-localized ROM from the previous public beta (Public Beta 1 / RC17)",
    "9098F025FDDD00119E0FD833199F5E9BBD1F66DCBEAD865D60749C3C4E652532":
        "an already-localized ROM from an earlier internal build (RC12)",
    "66FB6EFEDB7A7832C2E6DFBF0802190C217A840423B82E0E9AEDD2C95EA5EB1F":
        "an already-localized ROM from an earlier internal build (RC11)",
    "DAE7BF3FD524B36B3DA87493E9F3062D7A08E523752F54D72159A366C56ED002":
        "an already-localized ROM from a withdrawn internal build (RC10, defective)",
    "BF73CF3824CCF3BFEEA419D2C7836F902F78ADB8CC2C20BEDA4762D1BB4BB0F4":
        "an already-localized ROM from a withdrawn internal build (RC9, defective)",
    "3F3F05C2E6FA7519EB2033EB4B4DEF30A8DF2D51E46829D7668B9395C926ADD5":
        "an already-localized ROM from an earlier internal build (RC8)",
    "F654E8640F2E200C8C3ECD1819BDBD1D266C75E2C9CBB9FB2D440DC8F867FBF0":
        "an already-localized ROM from an earlier internal build (RC7)",
    "B80F206732635D3F3913CEBAA8F2E72498887D8AC65FD6B304E499C216E54281":
        "an already-localized ROM from an earlier internal build (RC6)",
    "F696951432446BECB013E5A630E0D2F7418293846C226D5D3716BD20E562072E":
        "an already-localized ROM from an earlier internal build (RC5)",
    "61C746714157CD5CBD7CFE58852CC225213EEA08BC8AABFEE9877497763038D3":
        "an already-localized ROM from an earlier internal build (RC4)",
    "BE03434D7FA2558836E409EE2225D3DC5BE3E77B9132D49202FCC282A405D27B":
        "an already-localized ROM from an earlier internal build (RC3)",
    "A7E97A64497BAACCAED10E17D5ED0E22A49ABC094A6419E7822917D833E33A34":
        "an already-localized ROM from an earlier internal build (RC2)",
    "7CB8D5DD2B7A5E9A8BFCCEC0C150BBD0FCA1A75F578707926BD73BCE2C95CC4C":
        "an already-localized ROM from a withdrawn internal build (RC1, defective)",
}


class BuildError(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"\nERROR: {message}\n")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest().upper()


def crc32_file(path: Path) -> str:
    crc = 0
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            crc = zlib.crc32(block, crc)
    return format(crc & 0xFFFFFFFF, "08X")


def describe(got: str) -> str:
    known = KNOWN.get(got)
    return f" That file is {known}." if known else ""


def verify_input(path: Path, want: str, role: str) -> str:
    """Exact-hash gate on one user-supplied ROM."""
    if not path.is_file():
        raise BuildError(f"--{role} file not found: {path}")
    size = path.stat().st_size
    if size != ROM_SIZE:
        raise BuildError(
            f"--{role} is {size:,} bytes; a Final Fantasy Tactics Advance ROM is "
            f"{ROM_SIZE:,} bytes.\n"
            "       If your file is a .zip/.7z, extract the .gba first.\n"
            "       Trimmed, over-dumped, headered or patched dumps are not supported.")
    got = sha256_file(path)
    if got == want:
        return got
    other = PRISTINE_JP if role == "us" else PRISTINE_US
    swapped = " It looks like --us and --jp are swapped." if got == other else ""
    raise BuildError(
        f"--{role} is not the supported pristine ROM.\n"
        f"       expected SHA-256 {want}\n"
        f"       got      SHA-256 {got}"
        f"{swapped}{describe(got)}\n"
        "       This project supports exactly one revision of each ROM; see docs/BUILD.md.\n"
        "       No ROM is distributed with this project and none will be downloaded.")


def stage(work: Path) -> Path:
    """Lay the chain out at the depth its modules resolve the build root from."""
    chain = work / "stage/localizer/chain"
    if chain.exists():
        shutil.rmtree(chain)
    chain.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(CHAIN, chain, ignore=shutil.ignore_patterns(
        "__pycache__", "build", "*.pyc"))
    (work / "rom/original").mkdir(parents=True, exist_ok=True)
    (work / "rom/build").mkdir(parents=True, exist_ok=True)
    return chain


def run_chain(chain: Path, log: Path) -> None:
    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.run(
            [sys.executable, TERMINAL], cwd=str(chain),
            stdout=fh, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        tail = "\n".join(log.read_text(encoding="utf-8", errors="replace")
                         .splitlines()[-25:])
        raise BuildError(
            "the build chain failed.\n"
            f"       full log: {log}\n"
            "       When reporting this, paste the TAIL of that log only.  Do not\n"
            "       attach the scratch directory: it holds copies of your ROMs.\n"
            f"\n{tail}")


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="build.py",
        description="Build the Japanese-localized FFTA (US) ROM from your own ROMs.",
        epilog="No ROM is included with this project and none is ever downloaded.")
    ap.add_argument("--us", type=Path, help="your pristine US ROM (.gba)")
    ap.add_argument("--jp", type=Path, help="your pristine JP ROM (.gba)")
    ap.add_argument("--output", "-o", type=Path, help="where to write the localized ROM")
    ap.add_argument("--work-dir", type=Path, default=REPO / ".build",
                    help="scratch directory for intermediates (default: ./.build)")
    ap.add_argument("--keep-work", action="store_true",
                    help="keep the scratch directory after a successful build")
    ap.add_argument("--identify", type=Path, metavar="ROM",
                    help="print what a ROM file is and exit")
    ap.add_argument("--print-hashes", action="store_true",
                    help="print the supported ROM hashes and exit")
    args = ap.parse_args()

    if args.print_hashes:
        print(f"{RELEASE}\n")
        print(f"  required input  US  SHA-256 {PRISTINE_US}")
        print(f"  required input  JP  SHA-256 {PRISTINE_JP}")
        print(f"  produced output     SHA-256 {EXPECTED_OUTPUT}")
        print(f"  produced output     CRC32   {EXPECTED_OUTPUT_CRC32}")
        return 0

    if args.identify:
        path = args.identify
        if not path.is_file():
            raise BuildError(f"file not found: {path}")
        got = sha256_file(path)
        print(f"{path}\n  size    {path.stat().st_size:,} bytes")
        print(f"  SHA-256 {got}\n  CRC32   {crc32_file(path)}")
        print(f"  -> {KNOWN.get(got, 'not a ROM revision this project knows about')}")
        return 0

    missing = [f"--{n}" for n, v in
               (("us", args.us), ("jp", args.jp), ("output", args.output)) if v is None]
    if missing:
        ap.error("missing required argument(s): " + ", ".join(missing))

    if not CHAIN.is_dir() or not (CHAIN / TERMINAL).is_file():
        raise BuildError(f"the build chain is missing from {CHAIN}")

    try:
        import PIL  # noqa: F401
    except ImportError:
        raise BuildError(
            "Pillow is required.  Install the dependencies first:\n"
            "           python -m pip install -r requirements.txt")

    print(f"FFTA US->JP localization -- {RELEASE}\n")

    print("[1/5] verifying your ROMs")
    verify_input(args.us, PRISTINE_US, "us")
    print(f"      US  OK  {PRISTINE_US}")
    verify_input(args.jp, PRISTINE_JP, "jp")
    print(f"      JP  OK  {PRISTINE_JP}")

    work = args.work_dir.resolve()
    print(f"\n[2/5] staging the build in {work}")
    chain = stage(work)
    shutil.copyfile(args.us, work / "rom/original/FFTA_US.gba")
    shutil.copyfile(args.jp, work / "rom/original/FFTA_JP.gba")

    print("\n[3/5] building (this rebuilds every layer from your ROMs; "
          "expect a few minutes)")
    started = time.time()
    log = work / "build.log"
    run_chain(chain, log)
    print(f"      done in {time.time() - started:.0f}s   log: {log}")

    produced = work / "rom/build/ffta_us_jp_item_job_strip.gba"
    if not produced.is_file():
        raise BuildError(f"the build chain produced no ROM at {produced}")

    print("\n[4/5] verifying the build")
    got = sha256_file(produced)
    if got != EXPECTED_OUTPUT:
        raise BuildError(
            "the finished ROM does not match this release.\n"
            f"       expected {EXPECTED_OUTPUT}\n"
            f"       got      {got}\n"
            "       Nothing was written to the output path.  Please open an issue\n"
            "       and include this hash and the tail of the build log.\n"
            f"       Do not attach {work}: it holds copies of your ROMs.")
    print(f"      SHA-256 {got}")
    print(f"      CRC32   {crc32_file(produced)}")

    print("\n[5/5] writing the output")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(produced, args.output)
    print(f"      {args.output}")

    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
        print(f"\n      removed the scratch directory ({work})")
    else:
        print(f"\n      kept the scratch directory ({work}) -- it contains ROM data,\n"
              "      never commit it or attach it to an issue.")

    print("\nDone.  This is a public beta: please read docs/KNOWN_ISSUES.md,\n"
          "and do not attach ROM files to bug reports.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
