import gzip
import ijson
import json
import logging
import pickle
import redis

from django.conf import settings
from django.core.management.base import BaseCommand
from itertools import islice
from cdot.data_release import get_latest_data_version_and_release
from cdot.hgvs.dataproviders import LocalDataProvider


def chunks(data, SIZE=10000):
    it = iter(data)
    for i in range(0, len(data), SIZE):
        yield {k:data[k] for k in islice(it, SIZE)}


class Command(BaseCommand):
    ANNOTATION_CONSORTIUMS = ["RefSeq", "Ensembl"]
    VALID_ANNOTATION_CONSORTIUMS = ', '.join(["'%s" % a for a in ANNOTATION_CONSORTIUMS])

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest='subcommand')
        parser_file = subparsers.add_parser("cdot_json", help="Single cdot JSON file")
        annotation_help = "One of " + Command.VALID_ANNOTATION_CONSORTIUMS
        parser_file.add_argument('--annotation-consortium', required=True, help=annotation_help)
        parser_file.add_argument('--cdot-data-version', required=True, help="Need to specify as using iterator to pull out json")
        parser_file.add_argument('filename', required=True, help="cdot json.gz file")
        # No extra params - handles automatically
        _parser_latest = subparsers.add_parser("latest", help="Automatically retrieve latest from cdot GitHub")


    def handle(self, *args, **options):
        r = redis.Redis(**settings.REDIS_KWARGS)
        subcommand = options["subcommand"]
        if subcommand == "cdot_json":
            annotation_consortium = options["annotation_consortium"]
            cdot_data_version = options["cdot_data_version"]
            if annotation_consortium not in Command.ANNOTATION_CONSORTIUMS:
                raise ValueError("--annotation-consortium must be one of " + Command.VALID_ANNOTATION_CONSORTIUMS)

            with gzip.open(options["filename"]) as cdot_json_file:
                self._insert_transcripts(r, cdot_data_version, annotation_consortium, cdot_json_file)
        elif subcommand == "latest":
            cdot_data_version, _release = get_latest_data_version_and_release()
            pattern = re.compile("cdot-\d+\.\d+\.\d+.all-builds-(ensembl|refseq)-.*.json.gz")

            for browser_url in get_latest_browser_urls():
                filename = browser_url.rsplit("/", maxsplit=1)[-1]
                if m := pattern.match(filename):
                    annotation_consortium = m.group(1)
                    logging.info("Downloading annotation_consortium=%s, url=%s", annotation_consortium, browser_url)
                    response = requests.get(browser_url, stream=True, timeout=60)
                    with gzip.GzipFile(fileobj=response.raw) as cdot_json_file:
                        self._insert_transcripts(r, cdot_data_version, annotation_consortium, cdot_json_file)


    def _insert_transcripts(self, r: Redis, cdot_data_version, annotation_consortium, cdot_json_file):
            logging.info("Reading cdot JSON...")
            # Loading it all into RAM via json was killed from lack of memory on a 4gig server, so using ijson

            transcripts_data = {}
            # Make this an iterator so that we can pass it and it also does work for us
            def transcripts_iter():
                for transcript_id, transcript in ijson.kvitems(cdot_json_file, 'transcripts'):
                    transcript["cdot_data_version"] = cdot_data_version
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
            for gene_id, gene in ijson.kvitems(cdot_json_file, 'genes'):
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
