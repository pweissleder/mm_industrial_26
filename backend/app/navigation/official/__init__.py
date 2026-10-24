"""Official kit 1.1.0 code, vendored unchanged (see the header of each module).

`building_map` is the route model the judges grade with; `score` is the official benchmark scorer
(used by the deterministic harness and the Train/Dev example comparison).
"""

import hashlib
from pathlib import Path

KIT_VERSION = "1.1.0"
KIT_COMMIT = "dc03c447dbe5b2e5f82c7db0d72249931817d86d"
# Git blob ids of the original files at KIT_COMMIT (CRLF-normalised content).
PINNED_BLOBS = {
    "building_map.py": (
        "starter_agent/building_map.py",
        "8029290de2daaef82b161fdaa701a8f4679f06a4",
    ),
    "score.py": ("benchmark/score.py", "11723a362747e93419b4f29631f2369385329094"),
}
HEADER_END = b"# --- end of vendoring header ---\n"


def git_blob_id(content: bytes, normalise_crlf: bool = True) -> str:
    """`git hash-object` of `content`; CRLF is normalised so a Windows checkout still matches."""
    if normalise_crlf:
        content = content.replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def vendored_body(filename: str) -> bytes:
    """The original file content of a vendored module (everything after the header)."""
    raw = (Path(__file__).parent / filename).read_bytes().replace(b"\r\n", b"\n")
    _, marker, body = raw.partition(HEADER_END)
    return body if marker else raw
