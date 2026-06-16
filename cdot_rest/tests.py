import json
from unittest import mock

import fakeredis
from django.core.cache import cache
from django.test import SimpleTestCase
from django.urls import reverse


def _make_transcript(accession):
    """ Minimal transcript payload - shape doesn't matter for these tests, identity does """
    return {"id": accession, "gene_name": "BRCA2", "cdot_data_version": "0.2.26"}


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
