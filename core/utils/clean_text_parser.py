import re
import html


def clean_text(text):
    """
    Clean and normalize text extracted from HTML.

    Handles:
    - HTML entities (&nbsp;, &amp;, etc.)
    - Extra spaces / newlines / tabs
    - Invisible characters
    - Encoding issues
    """

    if not text:
        return ""

    # ---------------------------------
    # CONVERT TO STRING
    # ---------------------------------
    text = str(text)

    # ---------------------------------
    # UNESCAPE HTML ENTITIES
    # ---------------------------------
    text = html.unescape(text)

    # ---------------------------------
    # REMOVE NON-BREAKING SPACES
    # ---------------------------------
    text = text.replace("\xa0", " ")

    # ---------------------------------
    # REMOVE LINE BREAKS / TABS
    # ---------------------------------
    text = re.sub(r"[\n\r\t]+", " ", text)

    # ---------------------------------
    # COLLAPSE MULTIPLE SPACES
    # ---------------------------------
    text = re.sub(r"\s+", " ", text)

    # ---------------------------------
    # STRIP EDGES
    # ---------------------------------
    text = text.strip()

    return text