"""Settings and stub replies for tests that run the extraction tasks."""

import json
import os
from pathlib import Path

from nomoscope import chat, settings
from tests import fake


def configure(**tables: dict[str, str]) -> settings.Settings:
    """Add settings tables to the scratch settings file and reload it."""
    path = Path(os.environ[settings.env_config(fake.APP)])
    text = path.read_text()
    for name, table in tables.items():
        text += f"[{name}]\n" + "".join(f'{k} = "{v}"\n' for k, v in table.items())
    path.write_text(text)
    return settings.load(fake.APP)


def reply(items: dict) -> chat.Completion:
    return chat.Completion(json.dumps(items), 100, 50)
