import gzip
import ijson
import json
import logging
import re
import requests
import pickle
import tempfile
from redis import Redis

from django.conf import settings
from django.core.management.base import BaseCommand
from cdot.data_release import get_latest_combo_file_urls, get_latest_data_version_and_release
from cdot.hgvs.dataproviders import LocalDataProvider


class Command(BaseCommand):
    ANNOTATION_CONSORTIUMS = ["RefSeq", "Ensembl"]
    VALID_ANNOTATION_CONSORTIUMS = ', '.join(["'%s" % a for a in ANNOTATION_CONSORTIUMS])
    # Genome builds to pull when running 'latest' (see issues #11, #13).
    LATEST_GENOME_BUILDS = ["GRCh37", "GRCh38", "T2T-CHM13v2.0"]

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest='subcommand')
        parser_file = subparsers.add_parser("cdot_json", help="Single cdot JSON file")
        annotation_help = "One of " + Command.VALID_ANNOTATION_CONSORTIUMS
        parser_file.add_argument('--annotation-consortium', required=True, help=annotation_help)
        parser_file.add_argument('--cdot-data-version', required=True, help="Need to specify as using iterator to pull out json")
        parser_file.add_argument('filename', help="cdot json.gz file")
        # No extra params - handles automatically
        parser_latest = subparsers.add_parser("latest", help="Automatically retrieve latest from cdot GitHub")

        # Loading is additive/merge (genome_builds merge, counts accumulate, sets/interval trees union),
        # so upgrading to a new release on top of an old one leaves stale data behind. --clear wipes the
        # Redis db first for a clean reload.
        for subparser in (parser_file, parser_latest):
            subparser.add_argument('--clear', action='store_true',
                                   help="Flush all existing data from Redis before loading (clean reload)")


    def handle(self, *args, **options):
        r = Redis(**settings.REDIS_KWARGS)
        if options.get("clear"):
            logging.info("Clearing existing data from Redis (--clear)")
            r.flushdb()
        subcommand = options["subcommand"]
        if subcommand == "cdot_json":
            annotation_consortium = options["annotation_consortium"]
            cdot_data_version = options["cdot_data_version"]
            if annotation_consortium not in Command.ANNOTATION_CONSORTIUMS:
                raise ValueError("--annotation-consortium must be one of " + Command.VALID_ANNOTATION_CONSORTIUMS)

            with gzip.open(options["filename"]) as cdot_json_file:
                self._insert_transcripts(r, cdot_data_version, annotation_consortium, cdot_json_file)
        elif subcommand == "latest":
            cdot_data_version, release = get_latest_data_version_and_release()
            # eg 'cdot-0.2.32.ensembl.GRCh38.json.gz' -> annotation_consortium='ensembl'
            pattern = re.compile(r"cdot-\d+\.\d+\.\d+\.(ensembl|refseq)\.(.+)\.json\.gz")

            for browser_url in get_latest_combo_file_urls(Command.ANNOTATION_CONSORTIUMS, Command.LATEST_GENOME_BUILDS):
                filename = browser_url.rsplit("/", maxsplit=1)[-1]
                if m := pattern.match(filename):
                    annotation_consortium = m.group(1)
                    logging.info("Downloading annotation_consortium=%s, url=%s", annotation_consortium, browser_url)
                    # Stream the (compressed) file to a temp file on disk rather than holding it
                    # (and a BytesIO copy) in RAM - it's read twice (transcripts then genes) so it
                    # needs to be seekable, but it doesn't need to live in memory.
                    with tempfile.NamedTemporaryFile(suffix=".json.gz") as tmp:
                        with requests.get(browser_url, timeout=60, stream=True) as response:
                            response.raise_for_status()
                            for chunk in response.iter_content(chunk_size=1 << 20):
                                tmp.write(chunk)
                        tmp.flush()
                        tmp.seek(0)
                        with gzip.GzipFile(fileobj=tmp) as cdot_json_file:
                            self._insert_transcripts(r, cdot_data_version, annotation_consortium, cdot_json_file)

            # Record which cdot data release this came from, so we can display it on the front page (issue #11)
            self._store_release(r, cdot_data_version, release)

    @staticmethod
    def _store_release(r: Redis, cdot_data_version, release):
        logging.info("Storing cdot data release %s", cdot_data_version)
        r.set("cdot_data_version", cdot_data_version)
        # GitHub release dict - link the front page to the actual release page
        if release_url := release.get("html_url"):
            r.set("cdot_release_url", release_url)
        else:
            r.delete("cdot_release_url")

    @staticmethod
    def _merge_genome_builds(existing_json, new_json):
        """ Combine the genome_builds of a transcript already in Redis with a newly read copy
            (eg the GRCh37 and GRCh38 records for the same accession), keeping the new copy's
            other fields. Returns a JSON string ready to store. """
        existing = json.loads(existing_json)
        new = json.loads(new_json)
        genome_builds = existing.get("genome_builds") or {}
        genome_builds.update(new.get("genome_builds") or {})
        new["genome_builds"] = genome_builds
        return json.dumps(new)


    BATCH_SIZE = 10000

    def _flush_transcripts(self, r: Redis, batch):
        """ Write a batch of {accession: transcript_json} to Redis, merging genome_builds with
            any existing copy. The same accession can appear in multiple per-build files (eg RefSeq
            NM_x.y is in both the GRCh37 and GRCh38 files, each with only its own build) - merge
            rather than overwrite, so we don't lose alignments. Returns the number of accessions
            new to Redis (so counts stay idempotent across re-imports). """
        accessions = list(batch)
        existing = r.mget(accessions)
        merged = {}
        new_accessions = 0
        for accession, existing_json in zip(accessions, existing):
            if existing_json is None:
                merged[accession] = batch[accession]
                new_accessions += 1
            else:
                merged[accession] = self._merge_genome_builds(existing_json, batch[accession])
        r.mset(merged)
        return new_accessions

    def _insert_transcripts(self, r: Redis, cdot_data_version, annotation_consortium, cdot_json_file):
            # Loading it all into RAM via json was killed from lack of memory on a 4gig server, so
            # using ijson. We also stream transcripts straight into Redis as we read them (flushing
            # each batch) rather than buffering the whole file's JSON - holding every transcript's
            # serialized JSON at once was the peak memory consumer.
            logging.info("Reading and inserting transcripts into Redis...")
            versions_by_accession = {}  # versionless accession -> set of full accessions
            new_accessions = 0
            batch = {}

            # Make this an iterator so that we can pass it and it also does work for us
            def transcripts_iter():
                nonlocal new_accessions
                for transcript_id, transcript in ijson.kvitems(cdot_json_file, 'transcripts'):
                    transcript["cdot_data_version"] = cdot_data_version
                    batch[transcript_id] = json.dumps(transcript)
                    versionless = transcript_id.rsplit(".", 1)[0]
                    versions_by_accession.setdefault(versionless, set()).add(transcript_id)
                    if len(batch) >= self.BATCH_SIZE:
                        new_accessions += self._flush_transcripts(r, batch)
                        batch.clear()
                    yield transcript_id, transcript

            tx_by_gene, tx_intervals = LocalDataProvider._get_tx_by_gene_and_intervals(transcripts_iter())
            if batch:  # final partial batch
                new_accessions += self._flush_transcripts(r, batch)
                batch.clear()

            # Store eg "refseq_count" or "ensembl_count" - accumulate across per-build files
            key = annotation_consortium.lower() + "_count"
            r.incrby(key, new_accessions)

            logging.info("Adding gene data")
            cdot_json_file.seek(0)
            genes_data = {}
            for gene_id, gene in ijson.kvitems(cdot_json_file, 'genes'):
                if gene_symbol := gene["gene_symbol"]:
                    genes_data[gene_symbol] = json.dumps(gene)
                    if len(genes_data) >= self.BATCH_SIZE:
                        r.mset(genes_data)
                        genes_data.clear()
            if genes_data:
                r.mset(genes_data)
            del genes_data

            logging.info("Adding transcripts for gene names")
            for gene_name, transcript_set in tx_by_gene.items():
                r.sadd(f"transcripts:{gene_name}", *tuple(transcript_set))

            logging.info("Adding versionless accession -> versions index")
            for versionless, version_set in versions_by_accession.items():
                r.sadd(f"versions:{versionless}", *tuple(version_set))
            del versions_by_accession

            logging.info("Adding transcript interval trees")
            for contig, iv_tree in tx_intervals.items():
                # Will need to combine interval trees from different imports
                if existing_iv_tree_pickle := r.get(contig):
                    existing_iv_tree = pickle.loads(existing_iv_tree_pickle)
                    iv_tree = iv_tree | existing_iv_tree
                iv_tree_pickle = pickle.dumps(iv_tree)
                r.set(contig, iv_tree_pickle)
