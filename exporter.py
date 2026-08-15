import json


def header_json(header):
    return json.dumps({str(k): _json_value(v) for k, v in header.items()}, ensure_ascii=False, indent=2)


def _json_value(value):
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)
