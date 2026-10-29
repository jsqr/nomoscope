"""Answers to questions about the law in force, from retrieved passages.

`ask` answers one question in one jurisdiction: it retrieves passages
with `nomoscope.search`, gives them to a chat model with the answers the
question depends on, and checks the response (`schema.Response`): an
answer, a rationale citing the passages, the passages cited, and a
confidence. `group` reads a file of questions that form a DAG; questions
are asked in dependency order. `store` keeps the answers, in files that
are only ever added.
"""
