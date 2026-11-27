"""What ffmpeg and ffprobe are allowed to read: the one thing they were given.

ffmpeg decides what a file is from its bytes, not its name, and some formats are
instructions rather than media: a playlist or a manifest that says "now fetch
these other addresses". Anybody who can put a file in a granted folder could so
have the server make requests from inside the house, without seeing the answers.

Two lists close that, and every run gets both:

* **Formats.** Only containers that hold their own picture and sound. This is
  the one that stops a manifest, because following it needs nothing but the
  same http the real source arrives over. Measured: a DASH manifest named
  ``.mp4`` was followed with the protocols limited and the formats not.
* **Protocols.** A library file is read over our own streaming endpoint, which
  needs ``http,tcp`` and nothing else -- in particular not ``file``. The reads
  that have a path instead of an address (a thumbnail, a length, a finished
  copy) get ``file`` and nothing else, so they cannot reach any address at all.

The command lines are built here too, so that a new caller cannot start either
program without saying which of the two it is reading. tests/test_ffinput.py
refuses either program's name anywhere else in the server.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from .signing import mint

# Where the server reaches itself. A source never supplies an address of its own:
# a Drive file and a local one are both read through the streaming endpoint.
SOURCE_BASE = "http://127.0.0.1:8080"

# ffmpeg's own names for the demuxers this library needs: what the catalog calls
# video or audio, what is converted for watching or for sending, and the usual
# alternatives ffmpeg picks for those extensions. A format is added here only if
# it cannot name another file or address. So never: hls, dash, concat, imf, sdp,
# rtsp, image2 (a file name pattern), or the "tee", "data" and "subfile" kinds.
DEMUXERS = (
    # Video. "mov" is also MP4 and 3GP, "matroska" is also WebM, "asf" is also
    # WMV and WMA, "mpeg" is the program stream of .mpg, .vob and .mod, and
    # "mpegvideo" and "m4v" are pictures with no container around them.
    "mov", "matroska", "avi", "asf", "flv", "live_flv", "mpeg", "mpegts",
    "mpegvideo", "m4v", "dv", "mxf", "rm", "ogg",
    # Sound.
    "mp3", "aac", "flac", "wav", "w64", "aiff", "ac3", "eac3", "dts", "dtshd",
    "amr", "amrnb", "amrwb", "ape", "au", "caf", "mpc", "mpc8", "tta", "wv",
)

_ADDRESS = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class Input:
    """One thing to read. Made by `stream_input` or `file_input`."""

    protocols: str
    url: str

    def args(self) -> list[str]:
        # Input options apply to the input that follows them, so these have to
        # stay in front of -i.
        return [
            "-protocol_whitelist", self.protocols,
            "-format_whitelist", ",".join(DEMUXERS),
            "-i", self.url,
        ]


def stream_input(item_id: UUID, user_id: UUID, ttl: int) -> Input:
    """A library file, read as `user_id` through the signed streaming endpoint."""
    token = mint(item_id, user_id, "stream", ttl=ttl)
    return Input("http,tcp", f"{SOURCE_BASE}/api/stream/{item_id}?t={token}")


def file_input(path: Path) -> Input:
    """A file the server already has by path: in a granted folder, or its own.

    Spelled as a ``file:`` address so that a name with a colon in it can never be
    taken for a protocol.
    """
    return Input("file", f"file:{Path(path).absolute()}")


def ffmpeg_argv(source: Input, output: Sequence[str],
                before: Sequence[str] = ()) -> list[str]:
    """`before` is what applies to the input (a seek, probe sizes) or to the run."""
    return ["ffmpeg", *before, *source.args(), *output]


def ffprobe_argv(source: Input, options: Sequence[str]) -> list[str]:
    return ["ffprobe", *options, *source.args()]


def redact(said: str) -> str:
    """What a child complained about, without the address it was reading.

    The address carries a token that unlocks the file, and both programs quote
    it in their errors. Apply before shortening: a cut through the middle of an
    address leaves the token and removes what marks it as one.
    """
    return _ADDRESS.sub("<source>", said)
