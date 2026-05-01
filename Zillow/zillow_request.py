import asyncio
from typing import List
import httpx
import json
from parsel import Selector

client = httpx.AsyncClient(
    # enable http2
    http2=True,
    # add basic browser like headers to prevent being blocked
    headers={
        "accept-language": "en-US,en;q=0.9",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36",
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "accept-language": "en-US;en;q=0.9",
        "accept-encoding": "gzip, deflate, br",
    },
)

async def scrape_properties(urls: List[str]):
    """scrape zillow property pages for property data"""
    to_scrape = [client.get(url) for url in urls]
    results = []
    for response in asyncio.as_completed(to_scrape):
        response = await response
        if response.status_code != 200:
            raise RuntimeError(
                f"Zillow request failed with {response.status_code}: {response.url}"
            )
        selector = Selector(response.text)
        data = selector.css("script#__NEXT_DATA__::text").get()
        if data:
            # Option 1: some properties are located in NEXT DATA cache
            data = json.loads(data)
            property_data = json.loads(data["props"]["pageProps"]["componentProps"]["gdpClientCache"])
            property_data = property_data[list(property_data)[0]]['property']
        else:
            # Option 2: other times it's in Apollo cache
            data = selector.css("script#hdpApolloPreloadedData::text").get()
            data = json.loads(json.loads(data)["apiCache"])
            property_data = next(
                v["property"] for k, v in data.items() if "ForSale" in k
            )
        results.append(property_data)
    return results

if __name__ == "__main__":
    urls = [
        "https://www.zillow.com/apartments/east-boston-ma/brandywyne-village/5XjK8K/",
        "https://www.zillow.com/apartments/east-boston-ma/bremen-282/CkCRpH/",
    ]
    properties = asyncio.run(scrape_properties(urls))
    print(json.dumps(properties, indent=2))