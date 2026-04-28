import json
import re
from urllib.request import Request, urlopen


class BaseBrandAdapter:
    user_agent = "Mozilla/5.0 (compatible; MeherloomBot/1.0; +https://example.com/bot)"
    json_object_pattern = re.compile(r"\{.*\}", re.DOTALL)

    def __init__(self, brand):
        self.brand = brand

    def fetch_url(self, url):
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urlopen(request, timeout=self.brand.request_timeout) as response:
            return response.read().decode("utf-8", errors="ignore")

    def fetch_product(self, product):
        raise NotImplementedError

    def parse_json_candidate(self, raw_text):
        candidate = (raw_text or "").strip()
        if not candidate:
            return None

        # Some storefronts HTML-escape embedded JSON.
        candidate = (
            candidate.replace("&quot;", '"')
            .replace("&#34;", '"')
            .replace("&amp;", "&")
            .replace("&#x27;", "'")
        )
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            match = self.json_object_pattern.search(candidate)
            if not match:
                return None
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
