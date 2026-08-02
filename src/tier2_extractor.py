import pandas as pd
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import time

# Set up standard paths
BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_DIR = BASE_DIR / "results"
TIER1_FILE = RESULTS_DIR / "extraction_results_tier1.csv"

def run_tier2_handoff():
    print("Loading Tier 1 results...")
    
    # 1. Load the Tier 1 CSV
    try:
        df = pd.read_csv(TIER1_FILE)
    except FileNotFoundError:
        print(f"Error: Could not find {TIER1_FILE}. Run Tier 1 first.")
        return
        
    # 2. Filter for only the MISS rows
    miss_df = df[df["winning_tier"] == "MISS"].copy()
    
    print(f"Found {len(miss_df)} missing metrics to process in Tier 2.")
    
    # 3. Group by document to only download each HTML file once
    unique_docs = miss_df.drop_duplicates(subset=["cik", "document_fiscal_year"])
    print(f"This requires fetching {len(unique_docs)} unique 10-K HTML documents.")
    
    return miss_df, unique_docs

if __name__ == "__main__":
    run_tier2_handoff()