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
    def make(self, name, url, fmt):
        return seao.classify(Resource(id="x", name=name, url=url, format=fmt))

    def test_yearly_xml(self):
        item = self.make("seao_2019.xml", "https://x/seao_2019.xml", "XML")
        assert item.era == "xml"
        assert item.cadence == "annuel"
        assert item.year == 2019

    def test_weekly_json_is_ocds(self):
        item = self.make("hebdo_2024-05.json", "https://x/hebdo_2024-05.json", "JSON")
        assert item.era == "ocds"
        assert item.cadence == "hebdo"
        assert item.year == 2024
        assert item.month == 5

    def test_monthly(self):
        item = self.make("mensuel_2022-11.json", "https://x/mensuel_2022-11.json", "JSON")
        assert item.cadence == "mensuel"
        assert item.month == 11

    def test_format_beats_period(self):
        """A 2023 file published as XML is still XML-era."""
        item = self.make("seao_2023.xml", "https://x/seao_2023.xml", "XML")
        assert item.era == "xml"

    def test_unknown_format_uses_era_boundary(self):
        assert self.make("data_2015", "https://x/data_2015", "").era == "xml"
        assert self.make("data_2023", "https://x/data_2023", "").era == "ocds"

    def test_select_filters_and_sorts_newest_first(self):
        resources = [
            self.make("seao_2019.xml", "https://x/a.xml", "XML"),
            self.make("hebdo_2024-05.json", "https://x/b.json", "JSON"),
            self.make("mensuel_2023-01.json", "https://x/c.json", "JSON"),
        ]
        ocds_only = seao.select(resources, era="ocds")
        assert [r.year for r in ocds_only] == [2024, 2023]
        assert len(seao.select(resources, limit=1)) == 1
        assert len(seao.select(resources, year=2019)) == 1


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
            assert entry.relative_path.startswith("seao/")
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

    def test_summarize(self):
        frame = awards_to_frame([make_award(), make_award(supplier_name="B", amount=50.0)])
        stats = summarize(frame)
        assert stats["rows"] == 2
        assert stats["total_value"] == 150.0
        assert stats["distinct_suppliers"] == 2

    def test_summarize_empty(self):
        assert summarize(awards_to_frame([])) == {"rows": 0}
