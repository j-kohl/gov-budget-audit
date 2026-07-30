"""Tests for the OCDS parser.

The fixture reproduces a real SEAO release, including the three publisher
conventions that a generic OCDS reader gets wrong: the notice number living in
the OCID, the NEQ living in `parties[].details`, and `contracts[].value` being
the settled amount rather than a duplicate of the award value.
"""

import json
from datetime import date

from govbudget.parsers import ocds

RELEASE_PACKAGE = {
    "uri": "https://donneesquebec.ca/seao/mensuel_20260601_20260630.json",
    "version": "1.1",
    "publishedDate": "2026-07-01T00:00:00-04:00",
    "publisher": {"name": "SEAO"},
    "releases": [
        {
            "ocid": "ocds-ec9k95-1740136",
            "id": "20260602192354",
            "date": "2026-06-02T14:05:54-04:00",
            "tag": ["contract"],
            "initiationType": "tender",
            "parties": [
                {
                    "name": "Ville de Montmagny",
                    "id": "OP-81796",
                    "address": {"locality": "Montmagny", "region": "QC", "countryName": "CAN"},
                    "roles": ["buyer"],
                    "details": {"municipal": "1"},
                },
                {
                    "name": "FNX-INNOV INC.",
                    "id": "FO-1174002437",
                    "address": {
                        "locality": "Longueuil",
                        "region": "QC",
                        "postalCode": "J4G2J4",
                        "countryName": "CAN",
                    },
                    "roles": ["supplier"],
                    "details": {"neq": "1174002437"},
                },
            ],
            "buyer": {"name": "Ville de Montmagny", "id": "OP-81796"},
            "tender": {
                "id": "VM-2022-02(G)",
                "title": "Étude géotechnique - Réfection du boulevard Taché Est",
                "status": "complete",
                "items": [
                    {
                        "id": "S8",
                        "description": "S8 - Contrôle de la qualité, essais et inspections",
                        "classification": {"scheme": "UNSPSC", "id": "81141503"},
                    }
                ],
                "procurementMethod": "direct",
                "procurementMethodDetails": "Contrat de gré à gré",
                "mainProcurementCategory": "services",
            },
            "awards": [
                {
                    "id": "20145160",
                    "status": "active",
                    "date": "2022-08-23T00:00:00-04:00",
                    "value": {"amount": 29709.54, "currency": "CAD"},
                    "suppliers": [{"name": "FNX-INNOV INC.", "id": "FO-1174002437"}],
                }
            ],
            "contracts": [
                {
                    "id": "20145160",
                    "awardID": "20145160",
                    "status": "terminated",
                    "period": {"endDate": "2022-08-24T00:00:00-04:00"},
                    "value": {"amount": 26738.59, "currency": "CAD"},
                    "dateSigned": "2022-08-23T00:00:00-04:00",
                }
            ],
        }
    ],
}


class TestPublisherConventions:
    def test_notice_number_comes_from_the_ocid(self):
        """`tender.id` is the buyer's own reference, not the SEAO number.
        Getting this wrong breaks the join to the XML-era records."""
        award = ocds.parse(RELEASE_PACKAGE)[0]
        assert award.notice_number == "1740136"
        assert award.buyer_reference == "VM-2022-02(G)"

    def test_neq_read_from_party_details(self):
        """SEAO puts the NEQ in `details.neq`, not the standard `identifier`."""
        assert ocds.parse(RELEASE_PACKAGE)[0].supplier_neq == "1174002437"

    def test_award_and_contract_values_kept_apart(self):
        awards, finals = ocds.parse_all(RELEASE_PACKAGE)
        assert awards[0].amount == 29709.54
        assert finals[0].final_amount == 26738.59

    def test_every_award_row_is_a_winner(self):
        """OCDS publishes no losing bids, so a shared is_winner filter needs
        this set or the OCDS era vanishes from every total."""
        award = ocds.parse(RELEASE_PACKAGE)[0]
        assert award.is_winner is True
        assert award.is_summable is True


