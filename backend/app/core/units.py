"""Units, scales and currencies.

Two documents can state the same fact as "81,420 million" and "8,142 Cr" and a naive
comparison reports a 10x contradiction. Resolving that is not a nicety, it is most of
the difference between a system that finds real disagreements and one that produces
noise, so unit handling gets a first-class module rather than a regex at the call site.

Every quantity is reduced to (magnitude in a class base unit, unit class, currency).
Comparison only ever happens inside one class, and currency is treated as part of the
identity rather than something to convert: exchange rates have their own vintage
problem and silently applying one would manufacture facts the documents never stated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

CURRENCY = "currency"
RATIO = "ratio"
RATIO_CHANGE = "ratio_change"
COUNT = "count"
MASS = "mass"
DISTANCE = "distance"
AREA = "area"
DURATION = "duration"
ENERGY = "energy"
DIMENSIONLESS = "dimensionless"


@dataclass(frozen=True)
class Unit:
    """A resolved unit: how to reach the class base, and what class that is."""

    canonical: str
    unit_class: str
    factor: float
    currency: str | None = None

    @property
    def comparison_key(self) -> str:
        return f"{self.unit_class}:{self.currency}" if self.currency else self.unit_class


@dataclass(frozen=True)
class Quantity:
    value: float
    unit: Unit
    base_value: float

    @property
    def unit_class(self) -> str:
        return self.unit.unit_class

    @property
    def currency(self) -> str | None:
        return self.unit.currency


# Scale words. Indian scales are included because every document in a South Asian
# corpus mixes them with the international ones, frequently on the same page.
SCALES: dict[str, float] = {
    "unit": 1.0,
    "units": 1.0,
    "one": 1.0,
    "hundred": 1e2,
    "hundreds": 1e2,
    "thousand": 1e3,
    "thousands": 1e3,
    "k": 1e3,
    "000": 1e3,
    "lakh": 1e5,
    "lakhs": 1e5,
    "lac": 1e5,
    "lacs": 1e5,
    "million": 1e6,
    "millions": 1e6,
    "mn": 1e6,
    "mio": 1e6,
    "m": 1e6,
    "crore": 1e7,
    "crores": 1e7,
    "cr": 1e7,
    "billion": 1e9,
    "billions": 1e9,
    "bn": 1e9,
    "b": 1e9,
    "trillion": 1e12,
    "trillions": 1e12,
    "tn": 1e12,
    "tr": 1e12,
    "lakh crore": 1e12,
    "trn": 1e12,
}

CURRENCY_ALIASES: dict[str, str] = {
    "₹": "INR",
    "rs": "INR",
    "rs.": "INR",
    "inr": "INR",
    "rupee": "INR",
    "rupees": "INR",
    "indian rupee": "INR",
    "indian rupees": "INR",
    "$": "USD",
    "us$": "USD",
    "usd": "USD",
    "us dollar": "USD",
    "us dollars": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "€": "EUR",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "pound": "GBP",
    "pounds sterling": "GBP",
    "¥": "JPY",
    "jpy": "JPY",
    "yen": "JPY",
    "sdr": "SDR",
}

# Non-currency base units, expressed as (canonical, class, factor to class base).
_SIMPLE_UNITS: dict[str, tuple[str, str, float]] = {
    "percent": ("%", RATIO, 1.0),
    "percentage": ("%", RATIO, 1.0),
    "per cent": ("%", RATIO, 1.0),
    "pct": ("%", RATIO, 1.0),
    "%": ("%", RATIO, 1.0),
    "ratio": ("ratio", RATIO, 100.0),
    "times": ("x", DIMENSIONLESS, 1.0),
    "x": ("x", DIMENSIONLESS, 1.0),
    "percentage point": ("pp", RATIO_CHANGE, 1.0),
    "percentage points": ("pp", RATIO_CHANGE, 1.0),
    "pp": ("pp", RATIO_CHANGE, 1.0),
    "ppt": ("pp", RATIO_CHANGE, 1.0),
    "basis point": ("pp", RATIO_CHANGE, 0.01),
    "basis points": ("pp", RATIO_CHANGE, 0.01),
    "bps": ("pp", RATIO_CHANGE, 0.01),
    "bp": ("pp", RATIO_CHANGE, 0.01),
    "kilogram": ("kg", MASS, 1.0),
    "kilograms": ("kg", MASS, 1.0),
    "kg": ("kg", MASS, 1.0),
    "gram": ("kg", MASS, 0.001),
    "grams": ("kg", MASS, 0.001),
    "tonne": ("kg", MASS, 1000.0),
    "tonnes": ("kg", MASS, 1000.0),
    "ton": ("kg", MASS, 1000.0),
    "tons": ("kg", MASS, 1000.0),
    "mt": ("kg", MASS, 1000.0),
    "metre": ("m", DISTANCE, 1.0),
    "metres": ("m", DISTANCE, 1.0),
    "meter": ("m", DISTANCE, 1.0),
    "m": ("m", DISTANCE, 1.0),
    "km": ("m", DISTANCE, 1000.0),
    "kilometre": ("m", DISTANCE, 1000.0),
    "kilometres": ("m", DISTANCE, 1000.0),
    "kilometer": ("m", DISTANCE, 1000.0),
    "mile": ("m", DISTANCE, 1609.344),
    "miles": ("m", DISTANCE, 1609.344),
    "sq ft": ("sqm", AREA, 0.092903),
    "square feet": ("sqm", AREA, 0.092903),
    "square foot": ("sqm", AREA, 0.092903),
    "sqft": ("sqm", AREA, 0.092903),
    "sq m": ("sqm", AREA, 1.0),
    "square metre": ("sqm", AREA, 1.0),
    "square metres": ("sqm", AREA, 1.0),
    "sqm": ("sqm", AREA, 1.0),
    "acre": ("sqm", AREA, 4046.86),
    "acres": ("sqm", AREA, 4046.86),
    "hectare": ("sqm", AREA, 10000.0),
    "hectares": ("sqm", AREA, 10000.0),
    "day": ("day", DURATION, 1.0),
    "days": ("day", DURATION, 1.0),
    "week": ("day", DURATION, 7.0),
    "weeks": ("day", DURATION, 7.0),
    "month": ("day", DURATION, 30.4375),
    "months": ("day", DURATION, 30.4375),
    "year": ("day", DURATION, 365.25),
    "years": ("day", DURATION, 365.25),
    "hour": ("day", DURATION, 1 / 24),
    "hours": ("day", DURATION, 1 / 24),
    "mw": ("kwh", ENERGY, 1000.0),
    "kwh": ("kwh", ENERGY, 1.0),
    "gwh": ("kwh", ENERGY, 1e6),
}

# Countable nouns that carry no dimension of their own. Recorded so that "shipments"
# and "employees" stay distinguishable in the unit label without being convertible.
_COUNT_NOUNS = {
    "shipment",
    "shipments",
    "parcel",
    "parcels",
    "employee",
    "employees",
    "customer",
    "customers",
    "client",
    "clients",
    "store",
    "stores",
    "facility",
    "facilities",
    "centre",
    "centres",
    "center",
    "centers",
    "hub",
    "hubs",
    "vehicle",
    "vehicles",
    "share",
    "shares",
    "unit",
    "units",
    "person",
    "persons",
    "people",
    "director",
    "directors",
    "branch",
    "branches",
    "account",
    "accounts",
    "transaction",
    "transactions",
    "pincode",
    "pincodes",
    "city",
    "cities",
    "count",
    "number",
}

_NUMBER_PATTERN = re.compile(
    r"""
    (?P<sign>[-+−])?
    \s*
    (?P<digits>
        \d{1,3}(?:,\d{2,3})+(?:\.\d+)?   # grouped: 1,234,567.8 or Indian 1,23,456
      | \d+(?:\.\d+)?                    # plain
      | \.\d+                            # leading decimal
    )
    """,
    re.VERBOSE,
)

_PAREN_NEGATIVE = re.compile(r"^\s*\(\s*(?P<body>[^()]+?)\s*\)\s*$")


def parse_number(text: str) -> float | None:
    """Read a number out of free text, honouring accounting parentheses and grouping."""
    if text is None:
        return None
    candidate = str(text).strip()
    if not candidate:
        return None

    negative = False
    paren = _PAREN_NEGATIVE.match(candidate)
    if paren:
        candidate = paren.group("body")
        negative = True

    match = _NUMBER_PATTERN.search(candidate)
    if not match:
        return None

    digits = match.group("digits").replace(",", "")
    try:
        value = float(Decimal(digits))
    except (InvalidOperation, ValueError):
        return None

    if match.group("sign") in {"-", "−"}:
        negative = not negative
    return -value if negative else value


def normalize_currency(text: str | None) -> str | None:
    if not text:
        return None
    key = str(text).strip().lower().rstrip(".")
    if key in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[key]
    key_with_dot = f"{key}."
    if key_with_dot in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[key_with_dot]
    if len(key) == 3 and key.isalpha():
        return key.upper()
    return None


def normalize_scale(text: str | None) -> float | None:
    if text is None:
        return None
    key = str(text).strip().lower().strip("'’\" ")
    if not key:
        return None
    key = key.rstrip(".")
    if key in SCALES:
        return SCALES[key]
    number = parse_number(key)
    if number and number > 0:
        return number
    return None


_SCALE_TOKEN = "|".join(
    sorted(
        (re.escape(token) for token in SCALES if token not in {"m", "b", "k"}),
        key=len,
        reverse=True,
    )
)
_CURRENCY_TOKEN = "|".join(
    sorted((re.escape(token) for token in CURRENCY_ALIASES), key=len, reverse=True)
)

_UNIT_DECLARATION = re.compile(
    rf"""
    (?:all\s+(?:amounts|figures)|amounts|figures|values|in|figures\s+in)?
    \s*
    (?:are\s+)?
    (?:stated\s+)?
    (?:in\s+)?
    (?P<currency>{_CURRENCY_TOKEN})?
    \s*
    (?:in\s+)?
    (?P<scale>{_SCALE_TOKEN})
    \b
    """,
    re.VERBOSE | re.IGNORECASE,
)


def detect_unit_declaration(text: str) -> tuple[str | None, float | None]:
    """Pull a page-level or document-level scale declaration out of a line of text.

    Handles the forms these filings actually use, e.g.
    "(All amounts in Indian Rupees in million, unless otherwise stated)" and "(₹ in crore)".
    """
    if not text:
        return None, None
    window = text[:2000]
    best: tuple[str | None, float | None] = (None, None)
    for match in _UNIT_DECLARATION.finditer(window):
        scale = normalize_scale(match.group("scale"))
        if scale is None:
            continue
        currency = normalize_currency(match.group("currency"))
        if currency or best[1] is None:
            best = (currency or best[0], scale)
        if currency:
            break
    return best


def resolve_unit(
    unit_text: str | None,
    *,
    scale: str | float | None = None,
    currency: str | None = None,
) -> Unit | None:
    """Turn free-text unit / scale / currency hints into a comparable `Unit`.

    Returns None when nothing recognisable is present, which the caller treats as an
    unresolved unit rather than guessing — a wrong unit is worse than a missing one.
    """
    scale_factor = 1.0
    if isinstance(scale, int | float):
        scale_factor = float(scale) or 1.0
    elif scale:
        scale_factor = normalize_scale(scale) or 1.0

    resolved_currency = normalize_currency(currency)
    raw = (unit_text or "").strip()
    cleaned = raw.lower().replace("(", " ").replace(")", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,;:")

    if not cleaned and not resolved_currency:
        return None

    # Strip a leading currency symbol or word out of the unit label.
    for alias in sorted(CURRENCY_ALIASES, key=len, reverse=True):
        if cleaned == alias or cleaned.startswith(f"{alias} ") or cleaned.startswith(f"{alias}."):
            resolved_currency = resolved_currency or CURRENCY_ALIASES[alias]
            cleaned = cleaned[len(alias) :].strip(" .")
            break

    # Absorb any scale word embedded in the label, e.g. "crore" inside "₹ crore".
    tokens = [token for token in re.split(r"[\s/]+", cleaned) if token]
    remaining: list[str] = []
    for token in tokens:
        stripped = token.strip("'’\".,")
        embedded = normalize_scale(stripped)
        if embedded is not None and embedded != 1.0:
            scale_factor *= embedded
            continue
        if stripped in {"in", "of", "per"}:
            continue
        remaining.append(stripped)
    tail = " ".join(remaining).strip()

    if resolved_currency:
        label = f"{resolved_currency} {_scale_label(scale_factor)}".strip()
        return Unit(
            canonical=label, unit_class=CURRENCY, factor=scale_factor, currency=resolved_currency
        )

    if not tail:
        if scale_factor != 1.0:
            return Unit(canonical=_scale_label(scale_factor), unit_class=COUNT, factor=scale_factor)
        return None

    simple = _SIMPLE_UNITS.get(tail)
    if simple is None and tail.endswith("s"):
        simple = _SIMPLE_UNITS.get(tail[:-1])
    if simple:
        canonical, unit_class, factor = simple
        return Unit(canonical=canonical, unit_class=unit_class, factor=factor * scale_factor)

    singular = tail[:-1] if tail.endswith("s") else tail
    if tail in _COUNT_NOUNS or singular in _COUNT_NOUNS:
        return Unit(canonical=singular, unit_class=COUNT, factor=scale_factor)

    # Unknown but non-empty: keep it as its own class so it is only ever compared with
    # an identically-labelled unit, never silently coerced into something else.
    return Unit(canonical=tail, unit_class=f"other:{singular}", factor=scale_factor)


SCALE_LABELS = frozenset({"thousand", "lakh", "million", "crore", "billion", "trillion"})


def is_bare_scale(unit: Unit | None) -> bool:
    """True when a unit says how big the number is but not what it measures.

    "in millions" with no noun beside it is a scale, not a unit. The caller needs to know
    the difference so it can decide whether the document's declared currency applies: it
    does to a bare scale, and it must not to a percentage or a shipment count.
    """
    if unit is None:
        return False
    if unit.currency or unit.unit_class != COUNT:
        return False
    return unit.canonical in SCALE_LABELS or unit.canonical.startswith("x")


def _scale_label(factor: float) -> str:
    for name, value in (
        ("trillion", 1e12),
        ("billion", 1e9),
        ("crore", 1e7),
        ("million", 1e6),
        ("lakh", 1e5),
        ("thousand", 1e3),
    ):
        if abs(factor - value) < 1e-6:
            return name
    return "" if abs(factor - 1.0) < 1e-9 else f"x{factor:g}"


def make_quantity(value: float, unit: Unit | None) -> Quantity | None:
    if unit is None:
        return None
    return Quantity(value=value, unit=unit, base_value=value * unit.factor)


def comparable(left: Unit | None, right: Unit | None) -> bool:
    if left is None or right is None:
        return False
    return left.comparison_key == right.comparison_key


def relative_difference(left: float, right: float) -> float:
    """Symmetric relative gap, safe around zero.

    Uses the larger magnitude as the denominator so the result is order independent and
    never explodes when one side rounds to zero.
    """
    if left == right:
        return 0.0
    scale = max(abs(left), abs(right))
    if scale == 0:
        return 0.0
    return abs(left - right) / scale


# Floor on how much two values may differ and still count as the same claim. These are
# deliberately tight: they exist to absorb floating point noise, not to express what counts
# as agreement. That judgement belongs to `rounding_tolerance` below, which derives the band
# from how precisely each side was actually written. A flat one percent band would call
# 6.5% and 6.6% GDP growth the same figure, and they are not.
CLASS_TOLERANCE: dict[str, float] = {
    CURRENCY: 0.0005,
    RATIO: 0.001,
    RATIO_CHANGE: 0.001,
    COUNT: 0.0005,
    MASS: 0.001,
    DISTANCE: 0.001,
    AREA: 0.001,
    DURATION: 0.001,
    ENERGY: 0.001,
    DIMENSIONLESS: 0.001,
}

DEFAULT_TOLERANCE = 0.001


def tolerance_for(unit_class: str | None) -> float:
    if not unit_class:
        return DEFAULT_TOLERANCE
    return CLASS_TOLERANCE.get(unit_class, DEFAULT_TOLERANCE)


def rounding_tolerance(left: float, right: float, unit_class: str | None) -> float:
    """Tolerance widened for the significant figures actually present.

    "8,142 Cr" against "81,419.7 million" is the same number reported at different
    precision. The band therefore has to scale with how coarsely the coarser side was
    rounded, not sit at a flat percentage.
    """
    base = tolerance_for(unit_class)
    coarser = min(abs(left), abs(right))
    if coarser == 0:
        return base
    magnitude = max(abs(left), abs(right))
    implied = _rounding_step(coarser) / magnitude if magnitude else base
    return max(base, min(implied, 0.05))


def _rounding_step(value: float) -> float:
    """Half of the last significant place of `value` as written."""
    text = f"{value:.10f}".rstrip("0")
    if "." in text:
        decimals = len(text.split(".", 1)[1])
        if decimals > 0:
            return 0.5 * (10**-decimals)
    integer = int(abs(value))
    trailing = 0
    while integer and integer % 10 == 0:
        integer //= 10
        trailing += 1
    return 0.5 * (10**trailing)
