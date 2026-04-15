import requests
import json
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def fetch_property_data(address):
    """
    Fetch property data from RentCast API and save to JSON file.
    
    Args:
        address (str): Property address to query
    """
    # Get API key from environment
    api_key = os.getenv('Rent_Cast_API_KEY')
    
    if not api_key:
        raise ValueError("Rent_Cast_API_KEY not found in .env file")
    
    # Prepare API request
    url = 'https://api.rentcast.io/v1/properties'
    headers = {
        'Accept': 'application/json',
        'X-Api-Key': api_key
    }
    params = {
        'address': address
    }
    
    # Make API request
    print(f"Fetching data for: {address}")
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()  # Raise exception for bad status codes
    
    # Parse JSON response
    data = response.json()
    
    # Save to JSON file
    output_file = Path(__file__).parent / 'property_data.json'
    with open(output_file, 'w') as f:
        json.dump(data, f, indent=2)
    
    print(f"Data saved to: {output_file}")
    return data

if __name__ == '__main__':
    # Example usage
    address = '258 Lexington Street, East Boston, MA, 02128'
    fetch_property_data(address)
