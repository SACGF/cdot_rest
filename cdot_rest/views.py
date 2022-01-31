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
    r = redis.Redis(**settings.REDIS_KWARGS)
    data = r.get(transcript_version)
    if data is None:
        raise Http404(transcript_version + " not found")
    return HttpResponse(data, content_type='application/json')
