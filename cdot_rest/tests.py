import gzip
import io
import json
from unittest import mock

import fakeredis
from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase
from django.urls import reverse


def _make_transcript(accession):
    """ Minimal transcript payload - shape doesn't matter for these tests, identity does """
    return {"id": accession, "gene_name": "BRCA2", "cdot_data_version": "0.2.26"}


class ApiDocsTests(SimpleTestCase):
    """ Docs are flat files served by the web server, but the spec is hand-edited YAML -
        guard against shipping one that won't parse or has a broken internal reference. """

    STATIC_DIR = settings.BASE_DIR / "cdot_rest" / "static"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import yaml
        cls.spec = yaml.safe_load((cls.STATIC_DIR / "openapi.yaml").read_text())

    def test_is_openapi_3_with_paths(self):
        self.assertTrue(self.spec["openapi"].startswith("3."))
        self.assertTrue(self.spec["paths"])

    def test_all_internal_refs_resolve(self):
        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "$ref":
                        target = self.spec
                        for part in value.lstrip("#/").split("/"):
                            self.assertIn(part, target, f"unresolved $ref: {value}")
                            target = target[part]
                    else:
                        walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)
        walk(self.spec)

    def test_docs_page_references_spec(self):
        docs = (self.STATIC_DIR / "api-docs.html").read_text()
        self.assertIn("/static/openapi.yaml", docs)


class TranscriptViewTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.redis = fakeredis.FakeStrictRedis()
        patcher = mock.patch("cdot_rest.views._get_redis", return_value=self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)

        # NM_000059 has 2 versions, NM_007294 has 1
        self.transcripts = {
            "NM_000059.3": _make_transcript("NM_000059.3"),
            "NM_000059.4": _make_transcript("NM_000059.4"),
            "NM_007294.3": _make_transcript("NM_007294.3"),
        }
        for accession, data in self.transcripts.items():
            self.redis.set(accession, json.dumps(data))
            versionless = accession.rsplit(".", 1)[0]
            self.redis.sadd(f"versions:{versionless}", accession)

    # --- single transcript (versioned) ---

    def test_versioned_hit(self):
        response = self.client.get(reverse("transcript", args=["NM_000059.3"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.transcripts["NM_000059.3"])

    def test_versioned_miss(self):
        response = self.client.get(reverse("transcript", args=["NM_000059.99"]))
        self.assertEqual(response.status_code, 404)

    # --- single transcript (versionless -> all versions) ---

    def test_versionless_returns_all_versions(self):
        response = self.client.get(reverse("transcript", args=["NM_000059"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "NM_000059.3": self.transcripts["NM_000059.3"],
            "NM_000059.4": self.transcripts["NM_000059.4"],
        })

    def test_versionless_miss(self):
        response = self.client.get(reverse("transcript", args=["NM_999999"]))
        self.assertEqual(response.status_code, 404)


class TranscriptTagsForGeneViewTests(SimpleTestCase):
    """ /transcripts/gene/<gene>/tags/<build> exposes get_tx_ac_tags_for_gene over HTTP so a
        RESTDataProvider can drive gene-symbol HGVS resolution in one round-trip (issue #12). """

    @staticmethod
    def _tx(accession, build, exons, tag=None):
        build_data = {"contig": "NC_000013.11", "strand": "+", "exons": exons}
        if tag is not None:
            build_data["tag"] = tag
        return {"id": accession, "gene_name": "BRCA2",
                "genome_builds": {build: build_data}}

    def setUp(self):
        cache.clear()
        self.redis = fakeredis.FakeStrictRedis()
        patcher = mock.patch("cdot_rest.views._get_redis", return_value=self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Longer transcript is MANE_Select; shorter has no tags. GRCh38 only.
        transcripts = {
            "NM_000059.4": self._tx("NM_000059.4", "GRCh38", [[100, 2100]], "MANE_Select,basic"),
            "NM_000059.3": self._tx("NM_000059.3", "GRCh38", [[100, 600]]),
        }
        for accession, data in transcripts.items():
            self.redis.set(accession, json.dumps(data))
            self.redis.sadd("transcripts:BRCA2", accession)

    def _get(self, gene, build):
        return self.client.get(reverse("transcripts_tags_for_gene", args=[gene, build]))

    def test_returns_tagged_pairs_longest_first(self):
        response = self._get("BRCA2", "GRCh38")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"results": [
            ["NM_000059.4", ["MANE_Select", "basic"]],
            ["NM_000059.3", []],
        ]})

    def test_unknown_build_returns_empty(self):
        response = self._get("BRCA2", "GRCh37")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"results": []})

    def test_unknown_gene_returns_empty(self):
        response = self._get("NOPE", "GRCh38")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"results": []})


