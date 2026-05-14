from urllib.parse import quote, urlsplit, urlunsplit


def normalize_url(url):

    if not url:
        return None

    try:

        parts = urlsplit(url)

        # encode path safely
        path = quote(parts.path)

        # encode query safely
        query = quote(
            parts.query,
            safe="=&"
        )

        normalized = urlunsplit((
            parts.scheme,
            parts.netloc,
            path,
            query,
            parts.fragment
        ))

        return normalized

    except Exception:

        return url