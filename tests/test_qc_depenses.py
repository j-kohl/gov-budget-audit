"""Tests for the Quebec Budget de dépenses ingester.

Column names in these fixtures are the real ones, including the drift between
years: 2026-27 publishes SUPERCATEGORIE and DEPENSES_SANS_CREDITS, while
2021-22 publishes SUPER_CATEGORIE and DEPENSES_SANS_CREDIT.
"""

from govbudget.ckan import Package, Resource
from govbudget.sources import qc_depenses

CSV_2026 = (
    "PORTEFEUILLE,PROGRAMME,ELEMENT,SUPERCATEGORIE,TYPE_DE_CREDITS,"
    "BUDGET_DE_DEPENSES_26_27,DEPENSES_SANS_CREDITS_26_27,"
    "BUDGET_INVESTISSEMENT_26_27,CREDITS_TOTAUX_26_27\n"
    "Affaires municipales,0050.01 Soutien aux activités,"
    "0050.01.01 Direction et administration,1 Rémunération,Votés,"
    "20301000,0,0,20301000\n"
    "Santé et Services sociaux,0075.02 Services de santé,"
    "0075.02.01 Réseau,5 Transfert,Permanents,"
    "1000000,500000,250000,1750000\n"
    "Finances,0010.01 Dette,0010.01.01 Service,4 Service de la dette,Votés,"
    "0,0,0,0\n"
).encode()

# 2021-22 spellings: SUPER_CATEGORIE, DEPENSES_SANS_CREDIT (singular).
CSV_2021 = (
    "PORTEFEUILLE,PROGRAMME,ELEMENT,SUPER_CATEGORIE,TYPE_DE_CREDITS,"
    "BUDGET_DE_DEPENSES_21_22,DEPENSES_SANS_CREDIT_21_22,"
    "BUDGET_INVESTISSEMENT_21_22,CREDITS_TOTAUX_21_22\n"
    "Éducation,0030.01 Enseignement,0030.01.01 Primaire,2 Fonctionnement,Votés,"
    "500,0,0,500\n"
).encode()


class TestFiscalYear:
    def test_read_from_the_column_suffix(self):
        """The suffix is reliable where the file name is not, and it yields the
        same YYYY-YY shape GC InfoBase uses, so the two tables join."""
        assert qc_depenses.parse(CSV_2026, "h")[0].fiscal_year == "2026-27"
        assert qc_depenses.parse(CSV_2021, "h")[0].fiscal_year == "2021-22"

    def test_no_suffix_yields_nothing_rather_than_a_wrong_year(self):
        data = b"PORTEFEUILLE,PROGRAMME,CREDITS_TOTAUX\nX,Y,100\n"
        assert qc_depenses.parse(data, "h") == []


class TestColumnDrift:
    def test_super_categorie_spelling_variants(self):
        """SUPERCATEGORIE and SUPER_CATEGORIE must resolve to the same field."""
        assert qc_depenses.parse(CSV_2026, "h")[0].economic_source_label == "1 Rémunération"
        assert qc_depenses.parse(CSV_2021, "h")[0].economic_source_label == "2 Fonctionnement"

    def test_depenses_sans_credit_singular_and_plural(self):
        assert qc_depenses._normalize("DEPENSES_SANS_CREDIT_21_22") == "DEPENSESSANSCREDIT"
        assert qc_depenses._normalize("DEPENSES_SANS_CREDITS_26_27") == "DEPENSESSANSCREDIT"

    def test_credits_totaux_not_mangled_by_plural_stripping(self):
        """TYPE_DE_CREDITS and CREDITS_TOTAUX must not be truncated by alias handling."""
        assert qc_depenses._normalize("CREDITS_TOTAUX_26_27") == "CREDITSTOTAUX"
        assert qc_depenses._normalize("TYPE_DE_CREDITS") == "TYPEDECREDITS"


class TestMeasures:
    def _lines(self):
        return qc_depenses.parse(CSV_2026, "h")

    def test_components_emitted_as_separate_measures(self):
        """BUDGET + SANS_CREDITS + INVESTISSEMENT equals CREDITS_TOTAUX on every
        published row, so these are components of a total. Summing across
        measures would double-count."""
        sante = [x for x in self._lines() if x.organization.startswith("Santé")]
        by_measure = {x.measure: x.amount for x in sante}
        assert by_measure["expenditure_budget"] == 1000000
        assert by_measure["spending_without_credits"] == 500000
        assert by_measure["investment_budget"] == 250000
        assert by_measure["authorities"] == 1750000
        components = sum(
            v for k, v in by_measure.items() if k != "authorities"
        )
        assert components == by_measure["authorities"]

    def test_zero_rows_dropped(self):
        """A nil credit adds nothing to any total."""
        assert not [x for x in self._lines() if x.organization == "Finances"]


class TestDimensions:
    def _first(self):
        return qc_depenses.parse(CSV_2026, "h")[0]

    def test_programme_code_split_from_label(self):
        line = self._first()
        assert line.programme_id == "0050.01"
        assert line.programme == "Soutien aux activités"

    def test_element_kept_in_dimensions(self):
        detail = self._first().dimensions
        assert detail["element_code"] == "0050.01.01"
        assert detail["element"] == "Direction et administration"

    def test_economic_category_harmonized(self):
        assert self._first().economic_category == "personnel"
        sante = next(x for x in qc_depenses.parse(CSV_2026, "h")
                     if x.organization.startswith("Santé"))
        assert sante.economic_category == "transfers"

    def test_appropriation_mapped(self):
        lines = qc_depenses.parse(CSV_2026, "h")
        assert {x.appropriation for x in lines} == {"voted", "statutory"}

    def test_jurisdiction_and_organization(self):
        line = self._first()
        assert line.jurisdiction == "qc"
        assert line.organization == "Affaires municipales"


class TestResourceSelection:
    def test_main_tables_only_newest_first(self):
        package = Package(
            id="p", name="budget", title="Budget de dépenses",
            resources=[
                Resource(id="1", name="Budgets et crédits des ministères et organismes 2024-2025",
                         url="https://x/a.csv", format="CSV"),
                Resource(id="2", name="Budgets et crédits des ministères et organismes 2026-2027",
                         url="https://x/b.csv", format="CSV"),
                Resource(id="3", name="Crédits de transfert des ministères et organismes 2026-2027",
                         url="https://x/c.csv", format="CSV"),
                Resource(id="4", name="Budget de dépenses volume 1", url="https://x/d.pdf",
                         format="PDF"),
            ],
        )
        tables = qc_depenses.main_tables(package)
        assert [t.id for t in tables] == ["2", "1"]


class TestRobustness:
    def test_semicolon_delimited(self):
        assert len(qc_depenses.parse(CSV_2026.replace(b",", b";"), "h")) > 0

    def test_bom_tolerated(self):
        assert len(qc_depenses.parse(b"\xef\xbb\xbf" + CSV_2026, "h")) > 0

    def test_empty_input(self):
        assert qc_depenses.parse(b"", "h") == []

    def test_rows_without_a_portefeuille_skipped(self):
        data = CSV_2026 + b",,,,,1,0,0,1\n"
        before = len(qc_depenses.parse(CSV_2026, "h"))
        assert len(qc_depenses.parse(data, "h")) == before
