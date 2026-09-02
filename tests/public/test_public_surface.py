#! python3
# coding: utf-8
"""ROM-free checks. These are what CI runs; see docs/LOCAL_ROM_QA.md for the rest.

Every test here must pass on a machine that has never seen a ROM.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CHAIN = REPO / "src/localizer/chain"
DATA = CHAIN / "data"

sys.path.insert(0, str(REPO))
import build as builder  # noqa: E402


# --------------------------------------------------------------- manifests ---

def manifest_files():
    return sorted(DATA.glob("*.json"))


def test_manifests_exist():
    assert manifest_files(), "no build manifest found"


@pytest.mark.parametrize("path", manifest_files(), ids=lambda p: p.name)
def test_manifest_parses(path):
    json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", manifest_files(), ids=lambda p: p.name)
def test_manifest_declared_counts_agree(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    entries = doc.get("entries")
    if not isinstance(entries, list):
        pytest.skip("no entries list")
    written = doc.get("translated_count", 0) + doc.get("no_change_required_count", 0)
    if written:
        assert written == len(entries)


@pytest.mark.parametrize("path", manifest_files(), ids=lambda p: p.name)
def test_manifest_rows_are_addressable(path):
    """Every row must name the record it targets, or the builder cannot act."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    entries = doc.get("entries")
    if not isinstance(entries, list):
        pytest.skip("no entries list")
    for i, row in enumerate(entries):
        assert isinstance(row, dict), f"row {i} is not an object"
        assert row.get("us_logical_path"), f"row {i} has no us_logical_path"


