#! python3
# coding: utf-8
"""Derive this public repository's build chain from the canonical development tree.

This repository is **not** a mirror of the development repository.  It is a
mechanically derived subset:

* only the modules the production build chain actually imports are copied;
* every US-retail English string in the editorial manifests is replaced by a
  SHA-256 digest of that string, so the public repository carries no copy of
  the retail script while the build keeps the exact same alignment gate;
* development-only evidence, exploratory tooling, savestates, ROMs, patches
  and generated artifacts are not copied at all.

Run it against a checkout of the canonical implementation repository:

    python tools/sync_from_canonical.py --canonical <path-to-canonical-repo>

It rewrites ``src/localizer/chain`` in place and prints a manifest of what it
did.  ``--check`` compares instead of writing, which is how CI-style drift
detection would be done locally.

This file is part of the FFTA US->JP localization project.
Licensed under the GNU General Public License v3 (see LICENSE).
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CHAIN = REPO / "src/localizer/chain"
DATA = CHAIN / "data"

TERMINAL = "ffta_jp_us_added_items"

# Upstream (third-party, GPL-3.0) modules.  Recorded so NOTICE.md and the
# provenance audit can name them without guessing.
UPSTREAM_FILES = {
    "ffta_charset.py", "ffta_finder.py", "ffta_font.py", "ffta_font_generator.py",
    "ffta_modifier.py", "ffta_ocr.py", "ffta_ocr_ambi.py", "ffta_parser.py",
    "ffta_sect.py", "tips_and_test.py", "trans_checker.py", "trans_formatter.py",
    "charset_cn.json", "charset_us.json", "trans_txt.json", "trans_fix_txt.json",
    "credits.txt", "readme.md", "LICENSE", ".gitignore",
    "font/BoutiqueBitmap9x9_1.7.ttf",
}

# Shared data files the chain loads by bare name from its own directory.
SHARED_DATA = ["charset_us.json", "charset_cn.json"]

# Files under data/ that a chain module *names* but only ever writes: they are
# reports produced by running that module as a script, not build inputs.  They
# carry decoded retail records, so they must not be copied.
GENERATED_OUTPUTS = {
    # written by ffta_placeholder_transfer_audit.main(); the build imports that
    # module for its placeholder predicates and never reads this report.
    "us_added_placeholder_transfer.json",
}

# Fields holding verbatim retail text.  Replaced by "<field>_sha256".
REDACT_TEXT_FIELDS = ("original_english", "retail_us_text_decoded")

# Rows whose English is a pure symbol run with no natural-language content are
# kept verbatim: the builder writes them back unchanged, and a digest cannot
# reconstruct the value it has to write.
KEEP_LITERAL_STATUS = "NO_CHANGE_REQUIRED"


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def chain_modules(canonical: Path) -> list[str]:
    """Every local module transitively imported by the terminal builder."""
    local = {p.stem: p for p in canonical.glob("*.py")}
    seen: set[str] = set()
    stack = [TERMINAL]
    while stack:
        name = stack.pop()
        if name in seen or name not in local:
            continue
        seen.add(name)
        tree = ast.parse(local[name].read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module.split(".")[0]]
            else:
                continue
            for m in mods:
                if m in local and m not in seen:
                    stack.append(m)
    return sorted(seen)


# --- source-gate rewrite -----------------------------------------------------
#
# The chain asserts that the record it is about to overwrite still reads the
# exact US retail English the manifest was authored against.  That assertion is
# what makes a stale or mis-aligned manifest fail loudly instead of writing
# Japanese into the wrong slot, so it must survive into the public build.  The
# rewrite keeps the assertion and changes only what it compares: a digest of
# the text read out of the user's own US ROM, against a digest in the manifest.

GATE_IMPORT = "import ffta_us_source as _us_source\n"

GATE_REWRITES = [
    # (module, old fragment, new fragment)
    ("ffta_jp_us_added_items.py",
     '        if row["original_english"] != fxt.visible(original, decode):',
     '        if not _us_source.matches(row, fxt.visible(original, decode)):'),
    # The US text this layer gates on is also the text it hands to the
    # placeholder proof.  Read it out of the user's ROM once and use it for
    # both, instead of carrying a second copy in the manifest.
    ("ffta_jp_us_added_missions.py",
     '        original = original_tokens(us, us_raw, row)\n'
     '        if row["original_english"] != fxt.visible(original, decode):',
     '        original = original_tokens(us, us_raw, row)\n'
     '        us_text = fxt.visible(original, decode)\n'
     '        if not _us_source.matches(row, us_text):'),
    ("ffta_jp_us_added_missions.py",
     '            jp, jp_names, row, row["original_english"])',
     '            jp, jp_names, row, us_text)'),
    ("ffta_jp_us_only_system_battle.py",
     '        if row["original_english"] != visible(\n'
     '                us_tokens(us, row["family"], row["us_index"]), decode):',
     '        if not _us_source.matches(row, visible(\n'
     '                us_tokens(us, row["family"], row["us_index"]), decode)):'),
    ("ffta_jp_us_only_fx_text.py",
     'if row["original_english"] != visible(tokens_of(page[index]), decode):',
     'if not _us_source.matches(row, visible(tokens_of(page[index]), decode)):'),
    ("ffta_jp_us_only_s_text.py",
     'if row["original_english"] != visible(tokens_of(page[line]), decode):',
     'if not _us_source.matches(row, visible(tokens_of(page[line]), decode)):'),
    ("ffta_jp_us_only_s_text_final.py",
     'if row["original_english"] != sje.visible(tokens_of(root[top][line]), decode):',
     'if not _us_source.matches(row, sje.visible(tokens_of(root[top][line]), decode)):'),
    ("ffta_jp_us_only_s_text_judge_ezel.py",
     'if row["original_english"] != visible(tokens_of(page[line]), decode):',
     'if not _us_source.matches(row, visible(tokens_of(page[line]), decode)):'),
    ("ffta_jp_us_only_s_text_top61.py",
     'if row["original_english"] != prev.visible(tokens_of(leaf[line]), decode):',
     'if not _us_source.matches(row, prev.visible(tokens_of(leaf[line]), decode)):'),
    ("ffta_jp_us_only_words_name.py",
     'if r["original_english"] != us_pool_text[index]:',
     'if not _us_source.matches(r, us_pool_text[index]):'),
]

# Comments that describe the canonical checkout's directory layout.  They are
# accurate there and wrong here, so they are corrected rather than carried over.
LAYOUT_REWRITES = [
    ("# This checkout lives at <project>/tools/external/ffta_us_cn.  Bind earlier\n"
     "# source layers to this actual project root without modifying those layers.",
     "# The chain is staged three directories below the build root, so parents[3]\n"
     "# is the directory holding rom/original and rom/build.  build.py lays that\n"
     "# out; see docs/ARCHITECTURE.md."),
]

# Sites that only echo the field into a local evidence JSON.
ECHO_PATTERNS = [
    (re.compile(r'"original_english": (row|r)\["original_english"\]'),
     r'"original_english_sha256": _us_source.digest(\1)'),
    (re.compile(r'\{[\'"]original_english[\'"]: (row|r)\["original_english"\]'),
     r'{"original_english_sha256": _us_source.digest(\1)'),
]


def rewrite_source(name: str, text: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    for old, new in LAYOUT_REWRITES:
        if old in text:
            text = text.replace(old, new)
            notes.append("layout comment corrected")
    for mod, old, new in GATE_REWRITES:
        if mod == name:
            if old not in text:
                raise SystemExit(f"SYNC_GATE_PATTERN_MISSING {name}: {old}")
            text = text.replace(old, new)
            notes.append("source-gate -> digest")

    # words_name reports the mismatch with the two values; the plaintext is gone.
    text = text.replace(
        'f"{r[\'original_english\']!r} != {us_pool_text[index]!r}"',
        'f"{_us_source.digest(r)} != {_us_source.of(us_pool_text[index])}"')

    # system_battle writes the retail value back for NO_CHANGE_REQUIRED rows,
    # whose English is kept verbatim in the public manifest (symbols only).
    for rx, rep in ECHO_PATTERNS:
        text, n = rx.subn(rep, text)
        if n:
            notes.append(f"evidence echo x{n} -> digest")

    if "_us_source." in text and GATE_IMPORT not in text:
        # Insert after the module's import block.  Use the AST end line, not a
        # startswith() scan: several modules end with a parenthesised
        # ``from x import (a, b, c)`` spanning several lines, and inserting
        # into the middle of one is a syntax error.
        tree = ast.parse(text)
        ends = [n.end_lineno for n in tree.body
                if isinstance(n, (ast.Import, ast.ImportFrom))]
        if not ends:
            raise SystemExit(f"SYNC_NO_IMPORT_BLOCK {name}")
        lines = text.splitlines(keepends=True)
        lines.insert(max(ends), GATE_IMPORT)
        text = "".join(lines)
        ast.parse(text)  # never emit a module that will not parse
        notes.append("import ffta_us_source")
    return text, notes


def redact_manifest(obj):
    """Replace verbatim retail text with its digest, recursively."""
    n = [0]

    def walk(o):
        if isinstance(o, list):
            return [walk(v) for v in o]
        if not isinstance(o, dict):
            return o
        out = {}
        keep = o.get("status") == KEEP_LITERAL_STATUS
        for k, v in o.items():
            if k in REDACT_TEXT_FIELDS and isinstance(v, str) and not keep:
                out[k + "_sha256"] = sha256_text(v)
                n[0] += 1
            else:
                out[k] = walk(v)
        return out

    return walk(obj), n[0]


def redact_csv_header(path: Path) -> None:
    """Drop an original_english column from a generated CSV."""
    import csv
    rows = list(csv.reader(path.open(encoding="utf-8", newline="")))
    if not rows:
        return
    head = rows[0]
    if "original_english" not in head:
        return
    i = head.index("original_english")
    out = [r[:i] + r[i + 1:] for r in rows]
    with path.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--canonical", required=True, type=Path,
                    help="path to the canonical implementation repository")
    ap.add_argument("--check", action="store_true",
                    help="compare instead of writing")
    args = ap.parse_args()

    canonical = args.canonical.resolve()
    if not (canonical / f"{TERMINAL}.py").is_file():
        raise SystemExit(f"SYNC_NOT_CANONICAL {canonical}")

    mods = chain_modules(canonical)
    manifest = {"chain_modules": [], "shared_data": [], "manifests": [],
                "upstream_modules": [], "redacted_fields": 0}

    staged: dict[Path, bytes] = {}

    for m in mods:
        src = canonical / f"{m}.py"
        text = src.read_text(encoding="utf-8")
        text, notes = rewrite_source(f"{m}.py", text)
        staged[CHAIN / f"{m}.py"] = text.encode("utf-8")
        entry = {"module": f"{m}.py",
                 "origin": "upstream" if f"{m}.py" in UPSTREAM_FILES else "project",
                 "rewrites": notes}
        manifest["chain_modules"].append(entry)
        if entry["origin"] == "upstream":
            manifest["upstream_modules"].append(f"{m}.py")

    for name in SHARED_DATA:
        src = canonical / name
        if src.is_file():
            # Normalize to LF like every other staged file.  .gitattributes
            # checks the tree out as LF, so copying CRLF bytes verbatim would
            # make --check report drift on every fresh clone.  JSON content is
            # unaffected.
            text = src.read_text(encoding="utf-8")
            staged[CHAIN / name] = text.encode("utf-8")
            manifest["shared_data"].append(name)

    # Editorial manifests the chain reads.  Only the ones actually referenced.
    wanted = set()
    for m in mods:
        text = (canonical / f"{m}.py").read_text(encoding="utf-8")
        wanted |= set(re.findall(r'data/([A-Za-z0-9_\-.]+\.json)', text))
    for name in sorted(wanted):
        if name in GENERATED_OUTPUTS:
            manifest.setdefault("skipped_outputs", []).append(name)
            continue
        src = canonical / "data" / name
        if not src.is_file():
            raise SystemExit(f"SYNC_MANIFEST_MISSING {name}")
        obj = json.loads(src.read_text(encoding="utf-8"))
        obj, n = redact_manifest(obj)
        obj = {"_public_note": (
            "Public build manifest. Verbatim retail source strings are stored as "
            "SHA-256 digests (fields ending in _sha256); the builder recomputes "
            "them from the user's own ROM. Japanese text in this file is written "
            "by this project."), **obj}
        blob = json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=False)
        staged[DATA / name] = (blob + "\n").encode("utf-8")
        manifest["manifests"].append({"file": name, "digested_fields": n})
        manifest["redacted_fields"] += n

    if args.check:
        bad = []
        for path, blob in staged.items():
            if not path.is_file() or path.read_bytes() != blob:
                bad.append(str(path.relative_to(REPO)))
        print(json.dumps({"drift": bad}, indent=1))
        return 1 if bad else 0

    CHAIN.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    for path, blob in staged.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)

    # write_bytes, not write_text: write_text uses the platform newline, and
    # every other file this tool emits is LF.
    (CHAIN / "SYNC_MANIFEST.json").write_bytes(
        (json.dumps(manifest, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    print(json.dumps({k: (len(v) if isinstance(v, list) else v)
                      for k, v in manifest.items()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