class ManeTranscriptsForGeneViewTests(SimpleTestCase):
    """ /transcripts/gene/<gene>/mane/<build> filters to MANE transcripts server-side and returns
        their full records keyed by accession, so a client gets the answer in one call (issue #14).

        MANE Select is a matched RefSeq+Ensembl pair; tag spelling differs by consortium
        (RefSeq 'MANE Select' vs Ensembl 'MANE_Select'), which the view normalizes. """

    @staticmethod
    def _tx(accession, build, exons, tag=None):
        build_data = {"contig": "NC_000013.11", "strand": "+", "exons": exons}
        if tag is not None:
            build_data["tag"] = tag
        return {"id": accession, "gene_name": "BRCA2",
                "genome_builds": {build: build_data}}

    def setUp(self):
        cache.clear()
        self.redis = fakeredis.FakeStrictRedis()
        patcher = mock.patch("cdot_rest.views._get_redis", return_value=self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)

        # MANE Select is the matched pair NM_000059.4 (space spelling) + ENST...8 (underscore).
        # NM_058195.4 is MANE Plus Clinical (RefSeq). NM_000059.3 has only RefSeq Select - not MANE.
        self.transcripts = {
            "NM_000059.4": self._tx("NM_000059.4", "GRCh38", [[100, 2100]], "MANE Select"),
            "ENST00000380152.8": self._tx("ENST00000380152.8", "GRCh38", [[100, 2100]],
                                          "MANE_Select,Ensembl_canonical"),
            "NM_058195.4": self._tx("NM_058195.4", "GRCh38", [[100, 900]], "MANE Plus Clinical"),
            "NM_000059.3": self._tx("NM_000059.3", "GRCh38", [[100, 600]], "RefSeq Select"),
        }
        for accession, data in self.transcripts.items():
            self.redis.set(accession, json.dumps(data))
            self.redis.sadd("transcripts:BRCA2", accession)

    def _get(self, gene, build, **params):
        url = reverse("mane_transcripts_for_gene", args=[gene, build])
        return self.client.get(url, params)

    def test_no_consortium_returns_both_mane_select(self):
        response = self._get("BRCA2", "GRCh38")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "NM_000059.4": self.transcripts["NM_000059.4"],
            "ENST00000380152.8": self.transcripts["ENST00000380152.8"],
        })

    def test_refseq_consortium_returns_one(self):
        response = self._get("BRCA2", "GRCh38", annotation_consortium="RefSeq")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(),
                         {"NM_000059.4": self.transcripts["NM_000059.4"]})

    def test_ensembl_consortium_returns_one(self):
        response = self._get("BRCA2", "GRCh38", annotation_consortium="ensembl")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(),
                         {"ENST00000380152.8": self.transcripts["ENST00000380152.8"]})

    def test_plus_clinical_excluded_by_default(self):
        response = self._get("BRCA2", "GRCh38", annotation_consortium="RefSeq")
        self.assertNotIn("NM_058195.4", response.json())

    def test_plus_clinical_included_when_requested(self):
        response = self._get("BRCA2", "GRCh38", annotation_consortium="RefSeq",
                             plus_clinical="true")
        self.assertEqual(set(response.json()), {"NM_000059.4", "NM_058195.4"})

    def test_refseq_select_is_not_mane(self):
        # NM_000059.3 carries only 'RefSeq Select' - must not be returned by the MANE endpoint
        response = self._get("BRCA2", "GRCh38")
        self.assertNotIn("NM_000059.3", response.json())

    def test_unknown_gene_returns_empty(self):
        response = self._get("NOPE", "GRCh38")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {})

    def test_unknown_build_returns_empty(self):
        response = self._get("BRCA2", "GRCh37")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {})

    def test_bad_consortium_is_400(self):
        response = self._get("BRCA2", "GRCh38", annotation_consortium="banana")
        self.assertEqual(response.status_code, 400)


