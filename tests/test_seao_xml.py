"""Tests for the SEAO XML parser.

Fixtures below reproduce the real published schema, taken from actual monthly
archives and cross-checked against "Format XML pour les données ouvertes du
SEAO" (Conseil du trésor, 2014). Tag names, the 0/1 flags and the
`0.000000`-for-not-applicable convention are all as SEAO really emits them.
"""

from datetime import date

from govbudget.parsers import seao_xml

AVIS = """<?xml version="1.0" encoding="utf-8"?>
<export xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" datedebut="2024-05-01" datefin="2024-05-31">
  <avis>
    <numeroseao>304462</numeroseao>
    <numero>EA-0902-02</numero>
    <organisme>Municipalité de Saint-Adolphe-d'Howard.</organisme>
    <municipal>1</municipal>
    <ville>Saint-Adolphe-d'Howard</ville>
    <province>QC</province>
    <titre>Caserne de pompier (route 329)</titre>
    <type>3</type>
    <nature>3</nature>
    <categorieseao>C03 - Autres travaux de construction</categorieseao>
    <datepublication>2009-06-01 14:27</datepublication>
    <datefermeture>2009-06-25 14:00</datefermeture>
    <dateadjudication>2009-07-21</dateadjudication>
    <regionlivraison>15</regionlivraison>
    <unspscprincipale>72131600</unspscprincipale>
    <disposition />
    <hyperlienseao>https://www.seao.ca/OpportunityPublication/avis.aspx?ItemId=8524186d</hyperlienseao>
    <fournisseurs>
      <fournisseur>
        <nomorganisation>TISSEUR INC.</nomorganisation>
        <ville>Sainte-Adèle</ville>
        <province>QC</province>
        <pays>CAN</pays>
        <codepostal>J8B2J6</codepostal>
        <neq>1149222300</neq>
        <admissible />
        <conforme>1</conforme>
        <adjudicataire>1</adjudicataire>
        <montantsoumis>574250.840000</montantsoumis>
        <montantssoumisunite>1</montantssoumisunite>
        <montantcontrat>574250.840000</montantcontrat>
        <montanttotalcontrat>0.000000</montanttotalcontrat>
      </fournisseur>
      <fournisseur>
        <nomorganisation>LES ENTREPRISES LANDCO INC.</nomorganisation>
        <ville>Piedmont</ville>
        <neq>1142177600</neq>
        <conforme>1</conforme>
        <adjudicataire>0</adjudicataire>
        <montantsoumis>744504.800000</montantsoumis>
        <montantssoumisunite>1</montantssoumisunite>
        <montantcontrat>0.000000</montantcontrat>
      </fournisseur>
    </fournisseurs>
  </avis>
</export>
"""

CONTRATS = """<?xml version="1.0" encoding="utf-8"?>
<export datedebut="2024-05-01" datefin="2024-05-31">
  <contrat>
    <numeroseao>304462</numeroseao>
    <numero>EA-0902-02</numero>
    <datefinale>2010-08-10</datefinale>
    <datepublicationfinale>2024-05-16</datepublicationfinale>
    <montantfinal>566036.760000</montantfinal>
    <nomcontractant>TISSEUR INC.</nomcontractant>
    <neqcontractant>1149222300</neqcontractant>
  </contrat>
</export>
"""

DEPENSES = """<?xml version="1.0" encoding="utf-8"?>
<export datedebut="2024-05-01" datefin="2024-05-31">
  <avis>
    <numeroseao>887070</numeroseao>
    <numero>8801-14-0304</numero>
    <depenses>
      <depense>
        <datedepense>2024-04-26</datedepense>
        <datepublicationdepense>2024-05-02</datepublicationdepense>
        <montantdepense>197702.180000</montantdepense>
        <description>Dépense supplémentaire excédant 10 % du montant initial</description>
        <nomcontractant>BRUNEAU ELECTRIQUE INC.</nomcontractant>
        <neqcontractant>1142851337</neqcontractant>
      </depense>
    </depenses>
  </avis>
</export>
"""


