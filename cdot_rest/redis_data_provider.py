import json
import pickle
from codecs import decode

import redis

from cdot.hgvs.dataproviders import LocalDataProvider


class RedisDataProvider(LocalDataProvider):
    def __init__(self, r: redis.Redis):
        super().__init__()
        self.redis = r

    def _get_contig_interval_tree(self, alt_ac):
        contig_interval_tree = None
        if cit_pickle := self.redis.get(alt_ac):
            contig_interval_tree = pickle.loads(cit_pickle)
        return contig_interval_tree

    def _get_transcript(self, tx_ac):
        transcript = None
        if t_str := self.redis.get(tx_ac):
            transcript = json.loads(t_str)
        return transcript

    def _get_gene(self, gene):
        gene_data = None
        if g_str := self.redis.get(gene):
            gene_data = json.loads(g_str)
        return gene_data

    def _get_transcript_ids_for_gene(self, gene):
        return [decode(x) for x in self.redis.smembers(f"transcripts:{gene}")]
