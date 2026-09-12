"""Formatting helpers shared by the GUI, the reports and the analysis modules.

Small on purpose. It exists because these two functions had drifted into
several copies: the byte formatter was written three times (widgets, the Qlik
capacity module and the report index), and a mismatch there is not cosmetic -
it makes the report library's "+104.3 GB vs 03 Sep" disagree with the figure
on the dashboard it came from, which is how you lose trust in a number. One
implementation, re-exported under the names the call sites already use.

No GUI and no third-party imports, so the analysis modules can use it too.

Note what is deliberately NOT here: the several `_days_since` helpers around
the codebase are not all the same function. Some return None on an
unparseable date and clamp negatives to zero, one returns "" and does not
clamp, and one takes the reference date as an argument and works on dates
rather than timestamps. Those sentinels feed different formatting paths, so
only the genuinely identical ones were merged into `days_since` below.
"""
from __future__ import annotations

import datetime


def human_bytes(n, sign=False):
    """Compact 1024-based byte count: 1.5 KB, 311.0 GB, 1.7 TB.

    `sign=True` prefixes a positive value with '+', for a delta. Negative
    values always carry their own minus sign.
    """
    lead = "+" if sign and (n or 0) > 0 else ""
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{lead}{int(n)} B" if unit == "B" else f"{lead}{n:,.1f} {unit}"
        n /= 1024
    return f"{lead}{n:,.1f} TB"


def days_since(iso):
    """Whole days since an ISO timestamp; None when absent or unparseable.

    Clamps to zero, so a clock skew that puts a timestamp slightly in the
    future reads as "today" rather than as a negative age.
    """
    if not iso:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    now = datetime.datetime.now(dt.tzinfo) if dt.tzinfo else datetime.datetime.now()
    return max(0, (now - dt).days)
