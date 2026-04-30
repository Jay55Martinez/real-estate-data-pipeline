import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin
from captcha_by_pass import solve_captcha

import pyzill
from pyzill.details import headers, parse_body_deparments, requests

ZILLOW_BASE_URL = "https://www.zillow.com"
ZILLOW_PATH = "/apartments/east-boston-ma/brandywyne-village/5XjK8K/"
ZILLOW_URL = urljoin(ZILLOW_BASE_URL, ZILLOW_PATH)

SCRIPT_DIR = Path(__file__).parent
PROXY_FILE = SCRIPT_DIR / "proxies.txt"
OUTPUT_DIR = SCRIPT_DIR / "pyzill_test_output"
OUTPUT_DIR.mkdir(exist_ok=True)


def load_proxies():
    proxies = []

    for line_number, raw_line in enumerate(PROXY_FILE.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        parts = line.split(":")
        if len(parts) != 4:
            print(f"Skipping malformed proxy on line {line_number}: {line}")
            continue

        username, password, host, port = parts
        proxies.append(pyzill.parse_proxy(host, port, username, password))

    if not proxies:
        raise ValueError(f"No valid proxies found in {PROXY_FILE}")

    return proxies


def save_json(filename, data):
    path = OUTPUT_DIR / filename
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Saved {path}")


def save_failed_response(response):
    path = OUTPUT_DIR / "failed_response.html"
    path.write_bytes(response.content[:500_000])
    print(f"Saved failed response preview to {path}")


def request_zillow_details(proxy_url):
    proxy_settings = {"http": proxy_url, "https": proxy_url}
    response = requests.get(
        url=ZILLOW_URL,
        headers=headers,
        proxies=proxy_settings,
        impersonate="chrome124",
    )

    # Bypassing the CAPTCHA if detected
    if response.status_code == 403 and "captcha" in response.text.lower():
        print("CAPTCHA detected, attempting to solve...")
        site_key = None
        base_url = ZILLOW_BASE_URL
    
        # Extract site_key from response
        match = re.search(r'"siteKey"\s*:\s*"([^"]+)"', response.text)
        if match:
            site_key = match.group(1)
        else:
            # Fallback for PerimeterX responses that expose the app id or captcha script path
            match = re.search(r"window\._pxAppId\s*=\s*['\"]([^'\"]+)['\"]", response.text)
            if match:
                site_key = match.group(1)
            else:
                match = re.search(r"/([A-Za-z0-9]+)/captcha/captcha\.js", response.text)
                if match:
                    site_key = match.group(1)

        if site_key:
            print(f"Found site key: {site_key}")
            print(f"Attempting to solve CAPTCHA with 2Captcha API key: {TWO_CAPTCHA_API_KEY[:4]}***")
            captcha_solution = solve_captcha(site_key, ZILLOW_URL, TWO_CAPTCHA_API_KEY)
            print(f"CAPTCHA solution obtained: {captcha_solution}")

            # Retry the request with the CAPTCHA solution
            response = requests.get(
                url=ZILLOW_URL,
                headers={**headers, "X-Captcha-Solution": captcha_solution},
                proxies=proxy_settings,
                impersonate="chrome124",
            )
        else:
            print("Could not find site key in the response, cannot solve CAPTCHA.")
            save_failed_response(response)
            raise ValueError("CAPTCHA detected but site key not found")

    if response.status_code >= 400:
        save_failed_response(response)
        raise ValueError(f"Zillow returned HTTP {response.status_code}")

    details = parse_body_deparments(response.content)
    if details is None:
        save_failed_response(response)
        raise ValueError("Could not parse Zillow details from the response")

    return details


def main():
    proxies = load_proxies()
    print(f"Requesting {ZILLOW_URL}")
    print(f"Loaded {len(proxies)} proxies from {PROXY_FILE}")

    last_error = None

    for attempt_number, proxy_url in enumerate(proxies, start=1):
        try:
            print(f"Attempt {attempt_number}/{len(proxies)} using proxy")
            details = request_zillow_details(proxy_url)
            print("Details keys:", list(details.keys())[:20])
            save_json("details.json", details)
            return
        except Exception as exc:
            last_error = exc
            print(f"Attempt {attempt_number} failed: {type(exc).__name__}: {exc}")

            if attempt_number < len(proxies):
                print("Trying next proxy...")
                time.sleep(2)

    raise last_error


if __name__ == "__main__":
    main()
