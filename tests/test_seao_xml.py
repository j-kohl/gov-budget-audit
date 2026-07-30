"""Tests for the legacy SEAO XML parser.

The fixtures below are synthetic. The real tag spellings are unverified — that
is exactly why the parser resolves fields through a candidate map and ships an
inspector. These tests pin the *mechanism*: tag normalization, supplier
nesting, amount preference, and streaming. They do not claim the tag names are
the ones SEAO actually uses.
"""

from datetime import date

from govbudget.parsers import seao_xml

# Accented tags, a wrapper element around suppliers, and mixed case — the three
# things that would break a naive parser.
SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<Avis>
  <Avis>
    <NuméroSEAO>1234567</NuméroSEAO>
    <Titre>Réfection de la route 132</Titre>
    <Organisme>Ministère des Transports</Organisme>
    <CategorieSeao>Travaux de construction</CategorieSeao>
    <TypeAvis>Appel d'offres public</TypeAvis>
    <DatePublication>2019-06-01</DatePublication>
    <DateFinale>2019-07-15</DateFinale>
    <NombreSoumissions>4</NombreSoumissions>
    <Fournisseurs>
      <Fournisseur>
        <NomOrganisation>Construction Tremblay inc.</NomOrganisation>
        <NEQ>1140000000</NEQ>
        <Ville>Québec</Ville>
        <MontantContrat>2 500 000,50</MontantContrat>
      </Fournisseur>
      <Fournisseur>
        <NomOrganisation>Excavation Bélanger ltée</NomOrganisation>
        <NEQ>1140000001</NEQ>
        <Ville>Lévis</Ville>
        <MontantContrat>1 250 000,00</MontantContrat>
      </Fournisseur>
    </Fournisseurs>
  </Avis>
  <Avis>
    <NuméroSEAO>7654321</NuméroSEAO>
    <Titre>Fourniture de papeterie</Titre>
    <Organisme>Ville de Montréal</Organisme>
    <DatePublication>2019-08-01</DatePublication>
    <Fournisseurs>
      <Fournisseur>
        <NomOrganisation>Papeterie du Nord</NomOrganisation>
        <MontantContrat>15 000,00</MontantContrat>
      </Fournisseur>
    </Fournisseurs>
  </Avis>