class TestFieldExtraction:
    def test_buyer(self):
        award = ocds.parse(RELEASE_PACKAGE)[0]
        assert award.buyer_name == "Ville de Montmagny"
        assert award.buyer_city == "Montmagny"
        assert award.is_municipal is True

    def test_supplier_address(self):
        award = ocds.parse(RELEASE_PACKAGE)[0]
        assert award.supplier_name == "FNX-INNOV INC."
        assert award.supplier_city == "Longueuil"
        assert award.supplier_country == "CAN"
        assert award.supplier_postal_code == "J4G2J4"

    def test_procurement_detail_preferred_over_code(self):
        award = ocds.parse(RELEASE_PACKAGE)[0]
        assert award.procurement_method == "Contrat de gré à gré"
        assert award.procurement_category == "services"
        assert award.unspsc_code == "81141503"
        assert award.seao_category.startswith("S8 -")

    def test_dates_with_offset(self):
        award = ocds.parse(RELEASE_PACKAGE)[0]
        assert award.award_date == date(2022, 8, 23)
        assert award.publication_date == date(2026, 6, 2)

    def test_provenance(self):
        award = ocds.parse(RELEASE_PACKAGE, content_hash="beef")[0]
        assert award.source_format == "ocds-json"
        assert award.source_content_hash == "beef"


class TestMultipleSuppliers:
    def test_one_row_per_supplier(self):
        package = json.loads(json.dumps(RELEASE_PACKAGE))
        package["releases"][0]["awards"][0]["suppliers"] = [
            {"id": "SUP-1", "name": "Alpha inc."},
            {"id": "SUP-2", "name": "Beta ltée"},
        ]
        awards = ocds.parse(package)
        assert len(awards) == 2
        assert len({a.key() for a in awards}) == 2


class TestContainerShapes:
    def test_record_package(self):
        package = {"records": [{"ocid": "x", "compiledRelease": RELEASE_PACKAGE["releases"][0]}]}
        assert len(ocds.parse(package)) == 1

    def test_bare_release(self):
        assert len(ocds.parse(RELEASE_PACKAGE["releases"][0])) == 1

    def test_list_of_packages(self):
        assert len(ocds.parse([RELEASE_PACKAGE, RELEASE_PACKAGE])) == 2

    def test_newline_delimited(self):
        text = "\n".join(json.dumps(r) for r in [RELEASE_PACKAGE["releases"][0]] * 3)
        assert len(ocds.parse(text)) == 3

    def test_accepts_bytes_and_bom(self):
        payload = b"\xef\xbb\xbf" + json.dumps(RELEASE_PACKAGE).encode("utf-8")
        assert len(ocds.parse(payload)) == 1

    def test_empty_input(self):
        assert ocds.parse(b"") == []
        assert ocds.parse("   ") == []


class TestEdgeCases:
    def test_tender_only_release_emits_no_award(self):
        package = {"releases": [{"ocid": "ocds-x-1", "id": "1", "tender": {"title": "t"}}]}
        assert ocds.parse(package) == []

    def test_buyer_falls_back_to_parties(self):
        package = json.loads(json.dumps(RELEASE_PACKAGE))
        del package["releases"][0]["buyer"]
        assert ocds.parse(package)[0].buyer_name == "Ville de Montmagny"

    def test_contract_without_value_produces_no_final(self):
        package = json.loads(json.dumps(RELEASE_PACKAGE))
        del package["releases"][0]["contracts"][0]["value"]
        _, finals = ocds.parse_all(package)
        assert finals == []

    def test_missing_suppliers_still_yields_award(self):
        package = json.loads(json.dumps(RELEASE_PACKAGE))
        del package["releases"][0]["awards"][0]["suppliers"]
        awards = ocds.parse(package)
        assert len(awards) == 1
        assert awards[0].supplier_name is None
        assert awards[0].amount == 29709.54

    def test_standard_identifier_still_read(self):
        """Non-SEAO OCDS publishers use the standard identifier object."""
        package = json.loads(json.dumps(RELEASE_PACKAGE))
        party = package["releases"][0]["parties"][1]
        del party["details"]
        party["identifier"] = {"scheme": "CA-QC-NEQ", "id": "9999999999"}
        assert ocds.parse(package)[0].supplier_neq == "9999999999"


class TestCompetitivenessHarmonization:
    """OCDS `procurementMethod` must land on the same canonical values as the
    XML <type> codes, or grouping splits one category at the 2021 boundary."""

    def _method(self, value):
        package = json.loads(json.dumps(RELEASE_PACKAGE))
        package["releases"][0]["tender"]["procurementMethod"] = value
        return ocds.parse(package)[0]

    def test_direct_matches_xml_gre_a_gre(self):
        award = self._method("direct")
        assert award.competitiveness == "direct"
        assert award.competitiveness_label == "Gré à gré"

    def test_open_matches_xml_public_tender(self):
        assert self._method("open").competitiveness == "open"

    def test_selective_is_limited(self):
        assert self._method("selective").competitiveness == "limited"

    def test_human_label_kept_separately(self):
        award = self._method("direct")
        assert award.notice_type_code == "direct"
        assert award.procurement_method == "Contrat de gré à gré"
