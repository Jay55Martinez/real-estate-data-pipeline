import asyncio
import json
import os
from typing import List
from dotenv import load_dotenv
from scrapfly import ScrapeConfig, ScrapflyClient

load_dotenv()
api_key = os.getenv("SCRAPFLY_API_KEY")
scrapfly = ScrapflyClient(key=api_key)

async def scrape_properties(urls: List[str]):
    """scrape zillow property pages for property data"""
    to_scrape = [ScrapeConfig(url, asp=True, country="US") for url in urls]
    results = []
    async for result in scrapfly.concurrent_scrape(to_scrape):
        data = result.selector.css("script#__NEXT_DATA__::text").get()
        if data:
            # Option 1: some properties are located in NEXT DATA cache
            data = json.loads(data)
            property_data = json.loads(data["props"]["pageProps"]["componentProps"]["gdpClientCache"])
            property_data = property_data[list(property_data)[0]]['property']
        else:
            # Option 2: other times it's in Apollo cache
            data = result.selector.css("script#hdpApolloPreloadedData::text").get()
            data = json.loads(json.loads(data)["apiCache"])
            property_data = next(v["property"] for k, v in data.items() if "ForSale" in k)
        results.append(property_data)
    return results

async def run():
    data = await scrape_properties(
            ["https://www.zillow.com/homedetails/63-Border-St-1-Boston-MA-02128/461019370_zpid/"]
        )
    with open("sample_property.json", "w") as f:
        json.dump(data, f, indent=4)

if __name__ == "__main__":
    asyncio.run(run())