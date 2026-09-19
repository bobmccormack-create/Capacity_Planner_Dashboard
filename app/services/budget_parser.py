"""
Pull quoted hours out of a Project Tools workbook.

Only the HOURS BUDGETS block is read. The HOURS CONSUMED block underneath
it is deliberately ignored: actuals now come from QuickBooks Time, which is
both more accurate and always current. That block is hand-maintained - 28
Sky's is kept up to date, 901 Seabury's is entirely empty - and replacing it
is the point of the exercise.

Layout varies between projects, so columns are read from each sheet's own
header row and never by position:

  28 Sky    CONTRACT | ROUGH | TRIM | FINAL | PROG. | SHADE | LIGHT | PM | ENG | T&M | TRAVEL | ADMIN | SERVICE | TOTAL
  Seabury   CONTRACT | DSA | ROUGH | TRIM | FINAL | PROG. | SHADE | LIGHT | PM | ENG | T&M | TOTAL
  Red Wolf  CONTRACT | DSA | ROUGH | TRIM | FINAL | PROG. | SHADE | LIGHT | PM | ENG | T&M | TRAVEL | ADMIN | SERVICE | TOTAL

Every file also carries a second, stale HOURS BUDGETS block further down
using PRE where the live one says ROUGH - an older template nobody deleted.
Only the first block is read.

Rows are kept individually, not just summed. The Seabury sheet holds Phase 1
contracts and a separate PHASE 2 row in one block, and those are two
different jobcodes (5740 and 6159) - collapsing them would compare each
jobcode against the other's budget as well as its own.
"""
from __future__ import annotations

import re

# Column headings that carry hours. Anything else in the header row (TOTAL,
# blanks, the TRAVEL BUDGETS block to the right) is ignored.
PHASE_COLUMNS = {
    "DSA", "ROUGH", "PRE", "TRIM", "FINAL", "PROG.", "PROG", "SHADE",
    "LIGHT", "PM", "ENG", "T&M", "TRAVEL", "ADMIN", "SERVICE",
}

# How a sheet column maps onto a QuickBooks Time phase bucket. Only these
# three are directly comparable to logged install hours.
BUDGET_TO_INSTALL = {"ROUGH": "rough", "PRE": "rough",
                     "TRIM": "trim", "FINAL": "final"}

# A row label that marks a distinct piece of work rather than a change order
# against the main contract.
_PHASE_ROW = re.compile(r"\bphase\s*([0-9]+|[IVX]+)\b", re.IGNORECASE)
_SHADE_ROW = re.compile(r"\bshade", re.IGNORECASE)

_ROMAN = {"I": "1", "II": "2", "III": "3", "IV": "4"}


def _cells(line: str) -> list:
    return [
        c.strip().replace("\\-", "-").replace("\\&", "&").replace(",", "")
        for c in line.strip().strip("|").split("|")
    ]


def _num(s: str) -> float:
    s = (s or "").strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def row_phase(label: str) -> str | None:
    """'PHASE 2' -> '2'. Used to split a multi-phase sheet by jobcode."""
    m = _PHASE_ROW.search(label or "")
    if not m:
        return None
    val = m.group(1).upper()
    return _ROMAN.get(val, val)


def row_is_shades(label: str) -> bool:
    """Shades contracts are budgeted separately and billed to their own jobcode."""
    return bool(_SHADE_ROW.search(label or ""))


def parse_budget(text: str) -> dict | None:
    """
    Returns:
      {
        "columns": [...],                       # as the sheet has them
        "rows": [ {"contract": str, "phase": str|None, "shades": bool,
                   "hours": {COLUMN: float}} ],
        "total": {COLUMN: float},               # every row summed
        "install": {"rough": f, "trim": f, "final": f},
      }
    or None when no readable budget block is found.
    """
    lines = text.split("\n")
    try:
        at = next(i for i, l in enumerate(lines) if "HOURS BUDGETS" in l)
    except StopIteration:
        return None

    header = _cells(lines[at + 1])
    if not header or header[0].strip().upper() != "CONTRACT":
        return None

    cols = [(i, h.strip().upper()) for i, h in enumerate(header)
            if h.strip().upper() in PHASE_COLUMNS]
    if not cols:
        return None

    rows = []
    for line in lines[at + 2:]:
        if "HOURS CONSUMED" in line or "HOURS BUDGETS" in line:
            break
        c = _cells(line)
        if not c:
            break
        label = (c[0] or "").strip()
        # The sheet's own TOTAL row would double everything.
        if label.upper() == "TOTAL":
            continue
        hours = {}
        for i, col in cols:
            if i < len(c):
                v = _num(c[i])
                if v:
                    hours[col] = hours.get(col, 0.0) + v
        if not label and not hours:
            continue
        if not hours:
            continue
        rows.append({
            "contract": label or "(unlabelled)",
            "phase": row_phase(label),
            "shades": row_is_shades(label),
            "hours": {k: round(v, 2) for k, v in hours.items()},
        })

    if not rows:
        return None

    total: dict = {}
    for r in rows:
        for k, v in r["hours"].items():
            total[k] = total.get(k, 0.0) + v

    install = {"rough": 0.0, "trim": 0.0, "final": 0.0}
    for col, slot in BUDGET_TO_INSTALL.items():
        if col in total:
            install[slot] += total[col]

    return {
        "columns": [h for _, h in cols],
        "rows": rows,
        "total": {k: round(v, 2) for k, v in total.items()},
        "install": {k: round(v, 2) for k, v in install.items()},
    }


def budget_for(parsed: dict, phase: str | None = None,
               shades: bool | None = None) -> dict:
    """
    Budget for one slice of a sheet.

    phase="2" gives just the PHASE 2 rows; phase=None gives everything that
    isn't marked with a phase (i.e. the main contract and its change orders).
    shades=True/False filters the shades rows in or out.
    """
    if not parsed:
        return {"rough": 0.0, "trim": 0.0, "final": 0.0, "total": {}}

    picked = []
    for r in parsed["rows"]:
        if phase is not None and r["phase"] != phase:
            continue
        if phase is None and r["phase"] is not None:
            continue
        if shades is not None and r["shades"] != shades:
            continue
        picked.append(r)

    total: dict = {}
    for r in picked:
        for k, v in r["hours"].items():
            total[k] = total.get(k, 0.0) + v

    install = {"rough": 0.0, "trim": 0.0, "final": 0.0}
    for col, slot in BUDGET_TO_INSTALL.items():
        if col in total:
            install[slot] += total[col]

    return {**{k: round(v, 2) for k, v in install.items()},
            "total": {k: round(v, 2) for k, v in total.items()},
            "contracts": [r["contract"] for r in picked]}