</Avis>
"""


class TestParsing:
    def test_yields_one_row_per_supplier(self):
        awards = seao_xml.parse(SAMPLE, content_hash="cafe")
        assert len(awards) == 3

    def test_accented_tags_resolve(self):
        """NuméroSEAO must map through normalization to notice_number."""
        awards = seao_xml.parse(SAMPLE)
        assert awards[0].notice_number == "1234567"

    def test_record_fields_shared_across_suppliers(self):
        awards = seao_xml.parse(SAMPLE)
        first_notice = [a for a in awards if a.notice_number == "1234567"]
        assert len(first_notice) == 2
        assert all(a.buyer_name == "Ministère des Transports" for a in first_notice)
        assert all(a.title == "Réfection de la route 132" for a in first_notice)
        assert all(a.number_of_bidders == 4 for a in first_notice)

    def test_supplier_fields(self):
        awards = seao_xml.parse(SAMPLE)
        tremblay = next(a for a in awards if a.supplier_name == "Construction Tremblay inc.")
        assert tremblay.supplier_neq == "1140000000"
        assert tremblay.supplier_city == "Québec"

    def test_french_amounts(self):
        awards = seao_xml.parse(SAMPLE)
        amounts = sorted(a.amount for a in awards)
        assert amounts == [15000.0, 1250000.0, 2500000.5]

    def test_dates(self):
        award = seao_xml.parse(SAMPLE)[0]
        assert award.publication_date == date(2019, 6, 1)
        assert award.award_date == date(2019, 7, 15)

    def test_provenance(self):
        award = seao_xml.parse(SAMPLE, content_hash="cafe")[0]
        assert award.source_format == "seao-xml"
        assert award.source_content_hash == "cafe"
        assert award.source_id == "seao"

    def test_dedupe_keys_distinguish_suppliers(self):
        awards = seao_xml.parse(SAMPLE)
        assert len({a.key() for a in awards}) == 3


class TestRobustness:
    def test_namespaced_document(self):
        namespaced = SAMPLE.replace("<Avis>", '<Avis xmlns="http://example.qc.ca/seao">', 1)
        awards = seao_xml.parse(namespaced)
        assert len(awards) == 3
        assert awards[0].notice_number == "1234567"

    def test_flat_suppliers_without_wrapper(self):
        """Some years nest <Fournisseur> directly under the record."""
        flat = SAMPLE.replace("<Fournisseurs>", "").replace("</Fournisseurs>", "")
        awards = seao_xml.parse(flat)
        assert len(awards) == 3

    def test_notice_without_supplier_still_emitted(self):
        xml = """<Avis><Avis>
            <NumeroSeao>999</NumeroSeao>
            <Organisme>Test</Organisme>
            <MontantContrat>100,00</MontantContrat>
        </Avis></Avis>"""
        awards = seao_xml.parse(xml)
        assert len(awards) == 1
        assert awards[0].amount == 100.0
        assert awards[0].supplier_name is None

    def test_malformed_xml_recovers(self):
        """lxml recover mode should salvage what it can rather than raise."""
        broken = SAMPLE.replace("</Titre>", "")
        awards = seao_xml.parse(broken)
        assert len(awards) >= 1

    def test_empty_input(self):
        assert seao_xml.parse("") == []
        assert seao_xml.parse(b"   ") == []

    def test_unmapped_tags_are_retained(self):
        xml = """<Avis><Avis>
            <NumeroSeao>1</NumeroSeao>
            <ChampInconnu>valeur surprise</ChampInconnu>
        </Avis></Avis>"""
        award = seao_xml.parse(xml)[0]
        assert award.unmapped.get("champinconnu") == "valeur surprise"

    def test_streaming_is_lazy(self):
        """iter_parse must not materialize the whole document first."""
        iterator = seao_xml.iter_parse(SAMPLE)
        first = next(iterator)
        assert first.notice_number == "1234567"


class TestAmountPreference:
    def test_final_amount_wins_over_contract_amount(self):
        xml = """<Avis><Avis>
            <NumeroSeao>1</NumeroSeao>
            <Fournisseur>
                <NomOrganisation>X</NomOrganisation>
                <MontantSoumis>100,00</MontantSoumis>
                <MontantContrat>200,00</MontantContrat>
                <MontantFinal>300,00</MontantFinal>
            </Fournisseur>
        </Avis></Avis>"""
        assert seao_xml.parse(xml)[0].amount == 300.0

    def test_falls_back_to_record_amount(self):
        xml = """<Avis><Avis>
            <NumeroSeao>1</NumeroSeao>
            <MontantContrat>500,00</MontantContrat>
            <Fournisseur><NomOrganisation>X</NomOrganisation></Fournisseur>
        </Avis></Avis>"""
        assert seao_xml.parse(xml)[0].amount == 500.0


class TestInspector:
    def test_reports_paths_and_record_tag(self):
        report = seao_xml.inspect_structure(SAMPLE)
        assert report["detected_record_tag"] == "avis"
        paths = {entry["path"] for entry in report["paths"]}
        assert any(p.endswith("numeroseao") for p in paths)

    def test_flags_unmapped_tags(self):
        xml = """<Avis><Avis>
            <NumeroSeao>1</NumeroSeao>
            <ChampMysterieux>x</ChampMysterieux>
        </Avis></Avis>"""
        report = seao_xml.inspect_structure(xml)
        assert "champmysterieux" in report["unmapped_leaf_tags"]
        assert "numeroseao" not in report["unmapped_leaf_tags"]

    def test_collects_samples(self):
        report = seao_xml.inspect_structure(SAMPLE)
        entry = next(e for e in report["paths"] if e["path"].endswith("organisme"))
        assert "Ministère des Transports" in entry["samples"]
