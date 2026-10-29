from nomoscope.extract import definitions, enactments, interpretation, penalties, references

TASKS = (
    definitions.TASK,
    interpretation.TASK,
    penalties.TASK,
    references.TASK,
    enactments.TASK,
)
BY_NAME = {t.name: t for t in TASKS}
