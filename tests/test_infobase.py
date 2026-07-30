"""Tests for the GC InfoBase ingester.

Fixtures reproduce the real column names and value shapes from the live CSVs
(`fy_ef`, `org_id`, `sobj_en`, `voted_or_statutory`), read from
open.canada.ca dataset a35cf382-690c-4221-a971-cf0fd189a46f.
"""

from govbudget.ckan import Package, Resource
from govbudget.sources import infobase

STANDARD_OBJECT_CSV = b"""fy_ef,org_id,org_name,sobj_en,expenditures
2023-24,1,Department of Agriculture and Agri-Food,Personnel,582717800.00
2023-24,1,Department of Agriculture and Agri-Food,Transfer payments,22928246.14
2023-24,1,Department of Agriculture and Agri-Food,Public debt charges,1000.00
2023-24,1,Department of Agriculture and Agri-Food,External revenues,-5000.00
2023-24,2,Canada Revenue Agency,Personnel,
"""

VOTE_CSV = b"""fy_ef,org_id,org_name,voted_or_statutory,description,authorities,expenditures
2023-24,1,Department of Agriculture,voted,Operating/Program,756690489.00,704941276.00
2023-24,1,Department of Agriculture,statutory,Old Age Security payments,459143202.00,359941850.00
2023-24,3,Some Org,voted,Capital,,
"""


class TestStandardObject:
    def _lines(self):
        return list(infobase.parse_standard_object(STANDARD_OBJECT_CSV, "hash1"))

    def test_skips_rows_without_an_amount(self):
        """A blank expenditure is not a zero."""
        assert len(self._lines()) == 4

    def test_maps_to_harmonized_economic_categories(self):
        by_label = {line.economic_source_label: line for line in self._lines()}
        assert by_label["Personnel"].economic_category == "personnel"
        assert by_label["Transfer payments"].economic_category == "transfers"
        assert by_label["Public debt charges"].economic_category == "debt_service"

    def test_keeps_the_publisher_label_verbatim(self):
        """Traceability: the harmonized category is derived, not authoritative."""
        assert self._lines()[0].economic_source_label == "Personnel"

    def test_negative_revenue_lines_are_preserved(self):
        revenue = next(x for x in self._lines() if x.economic_source_label == "External revenues")
        assert revenue.amount == -5000.0

    def test_measure_and_jurisdiction(self):
        line = self._lines()[0]
        assert line.measure == "expenditures"
        assert line.jurisdiction == "ca-federal"
        assert line.fiscal_year == "2023-24"
        assert line.organization_id == "1"
        assert line.source_content_hash == "hash1"


class TestVote:
    def _lines(self):
        return list(infobase.parse_vote(VOTE_CSV, "hash2"))

    def test_authorities_and_expenditures_are_separate_rows(self):
        """The gap between them is lapsed spending and must stay visible."""
        lines = self._lines()
        assert len(lines) == 4
        assert {x.measure for x in lines} == {"authorities", "expenditures"}

    def test_amounts_land_on_the_right_measure(self):
        lines = self._lines()
        operating = [x for x in lines if x.programme == "Operating/Program"]
        by_measure = {x.measure: x.amount for x in operating}
        assert by_measure["authorities"] == 756690489.00
        assert by_measure["expenditures"] == 704941276.00

    def test_appropriation_type_mapped(self):
        lines = self._lines()
        assert {x.appropriation for x in lines} == {"voted", "statutory"}

    def test_rows_with_no_amounts_emit_nothing(self):
        assert not [x for x in self._lines() if x.organization_id == "3"]

    def test_vote_description_retained_in_dimensions(self):
        line = self._lines()[0]
        assert line.dimensions["table"] == "vote"
        assert line.dimensions["vote"] == "Operating/Program"


class TestResourceSelection:
    def _package(self, *names):
        return Package(
            id="p", name="gc-infobase", title="GC InfoBase",
            resources=[
                Resource(id=str(i), name=n, url=f"https://x/{i}.csv", format="CSV")
                for i, n in enumerate(names)
            ],
        )

    def test_finds_the_named_table(self):
        table = infobase.TABLES[0]
        package = self._package("Something Else", table.resource_name)
        assert infobase.find_resource(package, table).name == table.resource_name

    def test_english_french_pairs_resolve_to_one(self):
        """Both twins share a name; either carries the same data."""
        table = infobase.TABLES[0]
        package = self._package(table.resource_name, table.resource_name)
        assert infobase.find_resource(package, table) is not None

    def test_missing_resource_returns_none_rather_than_raising(self):
        assert infobase.find_resource(self._package("Unrelated"), infobase.TABLES[0]) is None

    def test_non_csv_resources_ignored(self):
        table = infobase.TABLES[0]
        package = Package(
            id="p", name="n", title="t",
            resources=[Resource(id="1", name=table.resource_name,
                                url="https://x/a.pdf", format="PDF")],
        )
        assert infobase.find_resource(package, table) is None


class TestDelimiterHandling:
    def test_semicolon_delimited_csv(self):
        """Some Canadian portals publish semicolon-separated CSV."""
        data = STANDARD_OBJECT_CSV.replace(b",", b";")
        assert len(list(infobase.parse_standard_object(data, "h"))) == 4

    def test_utf8_bom_tolerated(self):
        data = b"\xef\xbb\xbf" + STANDARD_OBJECT_CSV
        assert len(list(infobase.parse_standard_object(data, "h"))) == 4


TRANSFERS_CSV = b"""fy_ef,org_id,org_name,type,description,expenditures,authorities
FY 2024-25,12,Department of Finance,Grant,(S) Canada Health Transfer,52070383303.00,52070383303.00
FY 2024-25,12,Department of Finance,Contribution,Some contribution programme,1000.00,2000.00
FY 2024-25,12,Department of Finance,Grant,No amounts here,,
"""


class TestTransferPayments:
    def _lines(self):
        return list(infobase.parse_transfer_payments(TRANSFERS_CSV, "h"))

    def test_fy_prefix_stripped_so_years_join_across_tables(self):
        """This table writes 'FY 2024-25'; every other table writes '2024-25'.
        Left as-is the same year would not join."""
        assert {x.fiscal_year for x in self._lines()} == {"2024-25"}
        assert infobase._fiscal_year("2023-24") == "2023-24"

    def test_named_programme_retained(self):
        cht = next(x for x in self._lines() if "Canada Health Transfer" in x.programme)
        assert cht.amount == 52070383303.00 or cht.measure == "authorities"

    def test_category_is_fixed_not_derived(self):
        """Every row in this table is a transfer by construction."""
        assert {x.economic_category for x in self._lines()} == {"transfers"}

    def test_grant_and_contribution_distinguished(self):
        assert {x.dimensions["transfer_type"] for x in self._lines()} == {"Grant", "Contribution"}

    def test_both_measures_emitted(self):
        contrib = [x for x in self._lines() if x.dimensions["transfer_type"] == "Contribution"]
        assert {x.measure: x.amount for x in contrib} == {
            "expenditures": 1000.0, "authorities": 2000.0
        }

    def test_rows_without_amounts_emit_nothing(self):
        assert not [x for x in self._lines() if x.programme == "No amounts here"]
