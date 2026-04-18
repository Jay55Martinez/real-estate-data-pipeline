import requests
import json
import csv
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

    # Save to JSON file in example data folder
    output_dir = Path(__file__).parent / 'example data'
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f'property_data_{parcel_id}.json'
    with open(output_file, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"Fetched data for {parcel_id}, status: {response.status_code}")

if __name__ == '__main__':
    # Path to the CSV file
    csv_path = Path(__file__).parent.parent / 'Analyze Boston' / 'sample_listing_02128.csv'
    
    with open(csv_path, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            parcel_id = row['PID'].strip()
            if parcel_id:  # Only fetch if PID is not empty
                fetch_property_data(parcel_id)