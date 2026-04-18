import requests
import json
from pathlib import Path

def fetch_property_data(parcel_id):
    """
    Fetch property data from Boston's API using the parcel ID and save to JSON file.
    
    Args:
        parcel_id (str): Parcel ID to query
    """
    url = "https://us-central1-assessing-properties-prod.cloudfunctions.net/fetchPropertyDetailsByParcelId"

    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Origin": "https://properties.boston.gov",
        "Referer": "https://properties.boston.gov/",
        "User-Agent": "Mozilla/5.0"
    }

    payload = {
        "data":{
        "parcelId": parcel_id, # <-- Change PID to fetch different property
        "date": "2026-04-18"
        }
    }

    response = requests.post(url, json=payload, headers=headers)
    data = response.json()

    # Save to JSON file
    output_file = Path(__file__).parent / f'property_data_{parcel_id}.json'
    with open(output_file, 'w') as f:
        json.dump(data, f, indent=2)

    print(response.status_code)

if __name__ == '__main__':
    # Example usage
    parcel_id = '0100254000'

    
    fetch_property_data(parcel_id)