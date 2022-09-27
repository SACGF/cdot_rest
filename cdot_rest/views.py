import redis
from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import cache_page

from cdot_rest.redis_data_provider import RedisDataProvider

MINUTE_SECONDS = 60
HOUR_SECONDS = MINUTE_SECONDS * 60
DAY_SECONDS = HOUR_SECONDS * 24

def _get_redis():
    return redis.Redis(**settings.REDIS_KWARGS)


@cache_page(HOUR_SECONDS)
def index(request):
    r = _get_redis()
    refseq_count = int(r.get("refseq_count") or 0)
    ensembl_count = int(r.get("ensembl_count") or 0)

    context = {
        "refseq_count": refseq_count,
        "ensembl_count": ensembl_count,
        "total_count": refseq_count + ensembl_count,
    }
    return render(request, 'index.html', context)


@cache_page(DAY_SECONDS)
def transcript(request, transcript_version):
    r = _get_redis()
    data = r.get(transcript_version)
    if data is None:
        raise Http404(transcript_version + " not found")

    return HttpResponse(data, content_type='application/json')


@cache_page(DAY_SECONDS)
def transcripts_for_gene(request, gene_symbol):
    rdp = RedisDataProvider(_get_redis())
    data = {"transcripts": rdp.get_tx_for_gene(gene_symbol)}
    print(data)
    return JsonResponse(data)


@cache_page(DAY_SECONDS)
def transcripts_for_region(request, contig, aln_method, start, end):
    rdp = RedisDataProvider(_get_redis())
    data = {"transcripts": rdp.get_tx_for_region(contig, aln_method, start, end)}
    return JsonResponse(data)
