"""Tests for the CKAN client, resource classification, storage and staging."""

import json
from datetime import date

import httpx
import pytest

from govbudget.ckan import CkanClient, CkanError, Resource
from govbudget.models import ContractAward
from govbudget.sources import seao
from govbudget.staging import awards_to_frame, deduplicate, summarize, write_awards
from govbudget.storage import RawStore


def envelope(result):
    return {"help": "", "success": True, "result": result}


PACKAGE = {
    "id": "pkg-1",
    "name": "seao-contrats",
    "title": "SEAO — contrats",
    "notes": "Contrats publics du Québec",
    "resources": [
        {
            "id": "r1",
            "name": {"fr": "seao_2019.xml"},
            "url": "https://example.qc.ca/seao_2019.xml",
            "format": "XML",
        },
        {
            "id": "r2",
            "name": "hebdo_2024-05.json",
            "url": "https://example.qc.ca/hebdo_2024-05.json",
            "format": "JSON",
        },
    ],
}


class TestCkanClient:
    def _client(self, handler):
        transport = httpx.MockTransport(handler)
        return CkanClient("https://example.qc.ca/api/3/action", client=httpx.Client(transport=transport))

    def test_package_search_uses_get(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return httpx.Response(200, json=envelope({"results": [PACKAGE]}))

        with self._client(handler) as client:
            packages = client.package_search("SEAO")

        assert seen["method"] == "GET", "CKAN calls must be GET, not POST"
        assert "q=SEAO" in seen["url"]
        assert packages[0].name == "seao-contrats"
        assert len(packages[0].resources) == 2

    def test_resource_name_dict_is_flattened(self):
        """CKAN returns multilingual names as {lang: value} on some portals."""

        def handler(request):
            return httpx.Response(200, json=envelope(PACKAGE))

        with self._client(handler) as client:
            package = client.package_show("pkg-1")
        assert package.resources[0].name == "seao_2019.xml"

    def test_error_envelope_raises(self):
        def handler(request):
            return httpx.Response(200, json={"success": False, "result": None, "error": "boom"})

        with self._client(handler) as client, pytest.raises(CkanError, match="boom"):
            client.action("package_show", id="x")

    def test_non_json_response_raises(self):
        def handler(request):
            return httpx.Response(200, text="<html>maintenance</html>")

        with self._client(handler) as client, pytest.raises(CkanError, match="not JSON"):
            client.action("package_show", id="x")

    def test_http_error_propagates(self):
        def handler(request):
            return httpx.Response(503)

        with self._client(handler) as client, pytest.raises(httpx.HTTPStatusError):
            client.action("package_show", id="x")

    def test_resources_matching(self):
        def handler(request):
            return httpx.Response(200, json=envelope(PACKAGE))

        with self._client(handler) as client:
            package = client.package_show("pkg-1")
        assert len(package.resources_matching(formats={"xml"})) == 1
        assert len(package.resources_matching(name_contains="hebdo")) == 1


class TestClassification:
    """Names below are the real ones published on Données Québec."""

    def make(self, name, url, fmt):
        return seao.classify(Resource(id="x", name=name, url=url, format=fmt))

    def test_yearly_xml_archive(self):
        item = self.make("Année 2019", "https://x/download/annee2019.zip", "XML")
        assert item.era == "xml"
        assert item.cadence == "annuel"
        assert item.year == 2019

    def test_monthly_xml_named_in_french(self):
        """The monthly XML archives are named 'Mai 2024', not 'mensuel_...'."""
        item = self.make("Mai 2024", "https://x/download/mai-2024.zip", "XML")
        assert item.era == "xml"
        assert item.cadence == "mensuel"
        assert (item.year, item.month) == (2024, 5)

    def test_french_month_with_accent(self):
        item = self.make("Février 2021", "https://x/download/2021_fevrier.zip", "XML")
        assert item.cadence == "mensuel"
        assert (item.year, item.month) == (2021, 2)

    def test_weekly_json_is_ocds(self):
        item = self.make(
            "hebdo_20260720_20260726.json", "https://x/hebdo_20260720_20260726.json", "JSON"
        )
        assert item.era == "ocds"
        assert item.cadence == "hebdo"
        assert (item.year, item.month) == (2026, 7)

    def test_monthly_json(self):
        item = self.make(
            "mensuel_20221101_20221130.json", "https://x/mensuel_20221101_20221130.json", "JSON"
        )
        assert item.cadence == "mensuel"
        assert (item.year, item.month) == (2022, 11)

    def test_zip_bundle_of_json_routed_by_name(self):
        """Declared format is 'zip'; only the name says it holds JSON."""
        item = self.make(
            "JSON_Mensuel_20230101_20240430.zip",
            "https://x/json_mensuel_20230101_20240430.zip",
            "ZIP",
        )
        assert item.era == "ocds"

    def test_format_beats_period(self):
        """XML archives continue past the 2021 OCDS start; format decides."""
        item = self.make("Mars 2024", "https://x/download/mars-2024.zip", "XML")
        assert item.era == "xml"

    def test_documentation_is_not_data(self):
        """The dataset carries the format specs and an FAQ as PDFs."""
        for name in ("FAQ_SEAO", "Format XML pour les données ouvertes du SEAO"):
            item = self.make(name, "https://x/download/faqseao.pdf", "PDF")
            assert item.cadence == "doc"
            assert item.is_data is False

    def test_select_excludes_documentation(self):
        resources = [
            self.make("Année 2019", "https://x/annee2019.zip", "XML"),
            self.make("FAQ_SEAO", "https://x/faqseao.pdf", "PDF"),
        ]
        assert [r.name for r in seao.select(resources)] == ["Année 2019"]
        assert len(seao.select(resources, include_docs=True)) == 2

    def test_select_filters_and_sorts_newest_first(self):
        resources = [
            self.make("Année 2019", "https://x/annee2019.zip", "XML"),
            self.make("hebdo_20240501_20240507.json", "https://x/b.json", "JSON"),
            self.make("mensuel_20230101_20230131.json", "https://x/c.json", "JSON"),
        ]
        ocds_only = seao.select(resources, era="ocds")
        assert [r.year for r in ocds_only] == [2024, 2023]
        assert len(seao.select(resources, limit=1)) == 1
        assert len(seao.select(resources, year=2019)) == 1


class TestRevisionsHandling:
    def test_revisions_members_skipped_by_default(self, tmp_path):
        """Revisions restate records already in the main files."""
        import io
        import zipfile

        from govbudget.storage import RawFile

        avis = b"<export><avis><numeroseao>1</numeroseao></avis></export>"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Avis_20240501_20240531.xml", avis)
            archive.writestr("AvisRevisions_20240501_20240531.xml", avis)

        entry = RawFile(
            source_id="seao", url="https://x/f.zip", content_hash="h",
            size_bytes=1, fetched_at="", relative_path="seao/h.zip",
        )
        awards, _, _ = seao.parse_raw(entry, buffer.getvalue(), "xml")
        assert len(awards) == 1

        awards, _, _ = seao.parse_raw(entry, buffer.getvalue(), "xml", include_revisions=True)
        assert len(awards) == 2


class TestZipHandling:
    def test_zip_members_are_expanded(self, tmp_path):
        import io
        import zipfile

        release = {
            "releases": [
                {
                    "ocid": "o-1",
                    "id": "r-1",
                    "buyer": {"name": "Ville"},
                    "awards": [
                        {
                            "id": "a-1",
                            "value": {"amount": 10.0, "currency": "CAD"},
                            "suppliers": [{"name": "S"}],
                        }
                    ],
                }
            ]
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("inner.json", json.dumps(release))

        members = list(seao._iter_members("https://x/f.zip", buffer.getvalue()))
        assert [name for name, _ in members] == ["inner.json"]

    def test_non_zip_passes_through(self):
        members = list(seao._iter_members("https://x/f.xml", b"<Avis/>"))
        assert members == [("f.xml", b"<Avis/>")]


class TestStorage:
    def test_content_addressed_and_manifest_appended(self, tmp_path):
        payload = b"<Avis><Avis><NumeroSeao>1</NumeroSeao></Avis></Avis>"

        def handler(request):
            return httpx.Response(200, content=payload, headers={"ETag": '"v1"'})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with RawStore(tmp_path, client=client) as store:
            entry = store.fetch("https://x/seao_2019.xml", "seao")

            assert entry.size_bytes == len(payload)
            # POSIX separators regardless of platform, so a manifest written on
            # Windows stays readable on Linux.
            assert entry.relative_path.startswith("seao/")
            assert "\\" not in entry.relative_path
            assert entry.relative_path.endswith(".xml")
            assert store.read(entry) == payload
            assert len(store.manifest("seao")) == 1

    def test_304_avoids_redownload(self, tmp_path):
        calls = {"n": 0}
        payload = b"data"

        def handler(request):
            calls["n"] += 1
            if request.headers.get("If-None-Match") == '"v1"':
                return httpx.Response(304)
            return httpx.Response(200, content=payload, headers={"ETag": '"v1"'})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with RawStore(tmp_path, client=client) as store:
            first = store.fetch("https://x/f.xml", "seao")
            second = store.fetch("https://x/f.xml", "seao")

        assert calls["n"] == 2
        assert first.from_cache is False
        assert second.from_cache is True
        assert second.content_hash == first.content_hash

    def test_identical_bytes_stored_once(self, tmp_path):
        def handler(request):
            return httpx.Response(200, content=b"same")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with RawStore(tmp_path, client=client) as store:
            a = store.fetch("https://x/one.xml", "seao")
            b = store.fetch("https://x/two.xml", "seao")

        assert a.content_hash == b.content_hash
        stored = list((tmp_path / "raw" / "seao").rglob("*.xml"))
        assert len(stored) == 1, "same bytes should not be stored twice"


def make_award(**overrides) -> ContractAward:
    base = dict(
        source_id="seao",
        source_content_hash="h",
        source_format="ocds-json",
        notice_number="1",
        award_id="a1",
        supplier_name="Supplier A",
        buyer_name="Ministère X",
        amount=100.0,
        award_date=date(2024, 1, 1),
        publication_date=date(2024, 1, 2),
    )
    base.update(overrides)
    return ContractAward(**base)


class TestStaging:
    def test_frame_has_stable_schema_when_empty(self):
        frame = awards_to_frame([])
        assert frame.height == 0
        assert "dedupe_key" in frame.columns

    def test_all_null_column_keeps_declared_type(self):
        """Without an explicit schema this infers as Null and breaks concat."""
        frame = awards_to_frame([make_award(ocid=None)])
        assert frame.schema["ocid"] == __import__("polars").Utf8

    def test_unmapped_serialized_to_json(self):
        frame = awards_to_frame([make_award(unmapped={"a": "b"})])
        assert json.loads(frame["unmapped"][0]) == {"a": "b"}

    def test_parquet_roundtrip(self, tmp_path):
        import polars as pl

        path = write_awards(
            [make_award()], source_id="seao", content_hash="abc", staging_dir=tmp_path
        )
        assert path is not None and path.exists()
        assert pl.read_parquet(path).height == 1

    def test_empty_write_returns_none(self, tmp_path):
        assert write_awards([], source_id="seao", content_hash="abc", staging_dir=tmp_path) is None

    def test_deduplicate_keeps_latest_publication(self):
        stale = make_award(amount=100.0, publication_date=date(2024, 1, 1))
        fresh = make_award(amount=250.0, publication_date=date(2024, 6, 1))
        frame = awards_to_frame([stale, fresh])
        deduped = deduplicate(frame)
        assert deduped.height == 1
        assert deduped["amount"][0] == 250.0

    def test_deduplicate_keeps_distinct_suppliers(self):
        frame = awards_to_frame(
            [make_award(supplier_name="A"), make_award(supplier_name="B")]
        )
        assert deduplicate(frame).height == 2

    def test_summarize_counts_only_winning_bids(self):
        frame = awards_to_frame(
            [
                make_award(supplier_name="A", amount=100.0, is_winner=True),
                make_award(supplier_name="B", amount=50.0, is_winner=True),
                make_award(supplier_name="C", amount=999.0, is_winner=False),
            ]
        )
        stats = summarize(frame)
        assert stats["bids"] == 3
        assert stats["winning_bids"] == 2
        assert stats["awarded_total_cad"] == 150.0
        assert stats["distinct_suppliers"] == 2

    def test_summarize_excludes_non_dollar_units(self):
        """A percentage or a points score must not be added to a dollar total."""
        frame = awards_to_frame(
            [
                make_award(supplier_name="A", amount=100.0, is_winner=True,
                           amount_unit_code="1"),
                make_award(supplier_name="B", amount=95.0, is_winner=True,
                           amount_unit_code="8", amount_unit_label="%"),
            ]
        )
        stats = summarize(frame)
        assert stats["awarded_total_cad"] == 100.0
        assert stats["awarded_rows_counted"] == 1
        assert stats["excluded_non_dollar_rows"] == 1
        assert stats["excluded_units"] == {"%": 1}

    def test_is_summable_materialized_into_the_frame(self):
        frame = awards_to_frame(
            [make_award(amount=1.0, amount_unit_code="11", amount_unit_label="points")]
        )
        assert frame["is_summable"][0] is False

    def test_summarize_empty(self):
        assert summarize(awards_to_frame([])) == {"rows": 0}


class TestOtherDatasets:
    def test_finals_roundtrip(self, tmp_path):
        import polars as pl

        from govbudget.models import ContractFinal
        from govbudget.staging import write_records

        final = ContractFinal(
            source_id="seao", source_content_hash="h", source_format="seao-xml",
            notice_number="304462", final_amount=566036.76, final_date=date(2010, 8, 10),
            supplier_name="TISSEUR INC.", supplier_neq="1149222300",
        )
        path = write_records(
            [final], dataset="finals", source_id="seao", content_hash="h", staging_dir=tmp_path
        )
        assert pl.read_parquet(path)["final_amount"][0] == 566036.76

    def test_expenses_roundtrip(self, tmp_path):
        import polars as pl

        from govbudget.models import ContractExpense
        from govbudget.staging import write_records

        expense = ContractExpense(
            source_id="seao", source_content_hash="h", source_format="seao-xml",
            notice_number="887070", amount=197702.18, expense_date=date(2024, 4, 26),
            description="Dépense supplémentaire", supplier_name="BRUNEAU ELECTRIQUE INC.",
        )
        path = write_records(
            [expense], dataset="expenses", source_id="seao", content_hash="h",
            staging_dir=tmp_path,
        )
        assert pl.read_parquet(path)["amount"][0] == 197702.18