class TestAwards:
    def test_emits_a_row_per_bidder(self):
        assert len(seao_xml.parse(AVIS)) == 2

    def test_only_the_adjudicataire_is_a_winner(self):
        """<fournisseurs> lists every bidder; treating all as awards overstates
        spending by roughly 80% on real data."""
        awards = seao_xml.parse(AVIS)
        winners = [a for a in awards if a.is_winner]
        assert len(winners) == 1
        assert winners[0].supplier_name == "TISSEUR INC."
        loser = next(a for a in awards if not a.is_winner)
        assert loser.supplier_name == "LES ENTREPRISES LANDCO INC."

    def test_zero_montanttotalcontrat_does_not_win_over_real_amount(self):
        """SEAO writes 0.000000 for 'not applicable' and it sits first in
        preference order — honouring it would zero out the real figure."""
        winner = next(a for a in seao_xml.parse(AVIS) if a.is_winner)
        assert winner.amount == 574250.84

    def test_notice_fields(self):
        award = seao_xml.parse(AVIS)[0]
        assert award.notice_number == "304462"
        assert award.buyer_reference == "EA-0902-02"
        assert award.buyer_name == "Municipalité de Saint-Adolphe-d'Howard."
        assert award.is_municipal is True
        assert award.title == "Caserne de pompier (route 329)"
        assert award.unspsc_code == "72131600"
        assert award.seao_category == "C03 - Autres travaux de construction"
        assert award.seao_url.startswith("https://www.seao.ca/")

    def test_code_tables_are_labelled(self):
        award = seao_xml.parse(AVIS)[0]
        assert award.notice_type_code == "3"
        assert award.notice_type_label == "Contrat adjugé suite à un appel d'offres public"
        assert award.nature_code == "3"
        assert award.nature_label == "Travaux de construction"
        assert award.delivery_region_code == "15"
        assert award.delivery_region_label == "Montérégie"

    def test_dates_with_time_component(self):
        award = seao_xml.parse(AVIS)[0]
        assert award.publication_date == date(2009, 6, 1)
        assert award.closing_date == date(2009, 6, 25)
        assert award.award_date == date(2009, 7, 21)

    def test_supplier_fields(self):
        winner = next(a for a in seao_xml.parse(AVIS) if a.is_winner)
        assert winner.supplier_neq == "1149222300"
        assert winner.supplier_city == "Sainte-Adèle"
        assert winner.supplier_country == "CAN"
        assert winner.supplier_postal_code == "J8B2J6"

    def test_bidder_count_derived_from_supplier_elements(self):
        """There is no bidder-count field; it comes from counting <fournisseur>."""
        assert all(a.number_of_bidders == 2 for a in seao_xml.parse(AVIS))

    def test_empty_flag_is_none_not_false(self):
        """<admissible /> means 'not disclosed', which differs from 0."""
        winner = next(a for a in seao_xml.parse(AVIS) if a.is_winner)
        assert winner.is_eligible is None
        assert winner.is_compliant is True

    def test_dedupe_keys_distinguish_suppliers(self):
        assert len({a.key() for a in seao_xml.parse(AVIS)}) == 2

    def test_provenance(self):
        award = seao_xml.parse(AVIS, content_hash="cafe")[0]
        assert award.source_format == "seao-xml"
        assert award.source_content_hash == "cafe"


