from bs4 import BeautifulSoup


def clean_html(text):
        if not text:
            return ""

        return BeautifulSoup(text, "lxml").get_text(" ", strip=True)