@pytest.mark.parametrize("path", manifest_files(), ids=lambda p: p.name)
def test_translated_rows_have_japanese(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    entries = doc.get("entries")
    if not isinstance(entries, list):
        pytest.skip("no entries list")
    for row in entries:
        if row.get("status") == "TRANSLATED_REVIEWED":
            assert row.get("japanese"), f"{row['us_logical_path']} has no japanese text"


# ------------------------------------------------------------ retail text ---

ALLOWED_LITERALS = {("words:ico/8", "/"), ("words:ico/9", "-△△○○◎")}
REDACTED = ("original_english", "retail_us_text_decoded")


@pytest.mark.parametrize("path", manifest_files(), ids=lambda p: p.name)
def test_no_verbatim_retail_text(path):
    """The public manifests must carry digests, not the retail script."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    found = []

    def walk(node):
        if isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, dict):
            for k, v in node.items():
                if k in REDACTED and isinstance(v, str):
                    if (node.get("us_logical_path"), v) not in ALLOWED_LITERALS:
                        found.append(f"{node.get('us_logical_path')}.{k}")
                else:
                    walk(v)

    walk(doc)
    assert not found, f"verbatim retail text in {path.name}: {found[:5]}"


@pytest.mark.parametrize("path", manifest_files(), ids=lambda p: p.name)
def test_digests_are_well_formed(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    bad = []

    def walk(node):
        if isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, dict):
            for k, v in node.items():
                if k.endswith("_sha256") and isinstance(v, str):
                    # Redaction digests are lowercase; the ROM-lineage hashes
                    # the manifests already carried are uppercase.  Both are
                    # SHA-256, so accept either case and reject anything else.
                    if not re.fullmatch(r"(?:[0-9a-f]{64}|[0-9A-F]{64})", v):
                        bad.append(f"{node.get('us_logical_path')}.{k}={v[:16]}")
                else:
                    walk(v)

    walk(doc)
    assert not bad, f"malformed digests: {bad[:5]}"


DECODED_JOINS = re.compile(r"[A-Za-z][{\[][0-9A-F]{2,4}[}\]][A-Za-z]")
DECODED_WORDS = re.compile(r"[A-Za-z]{2,}")


def looks_like_a_decoded_record(text: str) -> bool:
    """Markup used as a word separator, plus English prose -- see public_audit."""
    return (len(DECODED_JOINS.findall(text)) >= 3
            and len(DECODED_WORDS.findall(text)) >= 4)


@pytest.mark.parametrize("path", manifest_files(), ids=lambda p: p.name)
def test_no_decoded_retail_record_under_any_field_name(path):
    """The field-name-independent guard: catches a new field carrying retail prose."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    found = []

    def walk(node, where=""):
        if isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{where}[{i}]")
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{where}.{k}")
        elif isinstance(node, str) and looks_like_a_decoded_record(node):
            found.append(f"{where}: {node[:40]!r}")

    walk(doc)
    assert not found, f"decoded retail record in {path.name}: {found[:3]}"


def test_the_decoded_record_detector_actually_fires():
    """A guard that cannot fail is not a guard. This is the shape it must catch."""
    encoded = ("The{52}palace{52}and{52}the{4D}resistance{52}are{52}holding{4D}"
               "talks...and{52}there{52}are{52}some{4D}objectors{52}out{52}there.")
    assert looks_like_a_decoded_record(encoded)


def test_the_decoded_record_detector_spares_translator_notes():
    """Project-authored notes cite glyph codes and must not be flagged."""
    for note in ("Mau, JudgeWatch -> {5117}ウォッチのマウ ({5117}ウォッチ = JudgeWatch)",
                 "the JP originals write [5117]ポイント with an ordinary space",
                 "Ezel; 協定 = the treaty (judge_ezel: 不干渉の協定); {4005} is the break"):
        assert not looks_like_a_decoded_record(note), note


def test_generated_audit_reports_are_not_shipped():
    """A report a chain module writes is an output, not a build input."""
    assert not (DATA / "us_added_placeholder_transfer.json").exists(), \
        "a generated audit report (which carries decoded retail records) is present"
    sync = json.loads((CHAIN / "SYNC_MANIFEST.json").read_text(encoding="utf-8"))
    assert "us_added_placeholder_transfer.json" in sync.get("skipped_outputs", [])


def test_every_shipped_manifest_is_actually_read_by_the_chain():
    """Nothing sits in data/ that the build never opens."""
    referenced = set()
    for path in CHAIN.glob("*.py"):
        referenced |= set(re.findall(r"data/([A-Za-z0-9_\-.]+\.json)",
                                     path.read_text(encoding="utf-8")))
    sync = json.loads((CHAIN / "SYNC_MANIFEST.json").read_text(encoding="utf-8"))
    skipped = set(sync.get("skipped_outputs", []))
    for f in DATA.glob("*.json"):
        assert f.name in referenced, f"{f.name} is shipped but never referenced"
        assert f.name not in skipped, f"{f.name} is shipped but marked as an output"


# ---------------------------------------------------------- source gate ---

def test_source_gate_accepts_matching_text():
    sys.path.insert(0, str(CHAIN))
    import ffta_us_source as gate
    text = "I am unused to[4D]these sorts of[4D]places...[40][42]"
    row = {"original_english_sha256": hashlib.sha256(text.encode()).hexdigest()}
    assert gate.matches(row, text)


def test_source_gate_rejects_drifted_text():
    sys.path.insert(0, str(CHAIN))
    import ffta_us_source as gate
    row = {"original_english_sha256": hashlib.sha256(b"expected").hexdigest()}
    assert not gate.matches(row, "something else")


def test_source_gate_accepts_a_kept_literal():
    """Rows that keep a verbatim symbol run still gate correctly."""
    sys.path.insert(0, str(CHAIN))
    import ffta_us_source as gate
    row = {"original_english": "/"}
    assert gate.matches(row, "/")
    assert not gate.matches(row, "x")


def test_source_gate_refuses_a_row_with_no_assertion():
    sys.path.insert(0, str(CHAIN))
    import ffta_us_source as gate
    with pytest.raises(KeyError):
        gate.matches({}, "anything")


# ----------------------------------------------------------- build chain ---

def test_every_chain_module_parses():
    for path in sorted(CHAIN.glob("*.py")):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_chain_is_closed_under_import():
    """Nothing the terminal builder imports may be missing from this repo."""
    local = {p.stem for p in CHAIN.glob("*.py")}
    seen, stack = set(), ["ffta_jp_item_job_strip"]
    external = set()
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        tree = ast.parse((CHAIN / f"{name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module.split(".")[0]]
            else:
                continue
            for m in mods:
                if m in local:
                    stack.append(m)
                elif m not in sys.stdlib_module_names:
                    external.add(m)
    assert "ffta_jp_item_job_strip" in seen
    assert external <= {"PIL", "capstone", "cnocr", "hexdump", "numpy"}, \
        f"unexpected third-party dependency: {external}"


def test_sync_manifest_matches_the_tree():
    doc = json.loads((CHAIN / "SYNC_MANIFEST.json").read_text(encoding="utf-8"))
    present = {p.name for p in CHAIN.glob("*.py")}
    for entry in doc["chain_modules"]:
        assert entry["module"] in present, f"{entry['module']} declared but absent"
    for mod in doc["upstream_modules"]:
        assert mod in present


# --------------------------------------------------------------- builder ---

def test_pinned_hashes_are_well_formed():
    for value in (builder.PRISTINE_US, builder.PRISTINE_JP, builder.EXPECTED_OUTPUT):
        assert re.fullmatch(r"[0-9A-F]{64}", value)
    assert re.fullmatch(r"[0-9A-F]{8}", builder.EXPECTED_OUTPUT_CRC32)


def test_the_three_pinned_roms_are_distinct():
    assert len({builder.PRISTINE_US, builder.PRISTINE_JP, builder.EXPECTED_OUTPUT}) == 3


def test_every_known_rom_is_named():
    for h, label in builder.KNOWN.items():
        assert re.fullmatch(r"[0-9A-F]{64}", h)
        assert label.strip()


def test_output_is_not_mistaken_for_an_input():
    """A localized ROM must never be accepted as --us or --jp."""
    assert builder.EXPECTED_OUTPUT not in (builder.PRISTINE_US, builder.PRISTINE_JP)
    assert "already-localized" in builder.KNOWN[builder.EXPECTED_OUTPUT]


def test_wrong_rom_is_rejected(tmp_path):
    rom = tmp_path / "wrong.gba"
    rom.write_bytes(b"\0" * builder.ROM_SIZE)
    with pytest.raises(SystemExit) as exc:
        builder.verify_input(rom, builder.PRISTINE_US, "us")
    assert "not the supported pristine ROM" in str(exc.value)


def test_wrong_size_is_rejected_before_hashing(tmp_path):
    rom = tmp_path / "trimmed.gba"
    rom.write_bytes(b"\0" * 1024)
    with pytest.raises(SystemExit) as exc:
        builder.verify_input(rom, builder.PRISTINE_US, "us")
    assert "16,777,216 bytes" in str(exc.value)


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(SystemExit) as exc:
        builder.verify_input(tmp_path / "nope.gba", builder.PRISTINE_US, "us")
    assert "not found" in str(exc.value)


def test_swapped_roms_are_named_as_such(tmp_path, monkeypatch):
    rom = tmp_path / "jp.gba"
    rom.write_bytes(b"\0" * builder.ROM_SIZE)
    monkeypatch.setattr(builder, "sha256_file", lambda p: builder.PRISTINE_JP)
    with pytest.raises(SystemExit) as exc:
        builder.verify_input(rom, builder.PRISTINE_US, "us")
    assert "swapped" in str(exc.value)


def test_localized_rom_as_input_is_named_as_such(tmp_path, monkeypatch):
    rom = tmp_path / "patched.gba"
    rom.write_bytes(b"\0" * builder.ROM_SIZE)
    monkeypatch.setattr(builder, "sha256_file", lambda p: builder.EXPECTED_OUTPUT)
    with pytest.raises(SystemExit) as exc:
        builder.verify_input(rom, builder.PRISTINE_US, "us")
    assert "already-localized" in str(exc.value)


def test_superseded_build_as_input_is_named_as_such(tmp_path, monkeypatch):
    superseded = "9098F025FDDD00119E0FD833199F5E9BBD1F66DCBEAD865D60749C3C4E652532"
    rom = tmp_path / "rc12.gba"
    rom.write_bytes(b"\0" * builder.ROM_SIZE)
    monkeypatch.setattr(builder, "sha256_file", lambda p: superseded)
    with pytest.raises(SystemExit) as exc:
        builder.verify_input(rom, builder.PRISTINE_JP, "jp")
    assert "already-localized" in str(exc.value)


def test_correct_rom_is_accepted(tmp_path, monkeypatch):
    rom = tmp_path / "us.gba"
    rom.write_bytes(b"\0" * builder.ROM_SIZE)
    monkeypatch.setattr(builder, "sha256_file", lambda p: builder.PRISTINE_US)
    assert builder.verify_input(rom, builder.PRISTINE_US, "us") == builder.PRISTINE_US


def test_builder_cli_runs_without_a_rom():
    out = subprocess.run([sys.executable, str(REPO / "build.py"), "--print-hashes"],
                         capture_output=True, text=True, cwd=str(REPO))
    assert out.returncode == 0
    assert builder.EXPECTED_OUTPUT in out.stdout


def test_builder_mentions_no_rom_source():
    """The tool must never point anyone at a place to get a ROM."""
    text = (REPO / "build.py").read_text(encoding="utf-8").lower()
    for word in ("http://", "https://", "download", "torrent", "romsite"):
        if word in ("http://", "https://"):
            assert word not in text, f"build.py contains a URL ({word})"
        else:
            assert f"{word} a rom" not in text


# ------------------------------------------------------------------ docs ---

@pytest.mark.parametrize("rel", [
    "README.md", "LICENSE", "NOTICE.md", "CONTRIBUTING.md", "SECURITY.md",
    "docs/BUILD.md", "docs/TROUBLESHOOTING.md", "docs/FAQ.md",
    "docs/KNOWN_ISSUES.md", "docs/ARCHITECTURE.md",
    "docs/US_ADDED_CONTENT.md", "docs/PUBLIC_BETA_RELEASE_NOTES.md",
    "docs/PROVENANCE.md", "docs/LOCAL_ROM_QA.md", "docs/LABELS.md",
    "docs/PUBLISH_CHECKLIST.md",
    ".github/ISSUE_TEMPLATE/bug.yml", ".github/pull_request_template.md",
    ".github/workflows/public-ci.yml",
])
def test_required_file_present_and_not_empty(rel):
    path = REPO / rel
    assert path.is_file(), f"{rel} missing"
    assert path.stat().st_size > 0, f"{rel} is empty"


def test_readme_states_the_beta_status_and_no_affiliation():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert "Public Beta" in text
    assert "非公式" in text          # 非公式
    assert "スクウェア" in text  # スクウェア
    assert "ROMを一切含みません" in text


def test_readme_makes_no_overclaim():
    """Claims the evidence does not support must not be *made*.

    The README is allowed -- and expected -- to name these phrases in order to
    disclaim them, so a bare substring search would flag the disclaimer itself.
    A line carrying one of them must also carry a negation.
    """
    negations = ("ません", "しない", "ではありません", "not ", "no ")
    for path in (REPO / "README.md", REPO / "docs/PUBLIC_BETA_RELEASE_NOTES.md"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for phrase in ("完全翻訳", "100% bug free", "fully certified",
                           "公式日本語版", "Official Japanese Version"):
                if phrase in line and not any(n in line for n in negations):
                    pytest.fail(f"{path.name}:{lineno} claims {phrase!r}: {line.strip()}")


def test_readme_pins_the_same_hashes_as_the_builder():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert builder.PRISTINE_US in text
    assert builder.PRISTINE_JP in text
    assert builder.EXPECTED_OUTPUT in text


def test_notice_names_the_upstream_and_its_license():
    text = (REPO / "NOTICE.md").read_text(encoding="utf-8")
    assert "BSoD123456/ffta_us_cn" in text
    assert "GNU General Public License v3" in text


def test_license_is_the_gpl():
    text = (REPO / "LICENSE").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in text
    assert "Version 3, 29 June 2007" in text


def test_no_documentation_points_at_a_rom_source():
    for path in list((REPO / "docs").glob("*.md")) + [REPO / "README.md",
                                                      REPO / "CONTRIBUTING.md",
                                                      REPO / "SECURITY.md"]:
        text = path.read_text(encoding="utf-8").lower()
        for word in ("romhustler", "emuparadise", "coolrom", "torrent", "nointro.zip"):
            assert word not in text, f"{path.name} points at a ROM source"