class TestAmountUnits:
    def _with_unit(self, code):
        return seao_xml.parse(
            AVIS.replace(
                "<montantssoumisunite>1</montantssoumisunite>",
                f"<montantssoumisunite>{code}</montantssoumisunite>",
                1,
            )
        )[0]

    def test_dollars_are_summable(self):
        assert self._with_unit("1").is_summable is True

    def test_undocumented_zero_treated_as_dollars(self):
        """Code 0 is absent from the spec but is the most common value, and
        matches the published final amounts at a median ratio of 1.000."""
        award = self._with_unit("0")
        assert award.is_summable is True
        assert award.amount_unit_label == "$ (non précisé)"

    def test_percent_and_points_are_not_summable(self):
        assert self._with_unit("8").is_summable is False
        assert self._with_unit("8").amount_unit_label == "%"
        assert self._with_unit("11").is_summable is False

    def test_us_dollars_excluded_without_a_conversion_rate(self):
        assert self._with_unit("7").is_summable is False

    def test_rate_units_are_not_summable(self):
        for code in ("2", "3", "10", "12"):
            assert self._with_unit(code).is_summable is False, code


class TestFinalsAndExpenses:
    def test_contrats_file_yields_finals(self):
        awards, finals, expenses = seao_xml.parse_all(CONTRATS)
        assert (len(awards), len(finals), len(expenses)) == (0, 1, 0)
        final = finals[0]
        assert final.notice_number == "304462"
        assert final.final_amount == 566036.76
        assert final.final_date == date(2010, 8, 10)
        assert final.supplier_neq == "1149222300"

    def test_final_differs_from_awarded(self):
        """The variance between the two is the reason to ingest both."""
        awarded = next(a for a in seao_xml.parse(AVIS) if a.is_winner).amount
        final = seao_xml.parse_all(CONTRATS)[1][0].final_amount
        assert awarded != final

    def test_depenses_file_yields_expenses(self):
        awards, finals, expenses = seao_xml.parse_all(DEPENSES)
        assert (len(awards), len(finals), len(expenses)) == (0, 0, 1)
        expense = expenses[0]
        assert expense.notice_number == "887070"
        assert expense.amount == 197702.18
        assert expense.expense_date == date(2024, 4, 26)
        assert "excédant 10 %" in expense.description

    def test_depenses_avis_not_mistaken_for_an_award(self):
        """Both files use <avis>; routing is by content, not filename."""
        awards, _, _ = seao_xml.parse_all(DEPENSES)
        assert awards == []


class TestRobustness:
    def test_streaming_is_lazy(self):
        assert next(seao_xml.iter_parse(AVIS)).notice_number == "304462"

    def test_notice_without_suppliers_still_emitted(self):
        xml = "<export><avis><numeroseao>1</numeroseao></avis></export>"
        awards = seao_xml.parse(xml)
        assert len(awards) == 1
        assert awards[0].supplier_name is None
        assert awards[0].number_of_bidders is None

    def test_malformed_xml_recovers(self):
        assert len(seao_xml.parse(AVIS.replace("</titre>", ""))) >= 1

    def test_empty_input(self):
        assert seao_xml.parse("") == []
        assert seao_xml.parse(b"   ") == []

    def test_namespaced_document(self):
        namespaced = AVIS.replace("<export ", '<export xmlns="http://seao.qc.ca/ns" ', 1)
        assert len(seao_xml.parse(namespaced)) == 2

    def test_unknown_tags_retained(self):
        xml = """<export><avis><numeroseao>1</numeroseao>
                 <champinconnu>surprise</champinconnu></avis></export>"""
        assert seao_xml.parse(xml)[0].unmapped.get("champinconnu") == "surprise"

    def test_documented_fields_not_flagged_unmapped(self):
        assert seao_xml.parse(AVIS)[0].unmapped == {}


class TestInspector:
    def test_reports_record_tags(self):
        report = seao_xml.inspect_structure(AVIS)
        assert report["root"] == "export"
        assert "avis" in report["record_tags_present"]

    def test_flags_unknown_tags(self):
        xml = """<export><avis><numeroseao>1</numeroseao>
                 <champmysterieux>x</champmysterieux></avis></export>"""
        report = seao_xml.inspect_structure(xml)
        assert "champmysterieux" in report["unmapped_leaf_tags"]
        assert "numeroseao" not in report["unmapped_leaf_tags"]
