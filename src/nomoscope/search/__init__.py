"""Search the law in force by meaning, by words, or both, with optional reranking.

`query.search` takes the segments of the sections in force on a date and
ranks them:

- by the inner product of their stored vectors with the query's
  (`nomoscope.embed`), the query carrying the `[search] instruction`;
- by BM25 over their embedded text (`fulltext`), when asked;
- by reciprocal rank fusion of the two lists, when both are made;
- by the reranker's probability that a segment answers the query, for
  the top `rerank_depth`, when asked.

A section's hit is its best segment.
"""
