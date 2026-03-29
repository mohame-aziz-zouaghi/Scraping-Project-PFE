class BaseSite:

    name = "BASE"

    def scrape_site(self):
        raise NotImplementedError

    def search(self, query):
        raise NotImplementedError

    def parse_product(self, url):
        raise NotImplementedError

    def extract_categories(self):
        raise NotImplementedError