import argparse
import json
import time
from pathlib import Path
from urllib.parse import urljoin

import pyzill
from pyzill.details import headers, parse_body_deparments, requests


OUTPUT_DIR = Path("pyzill_test_output")
OUTPUT_DIR.mkdir(exist_ok=True)
PROXY_FILE = Path(__file__).with_name("proxies.txt")
SEARCH_STATE_FILE = Path(__file__).parent / "Example Requests" / "async-create-search-page-state.json"
ZILLOW_BASE_URL = "https://www.zillow.com"


def load_proxy_urls(proxy_file=PROXY_FILE):
    """
    Loads proxies from proxies.txt.

    Expected format per line:
      username:password:host:port
    """
    if not proxy_file.exists():
        return []

    proxy_urls = []
    seen_proxy_urls = set()

    for line_number, raw_line in enumerate(proxy_file.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        parts = line.split(":")

        if len(parts) == 4:
            username, password, host, port = parts
            proxy_url = pyzill.parse_proxy(host, port, username, password)
            if proxy_url not in seen_proxy_urls:
                proxy_urls.append(proxy_url)
                seen_proxy_urls.add(proxy_url)
            continue

        print(f"Skipping malformed proxy on line {line_number}: expected username:password:host:port")

    return proxy_urls


def run_with_proxy_retry(label, request_func, proxy_urls):
    """
    Runs a pyzill request and retries with the next proxy when it raises.
    If proxies.txt is empty/missing, it tries once without a proxy.
    """
    attempts = proxy_urls or [None]
    last_exc = None

    for attempt_number, proxy_url in enumerate(attempts, start=1):
        try:
            if proxy_url:
                print(f"{label}: attempt {attempt_number}/{len(attempts)} using proxy.")
            else:
                print(f"{label}: attempt {attempt_number}/{len(attempts)} without proxy.")

            return request_func(proxy_url)
        except Exception as exc:
            last_exc = exc
            print(f"{label}: attempt {attempt_number} failed: {type(exc).__name__}: {exc}")

            if attempt_number < len(attempts):
                print(f"{label}: retrying with next proxy...")
                time.sleep(2)

    raise last_exc


def save_json(filename, data):
    path = OUTPUT_DIR / filename
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Saved {path}")


def save_failed_response(response, filename="failed_response.html"):
    path = OUTPUT_DIR / filename
    path.write_bytes(response.content[:500_000])
    print(f"Saved failed response preview to {path}")


def find_listing_by_id(data, property_id):
    if isinstance(data, dict):
        if property_id in {
            str(data.get("plid", "")),
            str(data.get("buildingId", "")),
            str(data.get("lotId", "")),
        }:
            return data

        detail_url = data.get("detailUrl")
        if isinstance(detail_url, str) and f"/{property_id}/" in detail_url:
            return data

        for value in data.values():
            match = find_listing_by_id(value, property_id)
            if match:
                return match

    if isinstance(data, list):
        for item in data:
            match = find_listing_by_id(item, property_id)
            if match:
                return match

    return None


def load_search_listing(property_id, search_state_file=SEARCH_STATE_FILE):
    if not search_state_file.exists():
        return None

    data = json.loads(search_state_file.read_text(encoding="utf-8"))
    return find_listing_by_id(data, property_id)


def resolve_property_url(property_id):
    if property_id.startswith(("http://", "https://")):
        return property_id

    if property_id.startswith("/"):
        return urljoin(ZILLOW_BASE_URL, property_id)

    listing = load_search_listing(property_id)

    if listing and listing.get("detailUrl"):
        print(f"Matched id to listing: {listing.get('statusText') or listing.get('address')}")
        return urljoin(ZILLOW_BASE_URL, listing["detailUrl"])

    raise ValueError(
        "Could not resolve that id to a Zillow detail URL. "
        "Pass the full detailUrl instead, for example: "
        "/apartments/east-boston-ma/addison/CkBTpw/"
    )


def get_from_department_url(url, proxy_url=None):
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    response = requests.get(url=url, headers=headers, proxies=proxies, impersonate="chrome124")

    if response.status_code >= 400:
        save_failed_response(response)
        raise ValueError(f"Zillow returned HTTP {response.status_code} for {url}")

    data = parse_body_deparments(response.content)
    if data is None:
        save_failed_response(response)
        raise ValueError(
            f"Could not parse Zillow department data from {url}. "
            "The response may be a block, CAPTCHA, or unsupported page shape."
        )

    return data


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch Zillow apartment details by id or detailUrl.")
    parser.add_argument("property_id", nargs="?", help="Zillow property/building id or detailUrl.")
    parser.add_argument("--no-proxy", action="store_true", help="Make one direct request without proxies.txt.")
    return parser.parse_args()


def get_property_id(property_id=None):
    if property_id:
        return property_id.strip()

    return input("Enter Zillow property/building id or detailUrl: ").strip()


def main():
    args = parse_args()
    proxy_urls = [] if args.no_proxy else load_proxy_urls()
    property_id = get_property_id(args.property_id)

    if not property_id:
        print("No property id provided.")
        return

    property_url = resolve_property_url(property_id)
    print(f"Resolved URL: {property_url}")

    if args.no_proxy:
        print("Proxy disabled by --no-proxy. Using normal connection.")
    elif proxy_urls:
        print(f"Loaded {len(proxy_urls)} proxies from {PROXY_FILE}.")
    else:
        print("No proxies loaded. Using normal connection.")

    try:
        print(f"Requesting details for property id: {property_id}")

        details = run_with_proxy_retry(
            "property id details",
            lambda proxy_url: get_from_department_url(property_url, proxy_url),
            proxy_urls,
        )

        print("Details keys:", list(details.keys())[:20])
        save_json("details.json", details)

    except Exception as exc:
        print("Request failed.")
        print(type(exc).__name__, exc)
        print(
            "If you see 403, 429, CAPTCHA, JSON parse errors, or empty data, "
            "stop testing for now rather than retrying repeatedly."
        )


if __name__ == "__main__":
    main()
