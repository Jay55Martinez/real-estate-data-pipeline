import requests
import time

def solve_captcha(site_key, page_url, api_key):
    """
    Submit a CAPTCHA challenge to 2Captcha and fetch the solution after a fixed delay.

    Args:
        site_key (str): The reCAPTCHA site key from Zillow.
        page_url (str): The URL of the Zillow page containing the CAPTCHA.
        api_key (str): Your 2Captcha API key.

    Returns:
        str: The CAPTCHA solution token.

    Raises:
        Exception: If submission fails or the solution is not ready.
    """
    # Step 1: Submit the CAPTCHA to 2Captcha for solving
    submit_url = "http://2captcha.com/in.php"
    payload = {
        'key': api_key,
        'method': 'userrecaptcha',
        'googlekey': site_key,
        'pageurl': page_url,
        'json': 1
    }
    submit_response = requests.post(submit_url, data=payload)
    submit_result = submit_response.json()

    if submit_result.get('status') != 1:
        raise Exception("CAPTCHA submission failed: " + submit_result.get('request', 'Unknown error'))
    
    captcha_id = submit_result['request']
    print(f"CAPTCHA submitted successfully, ID: {captcha_id}")

    # Step 2: Wait for a fixed time period to allow 2Captcha to solve the CAPTCHA
    time.sleep(25)  # Wait 25 seconds

    # Step 3: Fetch the CAPTCHA solution
    fetch_url = f"http://2captcha.com/res.php?key={api_key}&action=get&id={captcha_id}&json=1"
    result_response = requests.get(fetch_url)
    result = result_response.json()
    
    if result.get('status') == 1:
        print("CAPTCHA solved successfully.")
        return result.get('request')
    else:
        raise Exception("CAPTCHA not solved in time or an error occurred:" + result.get('request', 'Unknown error'))

# Replace with your actual Zillow reCAPTCHA site key, the Zillow page URL, and your 2Captcha API key.
site_key = "YOUR_ZILLOW_SITE_KEY"
page_url = "https://www.zillow.com/homedetails/1234-Main-St-Some-City-CA-90210/12345678_zpid/"
api_key = "YOUR_2CAPTCHA_API_KEY"

try:
    captcha_solution = solve_captcha(site_key, page_url, api_key)
    print("CAPTCHA Solution Token:", captcha_solution)
except Exception as e:
    print("Error solving CAPTCHA:", e)