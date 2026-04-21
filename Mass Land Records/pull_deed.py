import sys
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

# Function to get deed data for an address
def get_deed_data(street_number, street_name, date_from="01/01/1900", date_to="12/31/2025"):
    # Set up Chrome options
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # Run in background
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument("--disable-web-security")
    chrome_options.add_argument("--allow-running-insecure-content")

    # Initialize the driver
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    
    # Execute script to remove webdriver property
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    try:
        url = "https://www.masslandrecords.com/Suffolk/D/Default.aspx"
        driver.get(url)

        # Wait for the page to load and check if form elements exist
        try:
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.ID, "SearchFormEx1_ACSTextBox_StreetNumber"))
            )
        except Exception as e:
            print(f"Page title: {driver.title}")
            print(f"Current URL: {driver.current_url}")
            print("Timeout waiting for form element. Checking available form elements...")
            # Print all input elements
            inputs = driver.find_elements(By.TAG_NAME, "input")
            print(f"Found {len(inputs)} input elements:")
            for inp in inputs:
                print(f"  ID: {inp.get_attribute('id')}, Name: {inp.get_attribute('name')}, Type: {inp.get_attribute('type')}")
            
            # Print all select elements
            selects = driver.find_elements(By.TAG_NAME, "select")
            print(f"Found {len(selects)} select elements:")
            for sel in selects:
                print(f"  ID: {sel.get_attribute('id')}, Name: {sel.get_attribute('name')}")
            
            # Print all button elements
            buttons = driver.find_elements(By.TAG_NAME, "button")
            print(f"Found {len(buttons)} button elements:")
            for btn in buttons:
                print(f"  ID: {btn.get_attribute('id')}, Text: {btn.text}")
            
            raise e

        # Fill in the search form
        driver.find_element(By.ID, "SearchFormEx1_ACSTextBox_StreetNumber").send_keys(street_number)
        driver.find_element(By.ID, "SearchFormEx1_ACSTextBox_StreetName").send_keys(street_name)
        driver.find_element(By.ID, "SearchFormEx1_ACSTextBox_DateFrom").send_keys(date_from)
        driver.find_element(By.ID, "SearchFormEx1_ACSTextBox_DateTo").send_keys(date_to)

        # Select "Recorded Land Property Search"
        search_type = driver.find_element(By.ID, "SearchCriteriaName1_DDL_SearchName")
        for option in search_type.find_elements(By.TAG_NAME, "option"):
            if "Recorded Land Property Search" in option.text:
                option.click()
                break

        # Click search button
        search_button = driver.find_element(By.ID, "SearchFormEx1_btnSearch")
        search_button.click()

        # Wait for results
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.ID, "SearchResultsGrid1"))
        )

        # Get the page source (contains the results)
        page_source = driver.page_source

        return page_source

    finally:
        driver.quit()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python pull_deed.py <street_number> <street_name> [date_from] [date_to]")
        print("Example: python pull_deed.py 258 'Lexington St' '12/13/1972' '04/20/2026'")
        sys.exit(1)

    street_number = sys.argv[1]
    street_name = sys.argv[2]
    date_from = sys.argv[3] if len(sys.argv) > 3 else "01/01/1900"
    date_to = sys.argv[4] if len(sys.argv) > 4 else "12/31/2025"

    result = get_deed_data(street_number, street_name, date_from, date_to)
    print(result)