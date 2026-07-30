import json
from datetime import date

from govbudget.parsers import ocds

RELEASE_PACKAGE = {
    "uri": "https://example.qc.ca/ocds/2021-03.json",
    "publishedDate": "2021-03-31T00:00:00Z",
    "publisher": {"name": "SEAO"},
    "releases": [
        {
            "ocid": "ocds-abc123-2021-0001",
            "id": "2021-0001-award-01",
            "date": "2021-03-15T12:00:00Z",
            "tag": ["award"],
            "parties": [
                {
                    "id": "ORG-1",
                    "name": "Ministère des Transports",
                    "roles": ["buyer"],
                },
                {
                    "id": "SUP-1",
                    "name": "Construction Tremblay inc.",
                    "roles": ["supplier"],
                    "identifier": {"scheme": "CA-QC-NEQ", "id": "1140000000"},
                    "address": {"locality": "Québec", "region": "QC"},
                },
            ],
            "buyer": {"id": "ORG-1", "name": "Ministère des Transports"},
            "tender": {
                "id": "1234567",
                "title": "Réfection de la route 132",
                "procurementMethod": "open",
                "mainProcurementCategory": "works",
                "numberOfTenderers": 4,
                "items": [{"classification": {"scheme": "UNSPSC", "id": "72141100"}}],
            },
            "awards": [
                {
                    "id": "AWD-1",
                    "title": "Réfection de la route 132",
                    "date": "2021-03-10T00:00:00Z",
                    "status": "active",
                    "value": {"amount": 2500000.5, "currency": "CAD"},
                    "suppliers": [{"id": "SUP-1", "name": "Construction Tremblay inc."}],
                    "contractPeriod": {"startDate": "2021-04-01", "endDate": "2022-03-31"},
                }
            ],
        }
    ],
}


class TestParseReleasePackage:
    def test_extracts_core_fields(self):
        awards = ocds.parse(RELEASE_PACKAGE, content_hash="deadbeef")
        assert len(awards) == 1
        award = awards[0]

        assert award.ocid == "ocds-abc123-2021-0001"
        assert award.notice_number == "1234567"
        assert award.award_id == "AWD-1"
        assert award.buyer_name == "Ministère des Transports"
        assert award.title == "Réfection de la route 132"
        assert award.amount == 2500000.5
        assert award.currency == "CAD"
        assert award.supplier_name == "Construction Tremblay inc."
        assert award.supplier_neq == "1140000000"
        assert award.supplier_city == "Québec"
        assert award.number_of_bidders == 4
        assert award.unspsc_code == "72141100"
        assert award.procurement_method == "open"

    def test_dates(self):
        award = ocds.parse(RELEASE_PACKAGE)[0]
        assert award.award_date == date(2021, 3, 10)
        assert award.publication_date == date(2021, 3, 15)
        assert award.contract_start == date(2021, 4, 1)
        assert award.contract_end == date(2022, 3, 31)

    def test_provenance_recorded(self):
        award = ocds.parse(RELEASE_PACKAGE, content_hash="deadbeef")[0]
        assert award.source_format == "ocds-json"
        assert award.source_content_hash == "deadbeef"

    def test_accepts_bytes_and_str(self):
        as_bytes = json.dumps(RELEASE_PACKAGE).encode("utf-8")
        assert len(ocds.parse(as_bytes)) == 1
        assert len(ocds.parse(json.dumps(RELEASE_PACKAGE))) == 1

    def test_handles_utf8_bom(self):
        """Government JSON exports frequently carry a BOM."""
        payload = b"\xef\xbb\xbf" + json.dumps(RELEASE_PACKAGE).encode("utf-8")
        assert len(ocds.parse(payload)) == 1


class TestMultipleSuppliers:
    def test_one_row_per_supplier(self):
        package = json.loads(json.dumps(RELEASE_PACKAGE))
        package["releases"][0]["awards"][0]["suppliers"] = [
            {"id": "SUP-1", "name": "Alpha inc."},
            {"id": "SUP-2", "name": "Beta ltée"},
        ]
        awards = ocds.parse(package)
        assert len(awards) == 2
        assert {a.supplier_name for a in awards} == {"Alpha inc.", "Beta ltée"}

    def test_award_id_in_dedupe_key_prevents_collapse(self):
        package = json.loads(json.dumps(RELEASE_PACKAGE))
        package["releases"][0]["awards"][0]["suppliers"] = [
            {"id": "SUP-1", "name": "Alpha inc."},
            {"id": "SUP-2", "name": "Beta ltée"},
        ]
        awards = ocds.parse(package)
        assert len({a.key() for a in awards}) == 2


class TestContainerShapes:
    def test_record_package(self):
        record_package = {
            "records": [{"ocid": "x", "compiledRelease": RELEASE_PACKAGE["releases"][0]}]
        }
        assert len(ocds.parse(record_package)) == 1

    def test_bare_release(self):
        assert len(ocds.parse(RELEASE_PACKAGE["releases"][0])) == 1

    def test_list_of_packages(self):
        assert len(ocds.parse([RELEASE_PACKAGE, RELEASE_PACKAGE])) == 2

    def test_newline_delimited(self):
        text = "\n".join(json.dumps(r) for r in [RELEASE_PACKAGE["releases"][0]] * 3)
        assert len(ocds.parse(text)) == 3

    def test_empty_input(self):
        assert ocds.parse(b"") == []
        assert ocds.parse("   ") == []


class TestEdgeCases:
    def test_tender_only_release_emits_nothing(self):
        """A tender with no award must not become a $0 award row."""
        package = {"releases": [{"ocid": "x", "id": "1", "tender": {"title": "t"}}]}
        assert ocds.parse(package) == []

    def test_buyer_falls_back_to_parties(self):
        package = json.loads(json.dumps(RELEASE_PACKAGE))
        del package["releases"][0]["buyer"]
        award = ocds.parse(package)[0]
        assert award.buyer_name == "Ministère des Transports"

    def test_value_falls_back_to_contract(self):
        package = json.loads(json.dumps(RELEASE_PACKAGE))
        del package["releases"][0]["awards"][0]["value"]
        package["releases"][0]["contracts"] = [
            {"id": "C-1", "awardID": "AWD-1", "value": {"amount": 999.0, "currency": "CAD"}}
        ]
        assert ocds.parse(package)[0].amount == 999.0

    def test_missing_suppliers_still_yields_award(self):
        package = json.loads(json.dumps(RELEASE_PACKAGE))
        del package["releases"][0]["awards"][0]["suppliers"]
        awards = ocds.parse(package)
        assert len(awards) == 1
        assert awards[0].supplier_name is None
        assert awards[0].amount == 2500000.5
