"""Normalization helpers shared by the parsers."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

_TRAILING_ZULU = re.compile(r"Z$")
_NON_NUMERIC = re.compile(r"[^\d,.\-]")


def parse_date(value: object) -> date | None:
    """Parse the date formats these sources actually emit.

    Handles ISO 8601 with or without a time and timezone, plus the bare
    YYYY-MM-DD and YYYY/MM/DD forms found in the legacy SEAO XML.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text:
        return None

    # fromisoformat on 3.11 handles most of this, but not a trailing Z.
    candidate = _TRAILING_ZULU.sub("+00:00", text)
    try:
        return datetime.fromisoformat(candidate).date()
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%Y%m%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[: len(fmt) + 4], fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(value: object) -> float | None:
    """Parse a monetary amount, tolerating French formatting.

    Quebec sources use both '1 234,56' (space group, comma decimal) and the
    anglophone '1,234.56'. Guessing wrong shifts a value by a factor of 100 or
    1000, so the rules are explicit: when both separators appear the rightmost
    is the decimal mark; a repeated separator is grouping; and a lone separator
    followed by exactly three digits is grouping too, since these sources never
    carry three decimal places.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    text = text.replace(" ", "").replace(" ", "")
    text = _NON_NUMERIC.sub("", text)
    if not text or text in {"-", ".", ","}:
        return None

    n_comma = text.count(",")
    n_dot = text.count(".")
    last_comma = text.rfind(",")
    last_dot = text.rfind(".")

    if n_comma and n_dot:
        # Both present: the rightmost is the decimal mark.
        if last_comma > last_dot:
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif n_comma > 1 or n_dot > 1:
        # A single separator type, repeated, can only be digit grouping.
        text = text.replace(",", "").replace(".", "")
    elif n_comma == 1 or n_dot == 1:
        position = max(last_comma, last_dot)
        trailing = len(text) - position - 1
        if trailing == 3:
            # Genuinely ambiguous: '1,234' is 1234 in English, 1.234 in French.
            # Three trailing digits means grouping — monetary values in these
            # sources carry two decimals or none, never three.
            text = text.replace(",", "").replace(".", "")
        else:
            text = text.replace(",", ".")

    try:
        return float(text)
    except ValueError:
        return None


def normalize_tag(name: str) -> str:
    """Reduce an XML tag or column name to a comparable key.

    Strips any namespace, accents, and non-alphanumerics, then lowercases:
    'NuméroSEAO', '{ns}numero-seao' and 'NUMERO_SEAO' all become 'numeroseao'.
    Used so the field maps do not have to enumerate spelling variants.
    """
    if "}" in name:
        name = name.rsplit("}", 1)[1]
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", stripped.lower())


def clean_text(value: object) -> str | None:
    """Collapse whitespace and return None for empties."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def parse_int(value: object) -> int | None:
    amount = parse_amount(value)
    if amount is None:
        return None
    try:
        return int(amount)
    except (ValueError, OverflowError):
        return None
