# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Windows release (a folder, zipped by CI).
#
#   python -m PyInstaller --noconfirm --clean packaging/windows/ffta_us_jp.spec
#
# The chain is shipped as DATA, not as frozen modules: its layers resolve the
# build root from their own file location, so build.py stages a copy and runs
# it from there.  Only the chain's outside imports (the standard library and
# Pillow) are frozen, found here by reading the chain's import statements.
# Nothing in the bundle comes from a ROM.
import ast
from pathlib import Path

REPO = Path(SPECPATH).resolve().parents[1]
CHAIN = REPO / "src" / "localizer" / "chain"

# Imported only by debug or self-check code the build never runs
# (see requirements.txt); not installed, and not wanted in the bundle.
UNUSED = {"capstone", "cnocr", "hexdump", "numpy"}


def chain_imports() -> list[str]:
    own = {p.stem for p in CHAIN.glob("*.py")}
    found = set()
    for path in CHAIN.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module)
                if node.module == "PIL":
                    found.update(f"PIL.{a.name}" for a in node.names)
    return sorted(m for m in found
                  if m.split(".")[0] not in own | UNUSED and m != "__future__")


a = Analysis(
    [str(REPO / "gui.py")],
    pathex=[str(REPO)],
    # Same set build.py's stage() copies: everything but caches.
    datas=[(str(p), str(Path("src/localizer/chain") / p.parent.relative_to(CHAIN)))
           for p in sorted(CHAIN.rglob("*"))
           if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"],
    hiddenimports=chain_imports(),
    excludes=sorted(UNUSED),
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FFTA_JP_Builder",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="FFTA_JP_Builder", upx=False)
