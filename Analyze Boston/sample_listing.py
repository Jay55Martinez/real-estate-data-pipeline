import os
import pandas as pd

"""
Sample listing of 30 properties in zip code 02128 from the full dataset. This is used for testing and demonstration purposes, allowing us to work with a manageable subset of the data while developing our analysis and visualizations.
"""

DATA_FILE = os.path.join(os.path.dirname(__file__), "fy2026-property-assessment-data_12_23_2025.csv")
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "fy2026_listing_02128.csv")
ZIP_CODE = "02128"


def main() -> None:
    df = pd.read_csv(DATA_FILE, dtype=str)
    filtered = df[df["ZIP_CODE"] == ZIP_CODE] if "ZIP_CODE" in df.columns else df[df["zip_code"] == ZIP_CODE]

    if filtered.empty:
        raise SystemExit(f"No rows found for zip code {ZIP_CODE}.")

    filtered.to_csv(OUTPUT_FILE, index=False)

    print(f"Loaded {len(df)} rows from {DATA_FILE}.")
    print(f"Found {len(filtered)} rows with zip code {ZIP_CODE}.")
    print(f"Saved {len(filtered)} rows to {OUTPUT_FILE}.")


if __name__ == "__main__":
    main()

