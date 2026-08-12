import re
import time
import chromadb
from bs4 import BeautifulSoup
import requests

# Import URL finder here so Tier 3 can use it dynamically
from url_finder import get_sec_10k_url

# Standard SEC Header
HEADERS = {
    "User-Agent": "WoosterDataProject student@email.com", #ADD REAL EMAIL HERE
    "Accept-Encoding": "gzip, deflate"
}

def convert_table_to_markdown(table_tag):
    """Converts a BeautifulSoup HTML table tag into a Markdown formatted string."""
    rows = table_tag.find_all('tr')
    if not rows:
        return ""

    md_lines = []
    max_cols = 0

    # First pass: determine the maximum number of columns to ensure structural alignment
    for row in rows:
        cols = row.find_all(['td', 'th'])
        if len(cols) > max_cols:
            max_cols = len(cols)

    if max_cols == 0:
        return ""

    # Second pass: extract data and format as Markdown
    for i, row in enumerate(rows):
        cols = row.find_all(['td', 'th'])
        # Extract text, stripping internal line breaks
        row_data = [col.get_text(separator=" ", strip=True).replace("\n", " ") for col in cols]
        
        # Pad the row with empty cells if it's missing columns
        while len(row_data) < max_cols:
            row_data.append("")

        md_lines.append("| " + " | ".join(row_data) + " |")

        # Inject the Markdown header divider after the first row
        if i == 0:
            separator = "|" + "---|".join([""] * (max_cols + 1))
            md_lines.append(separator)

    return "\n".join(md_lines)

def clean_html_to_text(html_content):
    """Replaces HTML tables with Markdown grids, then extracts the remaining clean text."""
    print("  -> Converting HTML tables to Markdown and extracting text...")
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 1. Isolate and convert all tables to Markdown
    for table in soup.find_all('table'):
        md_table = convert_table_to_markdown(table)
        # Replace the HTML table element with the raw Markdown string
        # We add line breaks so it doesn't get merged with adjacent paragraphs
        table.replace_with(f"\n\n{md_table}\n\n")
        
    # 2. Extract all remaining text (which now includes our Markdown strings)
    raw_text = soup.get_text(separator=' ', strip=True)
    
    # 3. Clean up whitespace but preserve the line breaks necessary for Markdown tables
    # Collapse multiple spaces/tabs into a single space
    clean_text = re.sub(r'[ \t]+', ' ', raw_text)
    # Ensure no more than two consecutive line breaks
    clean_text = re.sub(r'\n\s*\n', '\n\n', clean_text) 
    
    return clean_text

def chunk_text(text, chunk_size=800, overlap=150):
    """
    Slices text into overlapping chunks (sliding window).
    Overlap ensures a sentence split across two chunks doesn't lose its meaning.
    """
    print(f"  -> Slicing document into chunks (Size: {chunk_size}, Overlap: {overlap})...")
    words = text.split()
    chunks = []
    
    for i in range(0, len(words), chunk_size - overlap):
        chunk = " ".join(words[i:i + chunk_size])
        chunks.append(chunk)
        if i + chunk_size >= len(words):
            break
            
    return chunks

def build_vector_database(chunks, cik, year):
    """Initializes a local ChromaDB collection and embeds the text chunks."""
    print("  -> Initializing local ChromaDB vector database...")
    
    # We use an ephemeral (in-memory) client because we only need to store 
    # the document long enough to ask it one question, then we throw it away.
    chroma_client = chromadb.Client()
    
    collection_name = f"sec_10k_{cik}_{year}"
    
    try:
        chroma_client.delete_collection(name=collection_name)
    except Exception:
        pass # Collection doesn't exist yet, which is fine
        
    collection = chroma_client.create_collection(name=collection_name)
    
    # Chroma needs unique IDs for every chunk
    ids = [f"chunk_{i}" for i in range(len(chunks))]
    
    print(f"  -> Embedding {len(chunks)} chunks... (This may take a moment)")
    # By default, Chroma will automatically download and use the 
    # 'all-MiniLM-L6-v2' model to convert these strings into vector arrays
    collection.add(
        documents=chunks,
        ids=ids
    )
    
    print("  -> Vector database populated successfully.")
    return collection