class ImportTranscriptsCommandTests(SimpleTestCase):
    """ The 'latest' loader pulls per-build files, so the same accession arrives multiple times
        (once per genome build) - genome_builds must merge, not overwrite (issue #11). """

    @staticmethod
    def _tx(accession, build):
        return {
            "id": accession,
            "gene_name": "BRCA2",
            "genome_builds": {build: {"contig": "1", "exons": [[100, 200]]}},
        }

    @classmethod
    def _gz(cls, transcripts):
        payload = {"transcripts": transcripts, "genes": {"BRCA2": {"gene_symbol": "BRCA2"}}}
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as f:
            f.write(json.dumps(payload).encode())
        buf.seek(0)
        return gzip.GzipFile(fileobj=buf)

    def setUp(self):
        from cdot_json.management.commands.import_transcript_json import Command
        self.command = Command()
        self.redis = fakeredis.FakeStrictRedis()

    def test_genome_builds_merge_across_per_build_files(self):
        # NM_000059.3 appears in both builds; NM_007294.3 only in GRCh38
        grch37 = self._gz({"NM_000059.3": self._tx("NM_000059.3", "GRCh37")})
        grch38 = self._gz({
            "NM_000059.3": self._tx("NM_000059.3", "GRCh38"),
            "NM_007294.3": self._tx("NM_007294.3", "GRCh38"),
        })

        self.command._insert_transcripts(self.redis, "0.2.32", "RefSeq", grch37)
        self.command._insert_transcripts(self.redis, "0.2.32", "RefSeq", grch38)

        merged = json.loads(self.redis.get("NM_000059.3"))
        self.assertEqual(set(merged["genome_builds"]), {"GRCh37", "GRCh38"})
        # Count is unique accessions across builds, not the sum of per-file rows
        self.assertEqual(int(self.redis.get("refseq_count")), 2)

    def test_reimport_is_idempotent_for_count(self):
        grch38 = self._gz({"NM_000059.3": self._tx("NM_000059.3", "GRCh38")})
        self.command._insert_transcripts(self.redis, "0.2.32", "RefSeq", grch38)
        grch38_again = self._gz({"NM_000059.3": self._tx("NM_000059.3", "GRCh38")})
        self.command._insert_transcripts(self.redis, "0.2.32", "RefSeq", grch38_again)
        self.assertEqual(int(self.redis.get("refseq_count")), 1)

    def test_store_release(self):
        self.command._store_release(self.redis, "0.2.32", {
            "html_url": "https://github.com/SACGF/cdot/releases/tag/data_v0.2.32"})
        self.assertEqual(self.redis.get("cdot_data_version").decode(), "0.2.32")
        self.assertEqual(self.redis.get("cdot_release_url").decode(),
                         "https://github.com/SACGF/cdot/releases/tag/data_v0.2.32")

    def test_store_release_without_url(self):
        self.redis.set("cdot_release_url", "stale")
        self.command._store_release(self.redis, "0.2.32", {})
        self.assertEqual(self.redis.get("cdot_data_version").decode(), "0.2.32")
        self.assertIsNone(self.redis.get("cdot_release_url"))


class IndexViewTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.redis = fakeredis.FakeStrictRedis()
        patcher = mock.patch("cdot_rest.views._get_redis", return_value=self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_release_version_displayed_with_link(self):
        self.redis.set("cdot_data_version", "0.2.27")
        self.redis.set("cdot_release_url",
                       "https://github.com/SACGF/cdot/releases/tag/data_v0.2.27")
        response = self.client.get(reverse("index"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("v0.2.27", content)
        self.assertIn("https://github.com/SACGF/cdot/releases/tag/data_v0.2.27", content)

    def test_release_version_without_url_shown_as_text(self):
        self.redis.set("cdot_data_version", "0.2.27")
        response = self.client.get(reverse("index"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("v0.2.27", content)
        self.assertNotIn("releases/tag", content)

    def test_no_release_no_version_section(self):
        response = self.client.get(reverse("index"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("cdot data release", response.content.decode())


class BatchTranscriptsViewTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.redis = fakeredis.FakeStrictRedis()
        patcher = mock.patch("cdot_rest.views._get_redis", return_value=self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)

        # NM_000059 has versions .2 and .10 (to exercise numeric, not lexical, ordering)
        self.transcripts = {
            "NM_000059.2": _make_transcript("NM_000059.2"),
            "NM_000059.10": _make_transcript("NM_000059.10"),
            "NM_007294.3": _make_transcript("NM_007294.3"),
        }
        for accession, data in self.transcripts.items():
            self.redis.set(accession, json.dumps(data))
            versionless = accession.rsplit(".", 1)[0]
            self.redis.sadd(f"versions:{versionless}", accession)

    def _post(self, body):
        return self.client.post(reverse("transcripts"), data=json.dumps(body),
                                content_type="application/json")

    def test_versioned_ids(self):
        response = self._post({"ids": ["NM_000059.2", "NM_007294.3"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "NM_000059.2": self.transcripts["NM_000059.2"],
            "NM_007294.3": self.transcripts["NM_007294.3"],
        })

    def test_missing_versioned_id_is_null(self):
        response = self._post({"ids": ["NM_000059.2", "NM_000059.99"]})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["NM_000059.2"], self.transcripts["NM_000059.2"])
        self.assertIn("NM_000059.99", data)
        self.assertIsNone(data["NM_000059.99"])

    def test_versionless_id_expands(self):
        response = self._post({"ids": ["NM_000059"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "NM_000059.2": self.transcripts["NM_000059.2"],
            "NM_000059.10": self.transcripts["NM_000059.10"],
        })

    def test_unknown_versionless_id_contributes_nothing(self):
        response = self._post({"ids": ["NM_999999"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {})

    def test_mixed_batch(self):
        response = self._post({"ids": ["NM_000059", "NM_007294.3", "NM_000059.99"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "NM_000059.2": self.transcripts["NM_000059.2"],
            "NM_000059.10": self.transcripts["NM_000059.10"],
            "NM_007294.3": self.transcripts["NM_007294.3"],
            "NM_000059.99": None,
        })

    def test_get_not_allowed(self):
        response = self.client.get(reverse("transcripts"))
        self.assertEqual(response.status_code, 405)

    def test_malformed_body(self):
        response = self.client.post(reverse("transcripts"), data="not json",
                                    content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_missing_ids_key(self):
        response = self._post({"foo": "bar"})
        self.assertEqual(response.status_code, 400)

    def test_ids_not_a_list(self):
        response = self._post({"ids": "NM_000059.2"})
        self.assertEqual(response.status_code, 400)

    def test_ids_not_all_strings(self):
        response = self._post({"ids": ["NM_000059.2", 123, None]})
        self.assertEqual(response.status_code, 400)

    def test_too_many_ids(self):
        from cdot_rest.views import MAX_BATCH_SIZE
        response = self._post({"ids": ["NM_000059.2"] * (MAX_BATCH_SIZE + 1)})
        self.assertEqual(response.status_code, 400)
