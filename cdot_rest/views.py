import json

import redis
from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import cache_page
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from cdot_rest.redis_data_provider import RedisDataProvider

MINUTE_SECONDS = 60
HOUR_SECONDS = MINUTE_SECONDS * 60
DAY_SECONDS = HOUR_SECONDS * 24

# Cap on how many ids a single batch request may ask for (after versionless expansion the
# number of returned transcripts can be larger - this only limits the requested ids).
MAX_BATCH_SIZE = 10000


def _get_redis():
    return redis.Redis(**settings.REDIS_KWARGS)


def _is_versionless(accession):
    """ RefSeq/Ensembl accessions have no internal '.', so a '.' means an explicit version """
    return "." not in accession


def _version_sort_key(accession):
    """ Sort 'NM_000059.10' after 'NM_000059.2' (numeric, not lexical) """
    _, _, version = accession.rpartition(".")
    try:
        return int(version)
    except ValueError:
        return -1


def _expand_versionless(r, versionless_accession):
    """ Return all full accessions stored for a versionless accession, version-sorted """
    members = r.smembers(f"versions:{versionless_accession}")
    accessions = [m.decode() if isinstance(m, bytes) else m for m in members]
    return sorted(accessions, key=_version_sort_key)


def _get_transcripts(r, accessions):
    """ MGET accessions, returning a dict keyed by accession with parsed JSON (None for misses) """
    if not accessions:
        return {}
    values = r.mget(accessions)
    return {ac: (json.loads(value) if value is not None else None)
            for ac, value in zip(accessions, values)}


def _decode(value):
    """ Redis returns bytes; templates want str (None stays None) """
    return value.decode() if isinstance(value, bytes) else value


@cache_page(HOUR_SECONDS)
def index(request):
    r = _get_redis()
    refseq_count = int(r.get("refseq_count") or 0)
    ensembl_count = int(r.get("ensembl_count") or 0)

    context = {
        "refseq_count": refseq_count,
        "ensembl_count": ensembl_count,
        "total_count": refseq_count + ensembl_count,
        "cdot_data_version": _decode(r.get("cdot_data_version")),
        "cdot_release_url": _decode(r.get("cdot_release_url")),
    }
    return render(request, 'index.html', context)


@cache_page(DAY_SECONDS)
def gene(request, gene_symbol):
    r = _get_redis()
    data = r.get(gene_symbol)
    if data is None:
        raise Http404(gene_symbol + " not found")

    return HttpResponse(data, content_type='application/json')


@cache_page(DAY_SECONDS)
def transcript(request, transcript_version):
    r = _get_redis()

    if _is_versionless(transcript_version):
        # Versionless accession - return all available versions keyed by full accession
        accessions = _expand_versionless(r, transcript_version)
        if not accessions:
            raise Http404(transcript_version + " not found")
        return JsonResponse(_get_transcripts(r, accessions))

    data = r.get(transcript_version)
    if data is None:
        raise Http404(transcript_version + " not found")

    return HttpResponse(data, content_type='application/json')


@csrf_exempt
@require_POST
def transcripts(request):
    """ Batch fetch transcripts.

        POST body: {"ids": ["NM_000059.3", "NM_007294", ...]}

        Returns a dict keyed by full accession. Versioned ids that are missing return null
        (matching the cdot client's "store None" behaviour). Versionless ids are expanded to
        all of their available versions; an unknown versionless id contributes no keys.
    """
    try:
        body = json.loads(request.body)
        ids = body["ids"]
    except (ValueError, KeyError, TypeError):
        return HttpResponseBadRequest('Expected JSON body of the form {"ids": [...]}')

    if not isinstance(ids, list):
        return HttpResponseBadRequest('"ids" must be a list')

    if not all(isinstance(accession, str) for accession in ids):
        return HttpResponseBadRequest('"ids" must be a list of strings')

    if len(ids) > MAX_BATCH_SIZE:
        return HttpResponseBadRequest(f"Too many ids requested (max {MAX_BATCH_SIZE})")

    r = _get_redis()
    accessions = []
    for accession in ids:
        if _is_versionless(accession):
            accessions.extend(_expand_versionless(r, accession))
        else:
            accessions.append(accession)

    return JsonResponse(_get_transcripts(r, accessions))


@cache_page(DAY_SECONDS)
def transcripts_for_gene(request, gene_symbol):
    rdp = RedisDataProvider(_get_redis())
    data = {"results": rdp.get_tx_for_gene(gene_symbol)}
    return JsonResponse(data)


@cache_page(DAY_SECONDS)
def transcripts_for_region(request, contig, aln_method, start, end):
    rdp = RedisDataProvider(_get_redis())
    data = {"results": rdp.get_tx_for_region(contig, aln_method, start, end)}
    return JsonResponse(data)