def create_extraction_prompt(retrieved_chunks, metric_name, target_year):
    """Enforces a Chain-of-Thought JSON schema without triggering Example Bias."""
    context = "\n\n---\n\n".join(retrieved_chunks)
    
    prompt = f"""You are a precise financial data extraction algorithm.
Your task is to analyze the SEC 10-K text chunks below and extract the total dollar amount for '{metric_name}' in the year {target_year}.

Context:
{context}

Respond ONLY with a valid JSON object matching this exact schema:
{{
  "line_item_found": "The exact row header or line item name found in the text",
  "raw_reported_number": "The number exactly as printed in the table/paragraph for {target_year} (e.g., 'X,XXX')",
  "unit_scale": "Either 'thousands', 'millions', or 'exact'",
  "calculated_total_dollars": <final calculated integer value by applying the scale multiplier, or null if not found>,
  "metric_found": <true or false>
}}

Rules:
1. First identify the exact row name in 'line_item_found'.
2. Identify the raw number for fiscal year {target_year}.
3. Look at table headers or section text to determine if figures are 'in thousands' or 'in millions'.
4. Calculate 'calculated_total_dollars' by applying the multiplier (e.g., if the raw number is 45 and scale is millions, output 45000000).
5. If the metric is not present for {target_year}, set 'metric_found' to false and 'calculated_total_dollars' to null.
"""

    return prompt

import json

def query_phi3_mini(prompt):
    """Sends the prompt to Ollama with strict JSON mode enabled."""
    print("  -> Sending context to Phi-3 Mini for JSON extraction...")
    
    api_url = "http://localhost:11434/api/generate" 
    
    payload = {
        "model": "phi3:mini",
        "prompt": prompt,
        "format": "json",  # Forces Ollama to strictly output valid JSON
        "stream": False,
        "options": {
            "temperature": 0.0  # Kept inside 'options' dict for standard Ollama API
        }
    }
    
    try:
        response = requests.post(api_url, json=payload, timeout=180)
        response.raise_for_status()
        raw_response = response.json().get("response", "").strip()
        
        # Parse the returned JSON string into a Python dictionary
        data = json.loads(raw_response)
        
        print("\n--- Model Thought Process (JSON Breakdown) ---")
        print(f"  Line Item Identified : {data.get('line_item_found')}")
        print(f"  Raw Number Reported  : {data.get('raw_reported_number')}")
        print(f"  Unit Scale           : {data.get('unit_scale')}")
        print(f"  Metric Found         : {data.get('metric_found')}")
        print("----------------------------------------------\n")
        
        if data.get("metric_found") and data.get("calculated_total_dollars") is not None:
            return int(data.get("calculated_total_dollars"))
        else:
            return "METRIC_NOT_FOUND"
            
    except json.JSONDecodeError:
        print("  -> Error: Model failed to return valid JSON.")
        return "PARSE_ERROR"
    except Exception as e:
        print(f"  -> Local LLM Error: {e}")
        return "LLM_ERROR"

# --- TEST BLOCK ---
if __name__ == "__main__":
    test_cik = 785956
    test_year = 2019
    target_metric = "Research and Development expense"
    
    print(f"Starting Tier 3 Ingestion Test for CIK {test_cik} ({test_year})\n")
    url = get_sec_10k_url(test_cik, test_year)
    
    if url and url not in ["NO_FILING_EXISTS", "NO_FILINGS_FOUND", "API_ERROR"]:
        print("Downloading document...")
        response = requests.get(url, headers=HEADERS)
        clean_text = clean_html_to_text(response.text)
        document_chunks = chunk_text(clean_text)
        db_collection = build_vector_database(document_chunks, test_cik, test_year)
        
        # 1. Retrieve the top 4 most relevant chunks from ChromaDB
        print(f"\nSearching vector database for '{target_metric}'...")
        # Increase n_results to 10 and make the query more explicit?
        results = db_collection.query(
            query_texts=[f"Total consolidated {target_metric} for the entire fiscal year {test_year}"],
            n_results=3
        )
        # 2. Extract the text chunks from the ChromaDB result object
        retrieved_paragraphs = results['documents'][0]
        
        # 3. Build the strict prompt
        final_prompt = create_extraction_prompt(retrieved_paragraphs, target_metric, test_year)
        
        # 4. Send to Phi-3 Mini
        extracted_value = query_phi3_mini(final_prompt)
        
        print("\n" + "="*40)
        print("FINAL TIER 3 EXTRACTION RESULT:")
        print(extracted_value)
        print("="*40 + "\n")