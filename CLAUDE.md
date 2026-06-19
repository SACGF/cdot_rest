# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`cdot_rest` is a Django web service that serves [cdot](https://github.com/SACGF/cdot/)
transcript/gene data over REST so clients can resolve [HGVS](http://varnomen.hgvs.org/)
against historical RefSeq and Ensembl transcripts (GRCh37, GRCh38, T2T-CHM13v2.0). Public
instance: https://cdotlib.org

There is **no relational database** — all data lives in Redis, loaded by a management command.
Django serves read-only HTTP endpoints on top of it. gunicorn runs the app in production;
nginx terminates TLS and proxies to it.

## Commands

```bash
source .venv/bin/activate                      # uv-managed venv

# Tests (no DB, no live Redis - uses fakeredis + Django SimpleTestCase)
python3 manage.py test
python3 manage.py test cdot_rest.tests.BatchTranscriptsViewTests             # one class
python3 manage.py test cdot_rest.tests.BatchTranscriptsViewTests.test_mixed_batch  # one test

# Load data into Redis (requires a running Redis on localhost:6379, db 0)
python3 manage.py import_transcript_json latest [--clear]
python3 manage.py import_transcript_json cdot_json \
    --annotation-consortium=RefSeq --cdot-data-version=0.2.32 \
    cdot-0.2.32.refseq.GRCh38.json.gz [--clear]
```

Dependencies are managed with [uv](https://docs.astral.sh/uv/); `requirements*.txt` are
compiled from `requirements*.in`. Note `requirements.in` pins `cdot` to its git `main` branch
because the `latest` loader depends on `cdot.data_release` helpers not yet in a PyPI release.

## Architecture

**Redis is the entire data layer.** The key schema (set by `import_transcript_json`, read by
the views) is the contract that ties the two halves together:

- `<accession>` (e.g. `NM_000059.3`) → JSON transcript
- `<gene_symbol>` (e.g. `BRCA2`) → JSON gene
- `versions:<versionless_accession>` → Redis set of full accessions (powers versionless lookup)
- `transcripts:<gene_name>` → Redis set of transcript accessions for a gene
- `<contig>` → pickled interval tree (region queries)
- `refseq_count` / `ensembl_count`, `cdot_data_version`, `cdot_release_url` → front-page metadata

**`cdot_rest/views.py`** — plain Django function views, no DRF. Two access styles:
1. Direct Redis reads via `_get_redis()` for the simple key lookups (transcript, gene, batch).
   Versionless accessions (no `.`) expand through the `versions:` set, sorted *numerically* by
   version (`_version_sort_key`, so `.10` sorts after `.2`).
2. `RedisDataProvider` (`cdot_rest/redis_data_provider.py`) for gene/region/tag queries — it
   subclasses cdot's `LocalDataProvider` and overrides the `_get_*` accessors to read Redis.
   This reuses cdot's ranking/selection logic so the server stays consistent with the cdot
   client library. Tags ranking is intentionally left client-side (issue #12).

All read views are wrapped in `@cache_page`. The batch endpoint (`transcripts`) is POST-only,
CSRF-exempt, capped at `MAX_BATCH_SIZE` ids.

**`cdot_json/management/commands/import_transcript_json.py`** — the loader. Key invariants:
- Parses gzipped cdot JSON with **ijson** (streaming) — loading the whole file via `json`
  OOM-killed a 4 GB server.
- Loading is **additive/merge, not overwrite**: the same accession appears in multiple
  per-build files, so `genome_builds` are merged (`_merge_genome_builds`), counts only count
  accessions new to Redis (idempotent re-import), and interval trees are unioned. Use `--clear`
  (`flushdb`) when upgrading releases to avoid stale accessions.

`cdot_rest/tests.py` holds all tests (SimpleTestCase + fakeredis). The OpenAPI spec
(`cdot_rest/static/openapi.yaml`) and docs are hand-edited static files; tests guard that the
spec parses and its internal `$ref`s resolve.
