import pandas as pd
from pathlib import Path
import zipfile
import json
import requests
import time
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_DIR = BASE_DIR / "results"
DATA_DIR = BASE_DIR / "data" / "10k_section"
TIER1_FILE = RESULTS_DIR / "extraction_results_tier1.csv"

#PLACEHOLDER HEADER: Add logging configuration here:
HEADERS = {
    #"User-Agent": "College Research Student/1.0 (placeholder@email.com)"
}

import re

# Standardize the variations in SEC metric names
METRIC_MAPPING = {
    "SalesRevenueNet": ["revenue", "net sales", "total revenues", "sales"],
    "NetIncomeLoss": ["net income", "net loss", "net income (loss)"],
    "ResearchAndDevelopment": ["research and development", "r&d", "research and development expenses"]
}


def get_document_content(cik: int, edgar_url: str):
    """Tries local JSON, falls back to SEC EDGAR, and resolves index pages."""
    cik_folder = DATA_DIR / str(cik)
    zip_path = cik_folder / f"{cik}_10k_section_html.json"
    
    # Route 1: Local File
    if zip_path.exists():
        print(f"  -> Found local file for CIK {cik}. Cracking zip...")
        try:
            with zipfile.ZipFile(zip_path, 'r') as z:
                inner_filename = z.namelist()[0]
                with z.open(inner_filename) as f:
                    return json.load(f), "local_json"
        except Exception as e:
            print(f"  -> Error reading local zip: {e}")
            
    # Route 2: SEC Downloader
    print(f"  -> Local folder missing. Downloading from SEC...")
    try:
        time.sleep(0.15)
        response = requests.get(edgar_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        
        # If the URL is just the index page, we need to find the real 10-K document link
        if "-index.htm" in edgar_url:
            soup = BeautifulSoup(response.text, 'html.parser')
            for tr in soup.find_all('tr'):
                # Look for the row containing the 10-K document
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
    
    # If it's local JSON, we extract just the Item 8 HTML string
    if source == "local_json":
        # We will adjust this key if your JSON uses a different naming convention
        html_string = content.get("item_8", "") 
    
    # If it's the raw SEC HTML, we parse the whole document
    elif source == "sec_html":
        html_string = content
        
    if not html_string:
        return []
        
    print("  -> Parsing HTML structure...")
    soup = BeautifulSoup(html_string, 'lxml')
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
    """Finds the correct row and column, detects scale, and extracts the true numerical value."""
    rows = table.find_all('tr')
    offset_from_right = -1
    multiplier = 1  # Default to exact numbers if no scale is found
    
    # 1. Sweep headers for the target year AND detect the reporting scale
    for row in rows[:15]:
        # Grab the entire row's text as one lowercase string to search for scale keywords
        row_text_raw = row.get_text(separator=' ', strip=True).lower()
        
        if "in millions" in row_text_raw:
            multiplier = 1000000
        elif "in thousands" in row_text_raw:
            multiplier = 1000
        elif "in billions" in row_text_raw:
            multiplier = 1000000000
            
        # Standard cell extraction for year alignment
        cell_texts = [re.sub(r'\s+', ' ', cell.get_text(strip=True)) for cell in row.find_all(['th', 'td']) if cell.get_text(strip=True) and cell.get_text(strip=True) != '$']
        
        for idx, text in enumerate(cell_texts):
            if str(target_year) in text or str(target_year + 1) in text:
                if offset_from_right == -1: # Only set once
                    offset_from_right = len(cell_texts) - idx
                
    if offset_from_right == -1:
        return "YEAR_NOT_FOUND"

    # 2. Find the row for the target metric
    aliases = METRIC_MAPPING.get(metric_name, [metric_name.lower()])
    
    for row in rows[5:]: 
        cell_texts = [cell.get_text(strip=True) for cell in row.find_all(['td', 'th']) if cell.get_text(strip=True) and cell.get_text(strip=True) != '$']
        
        if not cell_texts:
            continue
            
        row_header = cell_texts[0].lower()
        
        # Check if the row matches our metric aliases
        if any(alias in row_header for alias in aliases):
            target_idx = len(cell_texts) - offset_from_right
            
            if 0 < target_idx < len(cell_texts):
                raw_value = cell_texts[target_idx]
                
                # Clean up formatting
                clean_val = raw_value.replace(',', '')
                is_negative = False
                
                # Handle standard negative accounting formats
                if clean_val.startswith('(') and clean_val.endswith(')'):
                    clean_val = clean_val[1:-1]
                    is_negative = True
                elif clean_val.startswith('-'):
                    clean_val = clean_val[1:]
                    is_negative = True
                    
                # Convert to math variable, scale it, and return true dollar amount
                try:
                    numeric_val = float(clean_val)
                    scaled_val = numeric_val * multiplier
                    
                    if is_negative:
                        scaled_val = -scaled_val
                        
                    return int(scaled_val)
                    
                except ValueError:
                    # Fallback if a cell contains a non-numeric character like a dash '-'
                    return raw_value
                
    return "METRIC_ROW_NOT_FOUND"


def test_hybrid_router():
    print("Loading Tier 1 results...")
    try:
        df = pd.read_csv(TIER1_FILE)
    except FileNotFoundError:
        print("Error: Run Tier 1 first.")
        return
        
    miss_df = df[df["winning_tier"] == "MISS"].copy()
    unique_docs = miss_df.drop_duplicates(subset=["cik", "document_fiscal_year"])
    
    for _, row in unique_docs.head(2).iterrows():
        cik = row['cik']
        year = row['document_fiscal_year']
        url = row['edgar_url']
        
        print(f"\nProcessing CIK: {cik}, Year: {year}")
        content, source = get_document_content(cik, url)
        
        if content:
            if source == "sec_html":
                print(f"  -> Successfully downloaded {len(content)} characters of HTML.")
            tables = parse_financial_tables(content, source)

            # Filter down to the Income Statement and verify
            if tables:
                income_table = find_income_statement_table(tables)
                if income_table:
                    print("  -> Successfully isolated the Income Statement table!")
                    
                    # NEW: Get the exact metric we are looking for in this row
                    target_metric = row['metric_name']
                    print(f"  -> Looking for: {target_metric} in {year}")
                    
                    extracted_value = extract_metric_value(income_table, target_metric, year)
                    print(f"  -> EXTRACTED VALUE: {extracted_value}\n")
                    
                else:
                    print("  -> Could not isolate the Income Statement.\n")

if __name__ == "__main__":
    test_hybrid_router()

