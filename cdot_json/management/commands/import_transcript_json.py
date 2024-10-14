import gzip
import ijson
import json
import logging
import pickle
import redis

from django.conf import settings
from django.core.management.base import BaseCommand
from itertools import islice
from cdot.hgvs.dataproviders import LocalDataProvider


def chunks(data, SIZE=10000):
    it = iter(data)
    for i in range(0, len(data), SIZE):
        yield {k:data[k] for k in islice(it, SIZE)}


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

            transcripts_data = {}
            # Make this an iterator so that we can pass it and it also does work for us
            def transcripts_iter():
                for transcript_id, transcript in ijson.kvitems(f, 'transcripts'):
                    transcripts_data[transcript_id] = json.dumps(transcript)
                    yield transcript_id, transcript

            tx_by_gene, tx_intervals = LocalDataProvider._get_tx_by_gene_and_intervals(transcripts_iter())

            logging.info("Inserting into to Redis...")
            for td in chunks(transcripts_data):
                r.mset(td)

            # Store eg "refseq_count" or "ensembl_count"
            key = annotation_consortium.lower() + "_count"
            r.set(key, len(transcripts_data))
            del transcripts_data

            logging.info("Adding gene data")
            f.seek(0)
            genes_data = {}
            for gene_id, gene in ijson.kvitems(f, 'genes'):
                if gene_symbol := gene["gene_symbol"]:
                    genes_data[gene_symbol] = json.dumps(gene)
            r.mset(genes_data)
            del genes_data

            logging.info("Adding transcripts for gene names")
            for gene_name, transcript_set in tx_by_gene.items():
                r.sadd(f"transcripts:{gene_name}", *tuple(transcript_set))

            logging.info("Adding transcript interval trees")
            for contig, iv_tree in tx_intervals.items():
                # Will need to combine interval trees from different imports
                if existing_iv_tree_pickle := r.get(contig):
                    existing_iv_tree = pickle.loads(existing_iv_tree_pickle)
                    iv_tree = iv_tree | existing_iv_tree
                iv_tree_pickle = pickle.dumps(iv_tree)
                r.set(contig, iv_tree_pickle)
