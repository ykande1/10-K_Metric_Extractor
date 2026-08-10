import requests
import time

def get_sec_10k_url(cik, target_year):
    """Queries the SEC Submissions API to reconstruct a missing 10-K URL."""
    
    # 1. The SEC API requires exactly 10 digits for the CIK
    padded_cik = str(cik).zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{padded_cik}.json"
    
    # 2. The SEC firewall bypass (Name/Project + Email)
    headers = {
        "User-Agent": "WoosterDataProject student@email.com", #ADD REAL EMAIL HERE
        "Accept-Encoding": "gzip, deflate"
    }
    
    print(f"Querying SEC Submissions API for CIK {cik} (Year: {target_year})...")
    
    try:
        # SEC rate limit (max 10 requests/sec)
        time.sleep(0.15) 
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # The SEC API returns filings as columnar arrays inside a dictionary
        recent_filings = data.get("filings", {}).get("recent", {})
        
        if not recent_filings:
            return "NO_FILINGS_FOUND"
            
        # 3. Loop through the forms looking for the matching 10-K
        for i, form in enumerate(recent_filings.get("form", [])):
            if form == "10-K":
                report_date = recent_filings["reportDate"][i]
                
                # Check if the fiscal report date matches our target year (e.g., "2014-12-31")
                if report_date.startswith(str(target_year)):
                    accession_number = recent_filings["accessionNumber"][i]
                    primary_doc = recent_filings["primaryDocument"][i]
                    
                    # The URL requires the accession number without dashes
                    accession_no_dashes = accession_number.replace("-", "")
                    
                    # The final URL uses the unpadded CIK
                    clean_cik = str(cik).lstrip("0")
                    
                    # Construct the final EDGAR URL
                    final_url = f"https://www.sec.gov/Archives/edgar/data/{clean_cik}/{accession_no_dashes}/{primary_doc}"
                    
                    return final_url
                    
        return "NO_FILING_EXISTS"
        
    except requests.exceptions.HTTPError as e:
        print(f"  -> HTTP Error: {e}")
        return "API_ERROR"
    except Exception as e:
        print(f"  -> Error: {e}")
        return "API_ERROR"

if __name__ == "__main__":
    # Test 1: SanDisk (1000180) for 2014 - We know this one exists
    print("Test 1: SanDisk (2014)")
    sandisk_url = get_sec_10k_url(1000180, 2014)
    print(f"Result: {sandisk_url}\n")
    print("-" * 50 + "\n")
    
    # Test 2: Scott Technologies (720032) for 2014 - The SEC API should return "NO_FILING_EXISTS" since this company did not file a 10-K in 2014
    print("Test 2: Scott Technologies (2014)")
    scott_url = get_sec_10k_url(720032, 2014)
    print(f"Result: {scott_url}")