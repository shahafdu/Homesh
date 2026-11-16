"""Which office formats are rendered to PDF.

In a module of its own because two programs read it: the server, to decide
whether to offer a preview, and the converter, to decide whether to accept a
document. One list, so the two can never disagree about what a preview is.
It imports nothing, which is what lets the converter use it without taking the
rest of the application along.
"""

from __future__ import annotations

# What LibreOffice can render faithfully. Deliberately explicit rather than
# "anything it will accept": a format that converts badly is worse than one that
# honestly says it has no preview.
CONVERTIBLE = frozenset({
    # Word processing
    "doc", "docx", "odt", "rtf", "dot", "dotx", "wpd",
    # Spreadsheets
    "xls", "xlsx", "ods", "csv", "xlsm", "xlt", "xltx",
    # Presentations
    "ppt", "pptx", "odp", "pps", "ppsx", "pot", "potx",
    # Other office-ish things it handles well
    "abw", "sxw", "fodt", "fods",
})


def convertible(ext: str | None) -> bool:
    return (ext or "").lower().lstrip(".") in CONVERTIBLE
