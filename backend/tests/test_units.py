import pytest

from app.core.units import (
    COUNT,
    CURRENCY,
    DURATION,
    MASS,
    RATIO,
    RATIO_CHANGE,
    comparable,
    detect_unit_declaration,
    is_bare_scale,
    parse_number,
    relative_difference,
    resolve_unit,
    rounding_tolerance,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("8,142", 8142.0),
        ("1,23,456", 123456.0),  # Indian digit grouping
        ("81,419.7", 81419.7),
        ("(1,234.5)", -1234.5),  # accounting parentheses mean negative
        ("12.5%", 12.5),
        ("-3.2", -3.2),
        (".75", 0.75),
        ("no digits here", None),
    ],
)
def test_parse_number(text, expected):
    assert parse_number(text) == expected


@pytest.mark.parametrize(
    ("declaration", "currency", "scale"),
    [
        ("(All amounts in Indian Rupees in million, unless otherwise stated)", "INR", 1e6),
        ("(₹ in crore)", "INR", 1e7),
        ("Rs. in lakhs", "INR", 1e5),
        ("(US$ billion)", "USD", 1e9),
        ("figures in thousands", None, 1e3),
        ("Notes to the consolidated financial statements", None, None),
    ],
)
def test_detect_unit_declaration(declaration, currency, scale):
    assert detect_unit_declaration(declaration) == (currency, scale)


@pytest.mark.parametrize(
    ("unit_text", "currency", "unit_class", "factor"),
    [
        ("Cr", "INR", CURRENCY, 1e7),
        ("million", "INR", CURRENCY, 1e6),
        ("%", None, RATIO, 1.0),
        ("bps", None, RATIO_CHANGE, 0.01),
        ("percentage points", None, RATIO_CHANGE, 1.0),
        ("'000 Tons", None, MASS, 1e6),
        ("Mn shipments", None, COUNT, 1e6),
        ("days", None, DURATION, 1.0),
    ],
)
def test_resolve_unit(unit_text, currency, unit_class, factor):
    unit = resolve_unit(unit_text, currency=currency)
    assert unit is not None
    assert unit.unit_class == unit_class
    assert unit.factor == pytest.approx(factor)


def test_crore_and_million_reduce_to_the_same_amount():
    """The Delhivery case: the deck reports crore, the annual report millions."""
    crore = resolve_unit("Cr", currency="INR")
    million = resolve_unit("million", currency="INR")
    assert comparable(crore, million)

    deck = 8142 * crore.factor
    statements = 81419.7 * million.factor
    difference = relative_difference(deck, statements)
    assert difference <= rounding_tolerance(deck, statements, CURRENCY)


def test_rounding_tolerance_widens_for_coarsely_stated_figures():
    """A figure rounded to the nearest crore cannot be held to a flat percentage band."""
    coarse, precise = 8142 * 1e7, 81419.7 * 1e6
    assert rounding_tolerance(coarse, precise, CURRENCY) >= relative_difference(coarse, precise)


def test_genuinely_different_values_stay_outside_tolerance():
    left, right = 8142 * 1e7, 7224 * 1e7
    assert relative_difference(left, right) > rounding_tolerance(left, right, CURRENCY)


def test_different_currencies_are_never_comparable():
    assert not comparable(
        resolve_unit("million", currency="INR"), resolve_unit("million", currency="USD")
    )


def test_percent_and_percentage_points_are_different_classes():
    assert not comparable(resolve_unit("%"), resolve_unit("pp"))


def test_unknown_unit_gets_its_own_class_rather_than_being_coerced():
    unit = resolve_unit("teu")
    assert unit is not None
    assert unit.unit_class.startswith("other:")
    assert not comparable(unit, resolve_unit("kg"))


def test_bare_scale_is_distinguished_from_a_real_unit():
    assert is_bare_scale(resolve_unit("million"))
    assert not is_bare_scale(resolve_unit("million", currency="INR"))
    assert not is_bare_scale(resolve_unit("%"))


def test_relative_difference_is_symmetric_and_safe_at_zero():
    assert relative_difference(100, 110) == relative_difference(110, 100)
    assert relative_difference(0, 0) == 0.0
