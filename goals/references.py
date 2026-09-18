# ABOUTME: The one predicate that decides whether an evidence reference may leave this package.
# ABOUTME: The JSON report and the HTML page both ask it, so they can never disagree about a ref.

from __future__ import annotations

import re

# Only these two classes are ever exported in the JSON or turned into an href in the page.
APPROVED_KINDS = ("https", "relative-path")

# A browser strips ASCII tab/newline/control characters before it parses a scheme, so a string
# carrying any of them is not the string a consumer would resolve. We do not clean it up and
# re-decide - a reference we cannot read literally is withheld.
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
NAMED_SCHEMES = {"javascript": "javascript-url", "data": "data-url",
                 "file": "file-url", "http": "insecure-url"}


def classify(ref):
    """Return `(kind, safe)`. `safe` is the value that may be exported AND linked, or None.

    Withholding is never silent: the kind names why, and both outputs show the same reason.
    Nothing dangerous is normalized into something linkable - a suspicious reference is
    withheld as it was found, never repaired.
    """
    if not isinstance(ref, str):
        return "not-a-string", None
    if CONTROL.search(ref):
        return "control-characters", None
    value = ref.strip(" ")
    if not value:
        return "empty", None
    # `//host/path` inherits the page's scheme, which for a file:// report is the local disk.
    if value.startswith("//"):
        return "protocol-relative-url", None
    scheme = SCHEME.match(value)
    if scheme:
        name = scheme.group(0)[:-1].lower()
        if name == "https" and value[scheme.end():].startswith("//"):
            return "https", value
        return NAMED_SCHEMES.get(name, "unapproved-url"), None
    if value.startswith(("/", "~")):
        return "absolute-path", None
    if ".." in re.split(r"[/\\]", value):
        return "path-traversal", None
    if "\\" in value:
        return "backslash-path", None
    return "relative-path", value


def link_target(ref):
    """The href a page may give this reference, or None. Same predicate, asked again."""
    kind, safe = classify(ref)
    return safe if kind in APPROVED_KINDS else None
