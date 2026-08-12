import os
import sys
import time
import pandas as pd
import requests

# 1. Dynamically find the project root directory and src directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")

# Ensure Python can import modules from the src folder
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

from tier_3_llm import get_sec_10k_url, clean_html_to_text, chunk_text, build_vector_database, create_extraction_prompt, query_phi3_mini

HEADERS = {
    "User-Agent": "WoosterDataProject student@email.com",  # ADD REAL EMAIL HERE
    "Accept-Encoding": "gzip, deflate"
}

def process_tier3_batch():
    print("Loading Tier 2 results...")
    
    # 2. Build absolute paths to the results folder
    input_csv = os.path.join(BASE_DIR, "results", "tier2_results.csv")
    output_csv = os.path.join(BASE_DIR, "results", "final_extraction_results.csv")
    
    # Safety check to ensure the file is found
    if not os.path.exists(input_csv):
        print(f"Error: Could not find {input_csv}")
        print("Please check that 'tier2_results.csv' is inside the 'results' folder.")
        return

    df = pd.read_csv(input_csv)
    
    # Identify rows that need Tier 3 processing
    missing_mask = df['extracted_value'].isin(['MISS', 'DOWNLOAD_FAILED', 'invalid_url', 'METRIC_ROW_NOT_FOUND', 'UNKNOWN']) | df['extracted_value'].isna()
    tier3_targets = df[missing_mask]
    
    print(f"Found {len(tier3_targets)} rows requiring Tier 3 LLM Extraction.\n")
    
    for index, row in tier3_targets.iterrows():
        # Keys updated to perfectly match your CSV column names
        cik = row['cik']
        year = row['metric_fiscal_year']
        metric = row['metric_name']
        
        print(f"--- Processing CIK: {cik} | Year: {year} | Metric: {metric} ---")
        
        # 1. Recover URL
        url = get_sec_10k_url(cik, year)
        if url in ["NO_FILING_EXISTS", "NO_FILINGS_FOUND", "API_ERROR"]:
            df.at[index, 'extracted_value'] = url
            df.at[index, 'winning_tier'] = "TIER_3_URL"
            continue
            
        # 2. Download and Process
        try:
            time.sleep(0.15)
            response = requests.get(url, headers=HEADERS, timeout=15)
            clean_text = clean_html_to_text(response.text)
            chunks = chunk_text(clean_text)
            db_collection = build_vector_database(chunks, cik, year)
            
            # 3. Retrieve and Extract
            results = db_collection.query(
                query_texts=[f"Total consolidated {metric} for the entire fiscal year {year}"],
                n_results=3
            )
            
            retrieved_paragraphs = results['documents'][0]
            final_prompt = create_extraction_prompt(retrieved_paragraphs, metric, year)
            extracted_value = query_phi3_mini(final_prompt)
            
            # 4. Save back to dataframe
            df.at[index, 'extracted_value'] = extracted_value
            df.at[index, 'winning_tier'] = "TIER_3_LLM"
            
        except Exception as e:
            print(f"  -> Pipeline Error: {e}")
            df.at[index, 'extracted_value'] = "TIER_3_FAILED"
            
        print("\n")
        
    print("Saving final dataset...")
    df.to_csv(output_csv, index=False)
    print(f"Pipeline Complete! Results saved to {output_csv}")

if __name__ == "__main__":
    process_tier3_batch()