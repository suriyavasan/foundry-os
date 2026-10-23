"""License allowlist for skills imported from GitHub."""

from __future__ import annotations

import re

ALLOWED_LICENSES = (
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "CC0-1.0",
    "CC-BY-4.0",
    "Unlicense",
    "MPL-2.0",
)
_BY_KEY = {re.sub(r"[^a-z0-9]", "", name.lower()): name for name in ALLOWED_LICENSES}
_ALIASES = {
    "apache2": "Apache-2.0",
    "apache20": "Apache-2.0",
    "apachelicense20": "Apache-2.0",
    "bsd2clause": "BSD-2-Clause",
    "bsd3clause": "BSD-3-Clause",
    "mozillapubliclicense20": "MPL-2.0",
    "mpl2": "MPL-2.0",
    "ccby40": "CC-BY-4.0",
    "cc0": "CC0-1.0",
    "unlicensed": "Unlicense",
}


def normalize_license(value: str | None) -> str | None:
    """Return the allowlisted SPDX id for ``value`` (case/punctuation tolerant), else None."""
    if not value:
        return None
    key = re.sub(r"[^a-z0-9]", "", value.strip().lower())
    return _BY_KEY.get(key) or _ALIASES.get(key)


# Phrases from the standard license texts, for LICENSE files that carry no SPDX id.
_TEXT_MARKERS = (
    ("Apache-2.0", ("apache license", "version 2.0")),
    ("MPL-2.0", ("mozilla public license", "2.0")),
    ("Unlicense", ("this is free and unencumbered software",)),
    ("CC0-1.0", ("cc0 1.0 universal",)),
    ("CC-BY-4.0", ("attribution 4.0 international",)),
    ("MIT", ("permission is hereby granted, free of charge",)),
    ("ISC", ("permission to use, copy, modify, and/or distribute this software",)),
    (
        "BSD-3-Clause",
        ("redistribution and use in source and binary forms", "neither the name"),
    ),
    ("BSD-2-Clause", ("redistribution and use in source and binary forms",)),
)


def detect_license_text(text: str) -> str | None:
    """Best-effort SPDX detection for a LICENSE file. None when unrecognized (-> refuse)."""
    lowered = " ".join(text.lower().split())
    for spdx, needles in _TEXT_MARKERS:
        if all(n in lowered for n in needles):
            return spdx
    return None
