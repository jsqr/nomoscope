"""Embeddings of derived segments, and the model calls that make them.

`models` lists the embedding and reranking models by slug. `client` calls
a provider. `text` rebuilds each segment's embedded text from the tables.
`store` keeps vectors by `content_sha256`, in files that are only ever
added. `run` embeds what is pending, within a spend limit.
"""
