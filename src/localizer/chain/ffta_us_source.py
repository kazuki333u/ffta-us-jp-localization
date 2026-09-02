#! python3
# coding: utf-8
"""The US-retail source gate, without a copy of the US retail script.

Every editorial layer of this build asserts that the record it is about to
overwrite still reads *exactly* the US retail English the translation was
authored against.  That assertion is the reason a stale manifest, a shifted
alignment or the wrong ROM revision fails loudly instead of quietly writing
Japanese into the wrong slot.  It has to survive into the public build.

In the development repository the manifests hold that English verbatim.  This
repository does not redistribute the retail script, so the manifests hold a
SHA-256 digest of it instead (``original_english_sha256``) and the comparison
is made against a digest of the text read out of **the user's own US ROM**.
The gate is exactly as strong: SHA-256 equality over the same string.

Manifest rows may still carry ``original_english`` verbatim where the value is
a pure symbol run with no natural-language content and the builder has to write
that value back unchanged (``words:ico/8``, ``words:ico/9``).  Both forms are
accepted, so the same chain runs against either manifest flavour.

This file is part of the FFTA US->JP localization project.
Licensed under the GNU General Public License v3 (see LICENSE).
"""
from __future__ import annotations

import hashlib

FIELD = "original_english"
DIGEST_FIELD = "original_english_sha256"


def of(text: str) -> str:
    """The digest of one decoded record."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest(row) -> str:
    """The digest the manifest row asserts, whichever form it is stored in."""
    if DIGEST_FIELD in row:
        return row[DIGEST_FIELD]
    if FIELD in row:
        return of(row[FIELD])
    raise KeyError(f"{FIELD}/{DIGEST_FIELD} missing from manifest row")


def matches(row, actual: str) -> bool:
    """Does the record actually in the ROM match what the row was authored on?"""
    return digest(row) == of(actual)


def literal(row):
    """The verbatim source string, when the row keeps one; otherwise None.

    Only rows the builder writes back unchanged keep a literal.  Anything else
    must go through :func:`matches`.
    """
    return row.get(FIELD)
