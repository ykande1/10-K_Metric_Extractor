import os
import sys
import time
import random
import pandas as pd
import requests

# 1. Dynamically find the project paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

# Import Tier 3 functions
from tier_3_llm import get_sec_10k_url, clean_html_to_text, chunk_text, build_vector_database, create_extraction_prompt, query_phi3_mini

# Import Tier 1 classes and functions
from tier1_extractor import Tier1Extractor

# Import Tier 2 functions
from tier2_extractor import get_document_content, parse_financial_tables, find_income_statement_table, extract_metric_value

HEADERS = {
    "User-Agent": "WoosterDataProject student@wooster.edu",
    "Accept-Encoding": "gzip, deflate"
}

def run_end_to_end_test():
    print("Loading original XBRL dataset...")
    
    # 2. Build absolute paths
    input_csv = os.path.join(BASE_DIR, "data", "xbrl.csv")
    output_csv = os.path.join(BASE_DIR, "results", "end_to_end_verification_sample.csv")
    
    if not os.path.exists(input_csv):
        print(f"Error: Could not find {input_csv}")
        return

    # Load and standardize columns to prevent KeyError issues
    df = pd.read_csv(input_csv)
    df.columns = df.columns.str.lower().str.strip()
    
    # 3. Sample 5 random cases for a quick test run
    sample_size = min(5, len(df))
    test_sample = df.sample(n=sample_size, random_state=42)
    print(f"Randomly selected {sample_size} cases.\n")
    
    # Initialize Tier 1 Extractor (Loads the mapping and XBRL data once)
    print("Initializing Tier 1 Cache...")
    t1_extractor = Tier1Extractor()
    t1_extractor.load_resources()
    print("\n")

    verification_data = []
    
    for current_step, (index, row) in enumerate(test_sample.iterrows(), start=1):
        # Map variables using the actual lowercased XBRL column names
        raw_cik = row.get('entitycentralindexkey', 0)
        raw_year = row.get('documentfiscalyearfocus', 0)
        raw_name = row.get('entityregistrantname', 'UNKNOWN_COMPANY')
        
        cik = int(raw_cik) if pd.notna(raw_cik) else "UNKNOWN_CIK"
        metric_year = int(raw_year) if pd.notna(raw_year) else "UNKNOWN_METRIC_YEAR"
        company_name = str(raw_name) if pd.notna(raw_name) else f"Company_{cik}"
        doc_year = metric_year 
        
        # Randomly assign one of the three target metrics
        target_metrics = ["SalesRevenueNet", "NetIncomeLoss", "ResearchAndDevelopment"]
        metric = random.choice(target_metrics)
        
        print(f"--- Testing {current_step}/{sample_size} (CSV Row: {index}) | {company_name} (CIK: {cik}) | Year: {metric_year} | Metric: {metric} ---")
        
        extracted_value = "MISS"
        winning_tier = "NONE"
        url = get_sec_10k_url(cik, metric_year)
        
        # --- TIER 1: Local Structured Cache ---
        print("  -> Running Tier 1...")
        metric_config = t1_extractor.metrics_config.get(metric, {})
        xbrl_col = metric_config.get("xbrl_column")

        if xbrl_col:
            res = t1_extractor.extract_single_metric(cik, "N/A", company_name, metric_year, metric, xbrl_col)
            if res.winning_tier == "TIER_1_XBRL":
                extracted_value = res.extracted_value
                winning_tier = "tier_1_cache"
        
        # --- TIER 2: HTML Scraping ---
        if winning_tier == "NONE" and url not in ["NO_FILING_EXISTS", "NO_FILINGS_FOUND", "API_ERROR"]:
            print("  -> Running Tier 2...")
            content, source = get_document_content(cik, url)
            if content:
                tables = parse_financial_tables(content, source)
                if tables:
                    income_table = find_income_statement_table(tables)
                    if income_table:
                        val = extract_metric_value(income_table, metric, metric_year)
                        if val not in ["METRIC_ROW_NOT_FOUND", "YEAR_NOT_FOUND", "TABLE_NOT_FOUND"]:
                            extracted_value = val
                            winning_tier = "tier_2_scraper"
                        else:
                            extracted_value = val 
                    else:
                        extracted_value = "TABLE_NOT_FOUND"
                else:
                    extracted_value = "NO_TABLES"
            else:
                extracted_value = "DOWNLOAD_FAILED"

        # --- TIER 3: LLM RAG Engine ---
        if winning_tier == "NONE":
            if url in ["NO_FILING_EXISTS", "NO_FILINGS_FOUND", "API_ERROR"]:
                extracted_value = url
                winning_tier = "api_check_failed"
            else:
                print("  -> Running Tier 3 (Phi-3 Mini)...")
                try:
                    time.sleep(0.15)
                    response = requests.get(url, headers=HEADERS, timeout=15)
                    clean_text = clean_html_to_text(response.text)
                    chunks = chunk_text(clean_text)
                    db_collection = build_vector_database(chunks, cik, metric_year)
                    
                    results = db_collection.query(
                        query_texts=[f"Total consolidated {metric} for the entire fiscal year {metric_year}"],
                        n_results=3
                    )
                    
                    final_prompt = create_extraction_prompt(results['documents'][0], metric, metric_year)
                    extracted_value = query_phi3_mini(final_prompt)
                    winning_tier = "tier_3_llm"
                    
                except Exception as e:
                    print(f"  -> Tier 3 Error: {e}")
                    extracted_value = "TIER_3_FAILED"
                    winning_tier = "failed"
        
        print(f"  Result: {extracted_value} ({winning_tier})\n")
        
        # Append clean data for manual review
        verification_data.append({
            "cik": cik,
            "company_name": company_name,
            "document_fiscal_year": doc_year,
            "metric_fiscal_year": metric_year,
            "metric_name": metric,
            "extracted_value": extracted_value,
            "winning_tier": winning_tier,
            "edgar_url": url,
            "verified_correct?": "" 
        })
        
    # Save the final sample to a new CSV
    verify_df = pd.DataFrame(verification_data)
    verify_df.to_csv(output_csv, index=False)
    print(f"Test Complete! Open {output_csv} to begin manual verification.")

if __name__ == "__main__":
    run_end_to_end_test()