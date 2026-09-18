"""Protected report links contain opaque selection, never login credentials."""

from urllib.parse import urlsplit


def report_url(public_origin, path):
    """Append a compiler-owned report route to an origin without URL credentials."""
    if type(public_origin) is not str or any(
        ord(char) <= 32 or ord(char) == 127 for char in public_origin
    ):
        raise ValueError("Reports require a public HTTP origin.")
    try:
        parsed = urlsplit(public_origin)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and "\\" not in public_origin
        )
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Reports require a public HTTP origin.")
    if (
        type(path) is not str
        or not path.startswith("/admin/reports/")
        or any(ord(char) <= 32 or ord(char) == 127 for char in path)
        or any(value in path for value in ("\\", "?", "#", "%", "..", "//"))
    ):
        raise ValueError("Reports require a compiler-owned protected route.")
    return public_origin.rstrip("/") + path
