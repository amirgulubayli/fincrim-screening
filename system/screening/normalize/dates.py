"""Partial dates from the many formats providers use.

A date can be a full date, a year+month, or a year alone. "Unknown stays
unknown": a missing part never contradicts anything.

Implemented: ISO, year-only, "Month D, YYYY", "D Month YYYY".
TODO (seed issue "Date parser"): "DD/MM/YYYY" vs "MM/DD/YYYY" ambiguity,
"1996; 1997" lists (split upstream by contracts.as_list), ages-at-date in
profile notes ("aged 45 in March 2019" -> 1973 or 1974).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}


@dataclass(frozen=True, order=True)
class PartialDate:
    year: int
    month: int | None = None
    day: int | None = None

    def __str__(self) -> str:
        if self.month and self.day:
            return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"
        if self.month:
            return f"{self.year:04d}-{self.month:02d}"
        return f"{self.year:04d}"


_ISO = re.compile(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?")
_YEAR = re.compile(r"^(\d{4})$")
_MDY = re.compile(r"^([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})$")
_DMY = re.compile(r"^(\d{1,2})\s+([A-Za-z]+)\.?,?\s+(\d{4})$")
_MY = re.compile(r"^([A-Za-z]+)\.?\s+(\d{4})$")


def parse_date(raw: str | None) -> PartialDate | None:
    if not raw:
        return None
    text = str(raw).strip()
    if m := _ISO.match(text):
        return PartialDate(int(m[1]), int(m[2]), int(m[3]) if m[3] else None)
    if m := _YEAR.match(text):
        return PartialDate(int(m[1]))
    if (m := _MDY.match(text)) and m[1].lower() in MONTHS:
        return PartialDate(int(m[3]), MONTHS[m[1].lower()], int(m[2]))
    if (m := _DMY.match(text)) and m[2].lower() in MONTHS:
        return PartialDate(int(m[3]), MONTHS[m[2].lower()], int(m[1]))
    if (m := _MY.match(text)) and m[1].lower() in MONTHS:
        return PartialDate(int(m[2]), MONTHS[m[1].lower()])
    return None  # TODO seed issue: slash formats and other provider wordings
