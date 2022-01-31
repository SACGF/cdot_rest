import redis
from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import render


def index(request):
    r = redis.Redis(**settings.REDIS_KWARGS)
    refseq_count = int(r.get("refseq_count") or 0)
    ensembl_count = int(r.get("ensembl_count") or 0)

    context = {
        "refseq_count": refseq_count,
        "ensembl_count": ensembl_count,
        "total_count": refseq_count + ensembl_count,
    }
    return render(request, 'index.html', context)


def transcript(request, transcript_version):
    # I don't think there's much point caching this as it just fetches it out of Redis anyway
    r = redis.Redis(**settings.REDIS_KWARGS)
    data = r.get(transcript_version)
    if data is None:
        raise Http404(transcript_version + " not found")

    max_age = getattr(settings, 'CACHE_CONTROL_MAX_AGE', 2592000)
    response = HttpResponse(data, content_type='application/json')
    response['Cache-Control'] = 'max-age=%d' % max_age
    return response
