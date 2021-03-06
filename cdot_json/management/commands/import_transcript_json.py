import json
import logging

from django.conf import settings
from django.core.management.base import BaseCommand

import gzip
import ijson
import redis


class Command(BaseCommand):
    ANNOTATION_CONSORTIUMS = ["RefSeq", "Ensembl"]
    VALID_ANNOTATION_CONSORTIUMS = ', '.join(["'%s" % a for a in ANNOTATION_CONSORTIUMS])

    def add_arguments(self, parser):
        annotation_help = "One of " + Command.VALID_ANNOTATION_CONSORTIUMS
        parser.add_argument('cdot_json', help='cdot json file')
        parser.add_argument('--annotation-consortium', required=True, help=annotation_help)

    def handle(self, *args, **options):
        annotation_consortium = options["annotation_consortium"]
        if annotation_consortium not in Command.ANNOTATION_CONSORTIUMS:
            raise ValueError("--annotation-consortium must be one of " + Command.VALID_ANNOTATION_CONSORTIUMS)

        r = redis.Redis(**settings.REDIS_KWARGS)
        with gzip.open(options["cdot_json"]) as f:
            logging.info("Reading cdot JSON...")
            # Loading it all into RAM via json was killed from lack of memory on a 4gig server, so using ijson

            mapping = {}
            num_transcripts = 0
            for transcript_id, transcript in ijson.kvitems(f, 'transcripts'):
                mapping[transcript_id] = json.dumps(transcript)
                num_transcripts += 1

            logging.info("Inserting into to Redis...")
            r.mset(mapping)

            # Store eg "refseq_count" or "ensembl_count"
            key = annotation_consortium.lower() + "_count"
            r.set(key, num_transcripts)

