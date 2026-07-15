import json
from typing import Any


def decode_json_argument_strings(arguments: dict[str, Any]) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for key, value in arguments.items():
        if isinstance(value, str) and value.strip().startswith(("{", "[")):
            try:
                decoded[key] = json.loads(value)
                continue
            except json.JSONDecodeError:
                pass
        decoded[key] = value
    return decoded

