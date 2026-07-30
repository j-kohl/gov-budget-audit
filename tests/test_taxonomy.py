"""Tests for the federal/Quebec crosswalk.

The vocabularies and the figures quoted here were read from the live sources:
GC InfoBase FY2023-24 Public Accounts, and the Quebec Budget de dépenses
2026-27. They are asserted because the whole point of the crosswalk is that
somebody checked whether these categories actually mean the same thing.
"""

import pytest

from govbudget import taxonomy


class TestFederalMapping:
    @pytest.mark.parametrize(
        "standard_object,expected",
        [
            ("Personnel", "personnel"),
            ("Transfer payments", "transfers"),
            ("Public debt charges", "debt_service"),
            ("Professional and special services", "operating"),
            ("Transportation and communications", "operating"),
            ("Acquisition of machinery and equipment", "capital"),
            ("Acquisition of land, buildings and works", "capital"),
            ("Other subsidies and payments", "other"),
        ],
    )
    def test_standard_objects(self, standard_object, expected):
        assert taxonomy.federal_economic(standard_object) == expected

    def test_every_published_standard_object_is_mapped(self):
        """All 14 categories InfoBase publishes must land somewhere explicit."""
        assert len(taxonomy.FEDERAL_STANDARD_OBJECT) == 14
        for category in taxonomy.FEDERAL_STANDARD_OBJECT.values():
            assert category in taxonomy.ECONOMIC_CATEGORIES

    def test_revenue_lines_are_not_spending(self):
        """External/internal revenues are negative offsets, not a spend type."""
        assert taxonomy.federal_economic("External revenues") == "other"
        assert taxonomy.federal_economic("Internal revenues") == "other"

    def test_unknown_falls_back_rather_than_raising(self):
        assert taxonomy.federal_economic("Something New In 2030") == "other"
        assert taxonomy.federal_economic(None) == "other"


class TestQuebecMapping:
    @pytest.mark.parametrize(
        "supercategorie,expected",
        [
            ("1 Rémunération", "personnel"),
            ("2 Fonctionnement", "operating"),
            ("3 Immobilisations autres qu'en ressources informationnelles", "capital"),
            ("M Immobilisations en ressources informationnelles", "capital"),
            ("4 Service de la dette", "debt_service"),
            ("5 Transfert", "transfers"),
            ("6 Prêts, placements, avances et autres coûts", "other"),
            ("F Affectation à un fonds spécial", "other"),
            ("P Créances douteuses, autres provisions et pertes", "other"),
            ("A moins Amortissement", "other"),
        ],
    )
    def test_supercategories(self, supercategorie, expected):
        assert taxonomy.quebec_economic(supercategorie) == expected

    def test_keyed_on_code_not_label(self):
        """Labels are reworded between years; the leading code is stable."""
        assert taxonomy.quebec_economic("5 Transfert") == "transfers"
        assert taxonomy.quebec_economic("5 Transferts aux organismes") == "transfers"

    def test_every_published_supercategorie_is_mapped(self):
        assert len(taxonomy.QUEBEC_SUPERCATEGORIE) == 10

    def test_unknown_falls_back(self):
        assert taxonomy.quebec_economic("Z Nouvelle catégorie") == "other"
        assert taxonomy.quebec_economic(None) == "other"


class TestAppropriation:
    def test_the_one_dimension_that_maps_exactly(self):
        assert taxonomy.quebec_appropriation("Votés") == "voted"
        assert taxonomy.quebec_appropriation("Permanents") == "statutory"
        assert taxonomy.FEDERAL_APPROPRIATION["voted"] == "voted"
        assert taxonomy.FEDERAL_APPROPRIATION["statutory"] == "statutory"

    def test_unknown_is_none_not_a_guess(self):
        assert taxonomy.quebec_appropriation("Autre") is None


class TestComparability:
    def test_personnel_is_flagged_not_comparable(self):
        """Federal Personnel $65.3B vs Quebec Rémunération $4.9B.

        Quebec's payroll is inside its Transfert line because the health and
        education networks employ the staff. Charting these side by side would
        be a straightforward misrepresentation, so the flag has to say so.
        """
        verdict = taxonomy.comparability("personnel")
        assert verdict.level == "not-comparable"
        assert "Transfert" in verdict.note

    def test_debt_service_is_the_safe_comparison(self):
        assert taxonomy.is_comparable("debt_service")

    def test_transfers_carry_a_double_counting_warning(self):
        verdict = taxonomy.comparability("transfers")
        assert verdict.level == "caution"
        assert "double-counts" in verdict.note

    def test_every_category_has_a_verdict(self):
        for category in taxonomy.ECONOMIC_CATEGORIES:
            assert taxonomy.comparability(category).level in {
                "comparable", "caution", "not-comparable"
            }

    def test_only_debt_service_is_freely_comparable(self):
        """If this ever changes, it should be a deliberate, reviewed decision."""
        comparable = [c for c in taxonomy.ECONOMIC_CATEGORIES if taxonomy.is_comparable(c)]
        assert comparable == ["debt_service"]

    def test_unknown_category_is_not_comparable(self):
        assert taxonomy.comparability("invented").level == "not-comparable"


class TestFrenchNotes:
    """The dashboard is French; an English caveat there reads as boilerplate."""

    def test_every_category_has_a_french_note(self):
        for category in taxonomy.ECONOMIC_CATEGORIES:
            note = taxonomy.comparability(category).note_fr
            assert note, category
            assert len(note) > 60, f"{category}: too short to explain anything"

    def test_french_note_is_not_the_english_one(self):
        for category in taxonomy.ECONOMIC_CATEGORIES:
            verdict = taxonomy.comparability(category)
            assert verdict.note_fr != verdict.note, category

    def test_the_personnel_note_names_the_actual_cause(self):
        assert "réseaux" in taxonomy.comparability("personnel").note_fr
