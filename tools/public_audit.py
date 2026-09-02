#! python3
# coding: utf-8
"""Public-release audit for this repository.

One command answers the questions that have to be answered before anything is
published, and again on every change:

  forbidden-files    no ROM, save, savestate, patch, archive or image is tracked
  local-paths        no developer machine path, username, e-mail or token leaks
  manifest-schema    every build manifest parses and carries what the chain needs
  retail-text        no verbatim retail source string is stored in the manifests
  decoded-records    no manifest field holds a decoded retail text record
  rom-substring      no tracked file reproduces a long run of bytes from a ROM
  provenance         upstream-derived files are declared and attributed
  chain-integrity    every module the builder imports is present and parses
  commit-email       no commit or tag carries a personal e-mail address

The ROM-substring check needs ROMs and is skipped without them, so this whole
tool is safe to run in CI, where no ROM exists.  Run it with ROMs locally
before a release:

    python tools/public_audit.py
    python tools/public_audit.py --us <us.gba> --jp <jp.gba>

Exit status is 0 when every check passes, 1 otherwise.

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
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

REPO = Path(__file__).resolve().parent.parent
CHAIN = REPO / "src/localizer/chain"
DATA = CHAIN / "data"
TERMINAL = "ffta_jp_us_added_items"

# --------------------------------------------------------------- policy ---

FORBIDDEN_SUFFIX = {
    ".gba", ".gb", ".gbc", ".agb", ".nds", ".smc", ".sfc", ".bin", ".rom",
    ".bps", ".ips", ".ups", ".xdelta", ".vcdiff",
    ".sav", ".srm", ".sa1", ".fla", ".flash", ".state", ".sps", ".nsv",
    ".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".tgz",
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".mp4", ".avi", ".webp",
    ".exe", ".dll", ".so", ".dylib", ".pyc",
}
FORBIDDEN_RE = [
    re.compile(r"\.ss\d?$", re.I),
    re.compile(r"\.st\d$", re.I),
]
# Binaries that are legitimately part of the project would be listed here.
# There are none: the repository is text-only by design.
BINARY_ALLOWLIST: set[str] = set()

# A tracked text file bigger than this is worth a human look: bulk data is how
# raw dumps get in.
LARGE_FILE_BYTES = 2_000_000

# Lines carrying this marker are exempt from the local-path scan: they define
# the very patterns the scan looks for.
AUDIT_ALLOW_MARK = "audit:pattern"

LOCAL_PATH_PATTERNS = [  # audit:pattern
    ("windows user directory", re.compile(r"[A-Za-z]:\\+Users\\+[^\\\s\"']+")),  # audit:pattern
    ("unix home directory", re.compile(r"/(?:home|Users)/(?!<)[A-Za-z0-9._-]+/")),  # audit:pattern
    ("windows drive path", re.compile(r"\b[D-Zd-z]:\\{1,2}[A-Za-z0-9_]")),  # audit:pattern
    ("e-mail address", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),  # audit:pattern
    ("vscode webview url", re.compile(r"vscode-webview://", re.I)),  # audit:pattern
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),  # audit:pattern
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),  # audit:pattern
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),  # audit:pattern
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]{20,}")),  # audit:pattern
]
# Documentation legitimately shows what a path looks like.  These are the
# only forms allowed, and they name nothing real.
LOCAL_PATH_ALLOW = re.compile(
    r"(?:C:\\Users\\<|path/to/|<path|/home/<|example\.com|your\.gba"
    r"|C:\\path\\|\.venv\\Scripts)", re.I)

REQUIRED_DOCS = [
    "README.md", "LICENSE", "NOTICE.md", "CONTRIBUTING.md", "SECURITY.md",
    ".gitignore", "requirements.txt", "build.py",
    "docs/BUILD.md", "docs/TROUBLESHOOTING.md", "docs/FAQ.md",
    "docs/KNOWN_ISSUES.md", "docs/ARCHITECTURE.md",
    "docs/US_ADDED_CONTENT.md", "docs/PUBLIC_BETA_RELEASE_NOTES.md",
    "docs/PROVENANCE.md", "docs/LOCAL_ROM_QA.md", "docs/LABELS.md",
    "docs/PUBLISH_CHECKLIST.md",
    ".github/workflows/public-ci.yml",
    ".github/ISSUE_TEMPLATE/bug.yml",
    ".github/pull_request_template.md",
]

# Fields that must never hold verbatim retail text in a public manifest.
REDACTED_FIELDS = ("original_english", "retail_us_text_decoded")
# ...except where the builder writes the value back unchanged and it carries no
# natural language.  Kept explicit so a new exception cannot appear silently.
LITERAL_EXCEPTIONS = {("words:ico/8", "/"), ("words:ico/9", "-\u25b3\u25b3\u25cb\u25cb\u25ce")}

# ROM-substring audit.  32 bytes of exact ROM data is far past coincidence for
# a text file, and short enough to catch a pasted table.
ROM_MATCH_BYTES = 32

# Commit metadata is published with the repository and cannot be taken back:
# rewriting history leaves the old objects resolvable by SHA on the host.  The
# only address that may appear in it is the one GitHub hands out for exactly
# this purpose.
NOREPLY_SUFFIX = "@users.noreply.github.com"


class Audit:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.notes: list[str] = []
        self.skipped: list[str] = []

    def fail(self, check: str, detail: str) -> None:
        self.failures.append(f"{check}: {detail}")

    def note(self, text: str) -> None:
        self.notes.append(text)

    def skip(self, check: str, why: str) -> None:
        self.skipped.append(f"{check}: {why}")


def tracked_files() -> list[Path]:
    """Files git tracks, or every non-ignored file when git is unavailable."""
    try:
        out = subprocess.run(["git", "-C", str(REPO), "ls-files"],
                             capture_output=True, text=True, check=True).stdout
        files = [REPO / line for line in out.splitlines() if line.strip()]
        if files:
            return files
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    skip = {".git", ".build", "__pycache__", ".venv", "venv"}
    found = []
    for root, dirs, names in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in skip]
        for n in names:
            found.append(Path(root) / n)
    return found


def is_text(path: Path) -> bool:
    try:
        chunk = path.open("rb").read(4096)
    except OSError:
        return False
    return b"\0" not in chunk


# ------------------------------------------------------------- checks ---

def check_forbidden(a: Audit, files: list[Path]) -> None:
    for f in files:
        rel = f.relative_to(REPO).as_posix()
        if rel in BINARY_ALLOWLIST:
            continue
        if f.suffix.lower() in FORBIDDEN_SUFFIX or any(r.search(f.name) for r in FORBIDDEN_RE):
            a.fail("forbidden-files", f"{rel} has a forbidden extension")
            continue
        if f.is_file() and not is_text(f):
            a.fail("forbidden-files", f"{rel} is a binary file and is not allowlisted")
        elif f.is_file() and f.stat().st_size > LARGE_FILE_BYTES:
            a.fail("forbidden-files",
                   f"{rel} is {f.stat().st_size:,} bytes; bulk data needs review")
    a.note(f"forbidden-files: {len(files)} tracked files scanned")


def check_local_paths(a: Audit, files: list[Path]) -> None:
    hits = 0
    for f in files:
        if not f.is_file() or not is_text(f):
            continue
        rel = f.relative_to(REPO).as_posix()
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            # A line that defines one of these patterns necessarily contains
            # one.  Marked lines are the pattern table itself, not a leak.
            if AUDIT_ALLOW_MARK in line:
                continue
            for label, rx in LOCAL_PATH_PATTERNS:
                for m in rx.finditer(line):
                    if LOCAL_PATH_ALLOW.search(m.group(0)) or LOCAL_PATH_ALLOW.search(line):
                        continue
                    a.fail("local-paths", f"{rel}:{lineno} {label}: {m.group(0)[:60]!r}")
                    hits += 1
    a.note(f"local-paths: {len(files)} files scanned, {hits} finding(s)")


def check_manifests(a: Audit) -> None:
    if not DATA.is_dir():
        a.fail("manifest-schema", f"{DATA} missing")
        return
    files = sorted(DATA.glob("*.json"))
    if not files:
        a.fail("manifest-schema", "no manifests found")
        return
    rows_total = 0
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            a.fail("manifest-schema", f"{f.name}: {exc}")
            continue
        entries = doc.get("entries")
        if isinstance(entries, list):
            rows_total += len(entries)
            for i, row in enumerate(entries):
                if not isinstance(row, dict):
                    a.fail("manifest-schema", f"{f.name}[{i}] is not an object")
                    continue
                if "us_logical_path" not in row:
                    a.fail("manifest-schema", f"{f.name}[{i}] has no us_logical_path")
            # "count" is the manifest's whole declared scope, which for some
            # layers is wider than "entries": a layer that also records
            # non-production internal rows keeps them in their own list, and
            # count is the sum of the declared sub-counts.
            subtotals = [doc[k] for k in
                         ("translated_count", "no_change_required_count",
                          "non_production_internal_count")
                         if isinstance(doc.get(k), int)]
            written = doc.get("translated_count", 0) + doc.get("no_change_required_count", 0)
            if subtotals and isinstance(doc.get("count"), int) \
                    and doc["count"] != sum(subtotals):
                a.fail("manifest-schema",
                       f"{f.name}: count={doc['count']} but sub-counts sum to "
                       f"{sum(subtotals)}")
            if subtotals and written != len(entries):
                a.fail("manifest-schema",
                       f"{f.name}: {len(entries)} entries but "
                       f"{written} declared as written")
    a.note(f"manifest-schema: {len(files)} manifests, {rows_total} rows")


def check_retail_text(a: Audit) -> None:
    """No verbatim retail source string may be stored in a public manifest."""
    kept = 0
    for f in sorted(DATA.glob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        def walk(node, path="") -> None:
            nonlocal kept
            if isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")
                return
            if not isinstance(node, dict):
                return
            for k, v in node.items():
                if k in REDACTED_FIELDS and isinstance(v, str):
                    key = (node.get("us_logical_path", ""), v)
                    if key in LITERAL_EXCEPTIONS:
                        kept += 1
                        continue
                    a.fail("retail-text",
                           f"{f.name}{path}.{k} stores verbatim retail text "
                           f"({len(v)} chars); it must be a _sha256 digest")
                else:
                    walk(v, f"{path}.{k}")

        walk(doc)
    a.note(f"retail-text: {kept} allowlisted symbol-only literal(s) kept, "
           "all other retail source strings stored as digests")


def check_decoded_records(a: Audit) -> None:
    """Catch a decoded retail text record under ANY field name.

    check_retail_text() knows the two field names the redaction targets.  This
    check does not depend on the field name at all, so a manifest that arrives
    with a new field carrying decoded retail prose still fails.

    The signature is markup used as a *word separator*: in an encoded record
    the word space and line break (``{52}``, ``{4D}``) sit directly between two
    letters with no space, so ``The{52}palace{52}and{4D}the`` scores three
    letter-to-letter joins.  Prose written by the project never does that -- a
    translator note cites a glyph code beside Japanese (``{5117}ウォッチ``) or
    with an ordinary space (``[5117] ポイント``), which is why the check counts
    joins rather than markup, and requires several English words as well.
    """
    joins = re.compile(r"[A-Za-z][{\[][0-9A-F]{2,4}[}\]][A-Za-z]")
    words = re.compile(r"[A-Za-z]{2,}")
    found = 0
    for f in sorted(DATA.glob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        def walk(node, path=""):
            nonlocal found
            if isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")
            elif isinstance(node, dict):
                for k, v in node.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(node, str):
                if len(joins.findall(node)) >= 3 and len(words.findall(node)) >= 4:
                    a.fail("decoded-records",
                           f"{f.name}{path} looks like a decoded retail record "
                           f"({len(node)} chars): {node[:48]!r}")
                    found += 1

        walk(doc)
    a.note(f"decoded-records: {found} finding(s) across the build manifests")


def check_chain(a: Audit) -> None:
    if not CHAIN.is_dir():
        a.fail("chain-integrity", f"{CHAIN} missing")
        return
    local = {p.stem for p in CHAIN.glob("*.py")}
    if TERMINAL not in local:
        a.fail("chain-integrity", f"{TERMINAL}.py missing")
        return
    seen: set[str] = set()
    stack = [TERMINAL]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        path = CHAIN / f"{name}.py"
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            a.fail("chain-integrity", f"{name}.py does not parse: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [x.name.split(".")[0] for x in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module.split(".")[0]]
            else:
                continue
            for m in mods:
                if m in local and m not in seen:
                    stack.append(m)
                elif (m not in local and m not in sys.stdlib_module_names
                      and m not in {"PIL", "capstone", "cnocr", "hexdump", "numpy"}):
                    a.fail("chain-integrity",
                           f"{name}.py imports unknown module {m!r}")
    orphans = local - seen - {"ffta_us_source"}
    for o in sorted(orphans):
        a.fail("chain-integrity", f"{o}.py is present but nothing imports it")
    a.note(f"chain-integrity: {len(seen)} modules reachable from {TERMINAL}")


def check_provenance(a: Audit) -> None:
    sync = CHAIN / "SYNC_MANIFEST.json"
    if not sync.is_file():
        a.fail("provenance", "SYNC_MANIFEST.json missing; run tools/sync_from_canonical.py")
        return
    doc = json.loads(sync.read_text(encoding="utf-8"))
    upstream = set(doc.get("upstream_modules", []))
    if not upstream:
        a.fail("provenance", "no upstream modules declared")
        return
    notice = (REPO / "NOTICE.md").read_text(encoding="utf-8")
    for mod in sorted(upstream):
        if mod not in notice:
            a.fail("provenance", f"{mod} is upstream-derived but not named in NOTICE.md")
    present = {p.name for p in CHAIN.glob("*.py")}
    for mod in sorted(upstream):
        if mod not in present:
            a.fail("provenance", f"{mod} declared upstream but not present")
    if "BSoD123456/ffta_us_cn" not in notice:
        a.fail("provenance", "NOTICE.md does not name the upstream project")
    if "GNU General Public License" not in (REPO / "LICENSE").read_text(encoding="utf-8"):
        a.fail("provenance", "LICENSE is not the GPL text the upstream requires")
    a.note(f"provenance: {len(upstream)} upstream modules declared and attributed")


def check_publish_placeholders(a: Audit, files: list[Path]) -> None:
    """Surface the values that can only be filled in once the repo has a URL.

    Reported as a note, not a failure: before the repository exists there is
    nothing correct to put here, and CI must stay green until then.
    See docs/PUBLISH_CHECKLIST.md.
    """
    found = []
    for f in files:
        if not f.is_file() or not is_text(f):
            continue
        rel = f.relative_to(REPO).as_posix()
        if rel.startswith("tools/") or rel.startswith("docs/PUBLISH_CHECKLIST"):
            continue
        for lineno, line in enumerate(
                f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if "OWNER/REPO" in line:
                found.append(f"{rel}:{lineno}")
    if found:
        a.note("publish-placeholders: " + str(len(found))
               + " OWNER/REPO placeholder(s) to fill in at publish time: "
               + ", ".join(found[:6]))
    else:
        a.note("publish-placeholders: none outstanding")


def check_required_files(a: Audit) -> None:
    for rel in REQUIRED_DOCS:
        if not (REPO / rel).is_file():
            a.fail("required-files", f"{rel} missing")
    a.note(f"required-files: {len(REQUIRED_DOCS)} checked")


def check_commit_emails(a: Audit) -> None:
    """Every reachable commit and tag must carry a GitHub noreply address.

    An author or committer e-mail is published the moment the repository is,
    and a force-push does not take it back: the old objects stay resolvable by
    SHA on the host long after the branch has moved.  So the address has to be
    right the first time, which is what this gate is for.

    The offending address is never printed.  This check runs in public CI, and
    naming the leak would be the leak; the SHA is enough to fix it with
    ``git log --format='%H %ae %ce'`` on a private clone.
    """
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(REPO), *args],
                              capture_output=True, text=True, check=True).stdout

    try:
        if git("rev-parse", "--is-shallow-repository").strip() == "true":
            a.skip("commit-email",
                   "shallow clone; check out with fetch-depth 0 to audit history")
            return
        log = git("log", "--all", "--pretty=format:%H%x09%ae%x09%ce")
        tags = git("for-each-ref", "--format=%(refname:short)%09%(taggeremail)",
                   "refs/tags")
    except (subprocess.CalledProcessError, FileNotFoundError):
        a.skip("commit-email", "not a git checkout; nothing to audit")
        return

    commits = 0
    for line in log.splitlines():
        if not line.strip():
            continue
        sha, _, rest = line.partition("	")
        author, _, committer = rest.partition("	")
        commits += 1
        for role, email in (("author", author), ("committer", committer)):
            if not email.endswith(NOREPLY_SUFFIX):
                a.fail("commit-email",
                       f"{sha[:12]} {role} address is not a GitHub noreply "
                       f"address (value withheld: this log is public)")

    tagged = 0
    for line in tags.splitlines():
        name, _, email = line.partition("	")
        email = email.strip().strip("<>")
        if not email:  # a lightweight tag has no tagger
            continue
        tagged += 1
        if not email.endswith(NOREPLY_SUFFIX):
            a.fail("commit-email",
                   f"tag {name} tagger address is not a GitHub noreply "
                   f"address (value withheld: this log is public)")

    a.note(f"commit-email: {commits} commit(s) and {tagged} annotated tag(s) "
           f"audited")


def check_rom_substrings(a: Audit, files: list[Path], roms: list[Path]) -> None:
    """No tracked file may reproduce a long contiguous run of ROM bytes.

    This is not a legal test.  It is a guard against a raw table, a font sheet
    or a text dump being pasted into the repository by accident.  A rolling set
    of every ROM_MATCH_BYTES-long window in each ROM is compared against every
    window of each tracked file, in both UTF-8 and the raw bytes on disk.
    """
    if not roms:
        a.skip("rom-substring", "no ROM supplied (--us/--jp); run locally before a release")
        return
    windows: set[bytes] = set()
    for rom in roms:
        blob = rom.read_bytes()
        windows.update(blob[i:i + ROM_MATCH_BYTES]
                       for i in range(0, len(blob) - ROM_MATCH_BYTES))
    a.note(f"rom-substring: {len(windows):,} distinct {ROM_MATCH_BYTES}-byte "
           f"windows from {len(roms)} ROM(s)")
    scanned = 0
    for f in files:
        if not f.is_file():
            continue
        rel = f.relative_to(REPO).as_posix()
        if rel == "LICENSE":
            continue
        blob = f.read_bytes()
        scanned += 1
        hit = next((blob[i:i + ROM_MATCH_BYTES]
                    for i in range(0, max(0, len(blob) - ROM_MATCH_BYTES))
                    if blob[i:i + ROM_MATCH_BYTES] in windows), None)
        if hit is not None:
            a.fail("rom-substring",
                   f"{rel} contains {ROM_MATCH_BYTES} contiguous bytes present "
                   f"in a retail ROM: {hit.hex()}")
    a.note(f"rom-substring: {scanned} tracked files scanned")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--us", type=Path, help="US ROM, for the substring audit")
    ap.add_argument("--jp", type=Path, help="JP ROM, for the substring audit")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    a = Audit()
    files = [f for f in tracked_files() if f.is_file()]

    check_required_files(a)
    check_publish_placeholders(a, files)
    check_forbidden(a, files)
    check_local_paths(a, files)
    check_manifests(a)
    check_retail_text(a)
    check_decoded_records(a)
    check_chain(a)
    check_provenance(a)
    check_commit_emails(a)
    check_rom_substrings(a, files, [p for p in (args.us, args.jp) if p and p.is_file()])

    if args.json:
        print(json.dumps({"pass": not a.failures, "failures": a.failures,
                          "notes": a.notes, "skipped": a.skipped}, indent=1))
        return 1 if a.failures else 0

    print("FFTA US->JP localization -- public release audit\n")
    for n in a.notes:
        print(f"  ok    {n}")
    for s in a.skipped:
        print(f"  SKIP  {s}")
    if a.failures:
        print(f"\n  {len(a.failures)} FAILURE(S):\n")
        for f in a.failures:
            print(f"  FAIL  {f}")
        print("\nAUDIT FAILED")
        return 1
    print("\nAUDIT PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
