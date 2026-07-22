---
name: cdotlib-api
description: Resolve HGVS variants and look up RefSeq/Ensembl transcript and gene data via the free, public cdotlib.org REST API. Use when you need transcript coordinates/exons, MANE or canonical transcript selection, gene-to-transcript mapping, region overlap queries, or historical transcript versions across GRCh37, GRCh38, or T2T-CHM13v2.0. No API key or auth required.
license: MIT
---

# Using the cdotlib.org transcript & gene API

`https://cdotlib.org` is a free, public, read-only REST API serving
[cdot](https://github.com/SACGF/cdot) transcript and gene data. Every endpoint
returns JSON. There is no API key, no auth, and no signup — just make the request.

Use it to answer questions like: *"What are the exon coordinates of NM_000059.3?"*,
*"What is the MANE Select transcript for BRCA2?"*, *"Which transcripts overlap this
genomic region?"*, or *"What versions of this transcript exist?"*.

## Concepts you need first

- A **transcript accession** looks like `NM_000059.3` (RefSeq) or
  `ENST00000380152.8` (Ensembl). The trailing `.N` is the **version**.
- **Versioned** lookup (`NM_000059.3`) returns one record. **Versionless**
  lookup (`NM_000059`) returns *every stored version*, keyed by full accession.
- A **gene symbol** looks like `BRCA2`.
- Genome builds are exactly: `GRCh37`, `GRCh38`, `T2T-CHM13v2.0`.

## Tasks

### Get a transcript (exons, CDS, coordinates)
```
curl https://cdotlib.org/transcript/NM_000059.3      # one version
curl https://cdotlib.org/transcript/NM_000059        # all versions
```

### Pick the canonical transcript for a gene (use MANE)
Returns the matched RefSeq + Ensembl MANE Select pair, keyed by accession:
```
curl https://cdotlib.org/transcripts/gene/BRCA2/mane/GRCh38
```
Add `?annotation_consortium=RefSeq` for just one half, or `?plus_clinical=true`
to also include MANE Plus Clinical. Returns `{}` if the gene has no MANE transcript.

### List a gene's transcripts
```
curl https://cdotlib.org/transcripts/gene/BRCA2                 # accessions
curl https://cdotlib.org/transcripts/gene/BRCA2/tags/GRCh38     # with tags, longest-first
```
Tags (`MANE_Select`, `RefSeq_Select`, `Ensembl_canonical`, ...) are returned
**verbatim from the source annotation**: RefSeq spells them with spaces
(`MANE Select`), Ensembl with underscores (`MANE_Select`). Normalise spaces to
underscores before matching tags across consortia.

### Get a gene record
```
curl https://cdotlib.org/gene/BRCA2
```

### Find transcripts overlapping a genomic region
`aln_method` is `splign` for RefSeq, `genebuild` for Ensembl:
```
curl https://cdotlib.org/transcripts/region/NC_000021.8/splign/36160000/36261000
```

### Batch lookup (many transcripts in one request)
POST up to 10000 ids. Versionless ids expand to all versions; missing versioned
ids come back as `null`:
```
curl -X POST https://cdotlib.org/transcripts \
  -H 'Content-Type: application/json' \
  -d '{"ids": ["NM_000059.3", "NM_007294", "ENST00000380152.8"]}'
```

## To actually convert HGVS <-> genomic coordinates

This API is the **data layer**, not an HGVS engine. To map HGVS to/from genomic
coordinates, use the [cdot Python library](https://github.com/SACGF/cdot) with
its `RESTDataProvider` pointed at this server — it handles transcript selection
and the HGVS maths for you:

```python
from cdot.hgvs.dataproviders import RESTDataProvider
hdp = RESTDataProvider(url="https://cdotlib.org")
# feed hdp to the `hgvs` package's AssemblyMapper / VariantMapper
```

## Reference

- Machine-readable OpenAPI 3.1 spec: <https://cdotlib.org/static/openapi.yaml>
- Full agent guide: <https://cdotlib.org/llms.txt>
- cdot library: <https://github.com/SACGF/cdot>
