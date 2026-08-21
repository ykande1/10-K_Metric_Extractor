import pandas as pd
from pathlib import Path
import zipfile
import json
import requests
import time
import re
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_DIR = BASE_DIR / "results"
DATA_DIR = BASE_DIR / "data" / "10k_section"
TIER1_FILE = RESULTS_DIR / "extraction_results_tier1.csv"

HEADERS = {
    "User-Agent": "WoosterDataProject student@email.com" #ADD REAL EMAIL HERE
}

# Standardize and prioritize the variations in SEC metric names
METRIC_MAPPING = {
    "SalesRevenueNet": [
        "total revenues", "total net revenues", "total net sales", 
        "net revenues", "net sales", "sales", 
        "collaboration revenue", "revenue"
    ],
    "NetIncomeLoss": [
        "net income (loss) attributable", "net loss attributable", "net income attributable",
        "net income (loss)", "net loss", "net income"
    ],
    "ResearchAndDevelopment": [
        "total research and development", "research and development expenses", 
        "research and development", "r&d"
    ]
}


def get_document_content(cik: int, edgar_url: str):
    """Bypasses local JSON and forces download directly from SEC EDGAR."""
    if not isinstance(edgar_url, str) or edgar_url.strip().upper() == "UNKNOWN":
        print(f"  -> ERROR: Invalid or missing URL ('{edgar_url}') for CIK {cik}. Skipping download.")
        return None, "invalid_url"
            
    print("  -> Bypassing local files. Downloading directly from SEC...")
    try:
        time.sleep(0.15)
        response = requests.get(edgar_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        
        # If the URL is just the index page, we need to find the real 10-K document link
        if "-index.htm" in edgar_url:
            soup = BeautifulSoup(response.text, 'html.parser')
            for tr in soup.find_all('tr'):
                tds = tr.find_all('td')
                if len(tds) > 3 and '10-K' in tds[3].text:
                    a_tag = tr.find('a')
                    if a_tag:
                        real_url = "https://www.sec.gov" + a_tag['href']
                        print(f"  -> Resolving index page to real 10-K: {real_url}")
                        time.sleep(0.15)
                        real_response = requests.get(real_url, headers=HEADERS, timeout=15)
                        return real_response.text, "sec_html"
                        
        return response.text, "sec_html"
    except Exception as e:
        print(f"  -> SEC download failed: {e}")
        return None, "error"
    

def parse_financial_tables(content, source):
    """Uses BeautifulSoup to find all tables in the HTML document."""
    html_string = ""
    
    if source == "local_json":
        html_string = content.get("item_8", "") 
    elif source == "sec_html":
        html_string = content
        
    if not html_string:
        return []
        
    print("  -> Parsing HTML structure...")
    soup = BeautifulSoup(html_string, 'html.parser')
    tables = soup.find_all('table')
    print(f"  -> Found {len(tables)} tables in the document.")
    return tables


def find_income_statement_table(tables):
    """Scans all tables and returns the most likely Income Statement."""
    candidate_tables = []
    
    for i, table in enumerate(tables):
        table_text = table.get_text(separator=' ', strip=True).lower()
        
        # Check for our top-line and bottom-line keywords
        if "net income" in table_text or "net loss" in table_text:
            if "revenue" in table_text or "sales" in table_text:
                # Explicitly exclude the Cash Flow statement
                if "cash flows from" not in table_text:
                    rows = table.find_all('tr')
                    if len(rows) > 10:
                        candidate_tables.append({
                            "index": i,
                            "table": table,
                            "text_length": len(table_text)
                        })
                    
    print(f"  -> Filtered down to {len(candidate_tables)} potential Income Statement tables.")
    
    if candidate_tables:
        candidate_tables = sorted(candidate_tables, key=lambda x: x["text_length"], reverse=True)
        return candidate_tables[0]["table"]
        
    return None


def extract_metric_value(table, metric_name, target_year):
    """Finds the correct row and aligns pure numeric data arrays to pure year arrays."""
    rows = table.find_all('tr')
    target_year_idx = -1
    multiplier = 1 
    
    # 1. Sweep headers for the target year AND detect the reporting scale
    for row in rows[:15]:
        row_text_raw = row.get_text(separator=' ', strip=True).lower()
        if "in millions" in row_text_raw:
            multiplier = 1000000
        elif "in thousands" in row_text_raw:
            multiplier = 1000
        elif "in billions" in row_text_raw:
            multiplier = 1000000000
            
        cell_texts = [re.sub(r'\s+', ' ', cell.get_text(strip=True)) for cell in row.find_all(['th', 'td']) if cell.get_text(strip=True) and cell.get_text(strip=True) != '$']
        
        year_cols = []
        for text in cell_texts:
            if re.search(r'\b(201\d|202\d)\b', text):
                year_cols.append(text)
                
        if year_cols:
            for idx, y_text in enumerate(year_cols):
                if str(target_year) in y_text:
                    target_year_idx = idx
                    break
                    
            if target_year_idx == -1:
                for idx, y_text in enumerate(year_cols):
                    if str(target_year + 1) in y_text:
                        target_year_idx = idx
                        break
                        
            if target_year_idx != -1:
                break
                
    if target_year_idx == -1:
        return "YEAR_NOT_FOUND"

    # 2. Pre-parse all valid data rows so we can scan them multiple times
    parsed_rows = []
    for row in rows[5:]: 
        cell_texts = [cell.get_text(strip=True) for cell in row.find_all(['td', 'th']) if cell.get_text(strip=True) and cell.get_text(strip=True) != '$']
        if cell_texts:
            parsed_rows.append(cell_texts)

    # 3. Find the row using strict priority alias matching
    aliases = METRIC_MAPPING.get(metric_name, [metric_name.lower()])
    
    for alias in aliases:
        for cell_texts in parsed_rows:
            row_header = cell_texts[0].lower()
            
            # Check if our current priority alias is in this row's header
            if alias in row_header:
                
                # --- Block false-positive accounting traps ---
                poison_words = [
                    "before", "per share", "basic", "diluted", 
                    "comprehensive", "in-process", "purchased"
                ]
                if any(poison in row_header for poison in poison_words):
                    continue # Skip this row!
                # ---------------------------------------------------------------
                
                data_cells = []
                for cell in cell_texts[1:]:
                    clean_cell = re.sub(r'\([a-zA-Z0-9]\)$', '', cell.strip())
                    clean_cell = clean_cell.replace(',', '').strip()
                    
                    if re.search(r'\d', clean_cell) or clean_cell in ['-', '—']:
                        if '%' not in clean_cell:
                            data_cells.append(clean_cell)
                
                if 0 <= target_year_idx < len(data_cells):
                    raw_value = data_cells[target_year_idx]
                    clean_val = raw_value
                    is_negative = False
                    
                    if clean_val.startswith('(') and clean_val.endswith(')'):
                        clean_val = clean_val[1:-1]
                        is_negative = True
                    elif clean_val.startswith('-') or clean_val.startswith('—'):
                        clean_val = clean_val[1:]
                        if not clean_val: 
                            return 0
                        is_negative = True
                        
                    try:
                        numeric_val = float(clean_val)
                        scaled_val = numeric_val * multiplier
                        if is_negative:
                            scaled_val = -scaled_val
                        return int(scaled_val)
                    except ValueError:
                        return raw_value
                        
    return "METRIC_ROW_NOT_FOUND"

def run_tier2_extraction():
    print("Loading Tier 1 results...")
    try:
        df = pd.read_csv(TIER1_FILE)
    except FileNotFoundError:
        print("Error: Run Tier 1 first.")
        return

    df["extracted_value"] = df["extracted_value"].astype(object)
    missing_indices = df[df["winning_tier"] == "MISS"].index
    print(f"Found {len(missing_indices)} missing metrics to extract. Starting Tier 2 Pipeline...\n")
    
    for idx in missing_indices: 
        row = df.loc[idx]
        cik = row['cik']
        year = row['document_fiscal_year']
        url = row['edgar_url']
        target_metric = row['metric_name']
        
        print(f"Processing CIK: {cik}, Year: {year} | Looking for: {target_metric}")
        content, source = get_document_content(cik, url)
        
        if content:
            tables = parse_financial_tables(content, source)

            if tables:
                income_table = find_income_statement_table(tables)
                if income_table:
                    extracted_value = extract_metric_value(income_table, target_metric, year)
                    print(f"  -> EXTRACTED VALUE: {extracted_value}\n")
                    
                    df.at[idx, "extracted_value"] = extracted_value
                    df.at[idx, "winning_tier"] = "TIER_2"
                else:
                    print("  -> ERROR: Could not isolate the Income Statement.\n")
                    df.at[idx, "extracted_value"] = "TABLE_NOT_FOUND"
            else:
                print("  -> ERROR: No financial tables parsed.\n")
                df.at[idx, "extracted_value"] = "NO_TABLES"
        else:
            print("  -> ERROR: Download failed.\n")
            df.at[idx, "extracted_value"] = "DOWNLOAD_FAILED"

        if source == "sec_html":
            time.sleep(0.15)

    output_file = "results/tier2_results.csv"
    
    import os
    os.makedirs("data", exist_ok=True)
    
    df.to_csv(output_file, index=False)
    print(f"Extraction batch complete! Results successfully saved to {output_file}")

if __name__ == "__main__":
    run_tier2_extraction()