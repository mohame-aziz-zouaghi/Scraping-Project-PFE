import re

from bs4 import BeautifulSoup


def parse_specs(html_list):
    """
    Converts HTML UL list into structured key/value dict
    """

    if not html_list:
        return {}

    html = " ".join(html_list) if isinstance(html_list, list) else str(html_list)

    soup = BeautifulSoup(html, "lxml")

    specs = {}

    for li in soup.find_all("li"):
        text = li.get_text(" ", strip=True)

        # remove emojis and noise (keep letters, numbers, accents, : and -)
        text = re.sub(r"[^a-zA-Z0-9À-ÿ\s:\-]", "", text)

        if ":" in text:
            key, value = text.split(":", 1)

            key = key.strip().lower()
            value = value.strip()

            specs[key] = value

    return specs