from datetime import date

from govbudget.parsers._util import clean_text, normalize_tag, parse_amount, parse_date, parse_int


class TestParseAmount:
    def test_plain_numbers(self):
        assert parse_amount(1234.56) == 1234.56
        assert parse_amount("1234.56") == 1234.56
        assert parse_amount(0) == 0.0

    def test_anglophone_formatting(self):
        assert parse_amount("1,234.56") == 1234.56
        assert parse_amount("$1,234,567.89") == 1234567.89

    def test_french_formatting(self):
        """Quebec sources use space groups and a comma decimal mark."""
        assert parse_amount("1 234,56") == 1234.56
        assert parse_amount("1 234 567,89") == 1234567.89
        # Narrow no-break space, which is what Word-authored documents emit.
        assert parse_amount("1 234,56") == 1234.56

    def test_ambiguous_separator_resolves_by_position(self):
        """The separator nearest the end is the decimal mark."""
        assert parse_amount("1.234,56") == 1234.56
        assert parse_amount("1,234.56") == 1234.56

    def test_thousands_only(self):
        assert parse_amount("1,234") == 1234.0
        assert parse_amount("1 234") == 1234.0

    def test_repeated_separator_is_grouping(self):
        """'1,234,567' must not be read as a decimal and silently become None."""
        assert parse_amount("1,234,567") == 1234567.0
        assert parse_amount("1.234.567") == 1234567.0

    def test_lone_separator_with_three_digits_is_grouping(self):
        """The documented tie-break: three trailing digits means grouping."""
        assert parse_amount("1,234") == 1234.0
        assert parse_amount("1.234") == 1234.0

    def test_lone_separator_with_two_digits_is_decimal(self):
        assert parse_amount("1234,56") == 1234.56
        assert parse_amount("1234.56") == 1234.56

    def test_single_trailing_digit_is_decimal(self):
        assert parse_amount("1234,5") == 1234.5

    def test_negatives_and_junk(self):
        assert parse_amount("-500,25") == -500.25
        assert parse_amount("") is None
        assert parse_amount(None) is None
        assert parse_amount("n/d") is None
        assert parse_amount("-") is None

    def test_booleans_are_not_numbers(self):
        assert parse_amount(True) is None


class TestParseDate:
    def test_iso_variants(self):
        assert parse_date("2021-03-15") == date(2021, 3, 15)
        assert parse_date("2021-03-15T10:30:00") == date(2021, 3, 15)
        assert parse_date("2021-03-15T10:30:00Z") == date(2021, 3, 15)
        assert parse_date("2021-03-15T10:30:00+00:00") == date(2021, 3, 15)

    def test_legacy_formats(self):
        assert parse_date("2009/06/01") == date(2009, 6, 1)
        assert parse_date("20090601") == date(2009, 6, 1)

    def test_empty(self):
        assert parse_date(None) is None
        assert parse_date("") is None
        assert parse_date("   ") is None

    def test_passthrough(self):
        assert parse_date(date(2020, 1, 1)) == date(2020, 1, 1)


class TestNormalizeTag:
    def test_collapses_spelling_variants(self):
        """The whole field-mapping strategy rests on this."""
        for variant in ("NuméroSEAO", "NUMERO_SEAO", "numero-seao", "Numero SEAO"):
            assert normalize_tag(variant) == "numeroseao"

    def test_strips_namespace(self):
        assert normalize_tag("{http://example.org/ns}numeroSeao") == "numeroseao"

    def test_accents(self):
        assert normalize_tag("Détail") == "detail"
        assert normalize_tag("québec") == "quebec"


def test_clean_text():
    assert clean_text("  hello   world \n") == "hello world"
    assert clean_text("") is None
    assert clean_text(None) is None


def test_parse_int():
    assert parse_int("3") == 3
    assert parse_int("3,0") == 3
    assert parse_int(None) is None
