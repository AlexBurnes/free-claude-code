"""Preserve inference middleware behavior under launch-session URL prefixes."""


def inference_path(path: str) -> str:
    prefix = "/fcc/sessions/"
    if path.startswith(prefix):
        session_id, separator, remainder = path[len(prefix) :].partition("/")
        if session_id and separator:
            return f"/{remainder}"
    return path
