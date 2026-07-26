import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd

# Configure terminal logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# Dynamically resolve project directory paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_DIR = PROJECT_ROOT / "config"
RESULTS_DIR = PROJECT_ROOT / "results"


@dataclass
class ExtractionResult:
    extraction_id: str
    cik: int
    ticker: str
    company_name: str
    fiscal_year: int
    metric_name: str
    extracted_value: Optional[float]
    currency: str
    winning_tier: str
    source_snippet: str
    accession_no: str
    edgar_url: str
    audit_status: str = "UNVERIFIED"
    audit_notes: str = ""


class Tier1Extractor:
    def __init__(self):
        self.mapping_df: Optional[pd.DataFrame] = None
        self.query_df: Optional[pd.DataFrame] = None
        self.xbrl_df: Optional[pd.DataFrame] = None
        self.metrics_config: Dict[str, Any] = {}

    def load_resources(self):
        """Loads configuration and reference CSVs into memory."""
        logging.info("Loading metrics configuration...")
        config_path = CONFIG_DIR / "metrics_config.json"
        with open(config_path, "r") as f:
            self.metrics_config = json.load(f)

        logging.info("Loading reference CSV datasets from /data...")
        
        # Load mapping dictionary
        self.mapping_df = pd.read_csv(DATA_DIR / "mapping.csv", low_memory=False)
        self.mapping_df["cik"] = pd.to_numeric(self.mapping_df["cik"], errors="coerce").fillna(0).astype(int)

        # Load query filing index
        self.query_df = pd.read_csv(DATA_DIR / "query.csv", low_memory=False)
        self.query_df["cik"] = pd.to_numeric(self.query_df["cik"], errors="coerce").fillna(0).astype(int)

        # Load master XBRL data table
        self.xbrl_df = pd.read_csv(DATA_DIR / "xbrl.csv", low_memory=False)
        self.xbrl_df["EntityCentralIndexKey"] = pd.to_numeric(
            self.xbrl_df["EntityCentralIndexKey"], errors="coerce"
        ).fillna(0).astype(int)

        # Convert decimal years to integers
        self.xbrl_df["DocumentFiscalYearFocus"] = pd.to_numeric(
            self.xbrl_df["DocumentFiscalYearFocus"], errors="coerce"
        ).fillna(0).astype(int)

        logging.info("All resources successfully loaded into memory.")

    def _resolve_filing_metadata(self, cik: int, year: int) -> tuple[str, str]:
        """Helps match the exact SEC filing URL and accession number from query.csv."""
        # Try matching periodOfReport first (e.g., "2016-12-31")
        match = self.query_df[
            (self.query_df["cik"] == cik) &
            (self.query_df["formType"] == "10-K") &
            (self.query_df["periodOfReport"].astype(str).str.startswith(str(year)))
        ]

        # If periodOfReport fails, fallback to checking filedAt timestamp
        if match.empty:
            match = self.query_df[
                (self.query_df["cik"] == cik) &
                (self.query_df["formType"] == "10-K") &
                (self.query_df["filedAt"].astype(str).str.contains(f"{year}|{year+1}"))
            ]

        if not match.empty:
            return str(match["accessionNo"].values[0]), str(match["linkToHtml"].values[0])
        return "UNKNOWN", "UNKNOWN"

    def extract_single_metric(
        self, cik: int, ticker: str, company_name: str, year: int, metric_name: str, xbrl_col: str
    ) -> ExtractionResult:
        """Executes the Tier 1 XBRL dataframe check for a single metric."""
        accession_no, edgar_url = self._resolve_filing_metadata(cik, year)

        # Query xbrl.csv for facts matching CIK and Fiscal Year
        xbrl_match = self.xbrl_df[
            (self.xbrl_df["EntityCentralIndexKey"] == cik) &
            (self.xbrl_df["DocumentFiscalYearFocus"] == year)
        ]

        extracted_val = None
        source_snippet = ""
        winning_tier = "MISS"

        # Check if column exists, then drop blank quarterly NaN rows before taking the value
        if not xbrl_match.empty and xbrl_col in xbrl_match.columns:
            valid_vals = xbrl_match[xbrl_col].dropna()
            if not valid_vals.empty:
                extracted_val = float(valid_vals.values[0])
                winning_tier = "TIER_1_XBRL"
                source_snippet = f"xbrl.csv -> Column: {xbrl_col}"



        return ExtractionResult(
            extraction_id=f"{cik}_{year}_{metric_name}",
            cik=cik,
            ticker=ticker,
            company_name=company_name,
            fiscal_year=year,
            metric_name=metric_name,
            extracted_value=extracted_val,
            currency="USD",
            winning_tier=winning_tier,
            source_snippet=source_snippet,
            accession_no=accession_no,
            edgar_url=edgar_url,
        )

    def run_sweep(self, target_ciks: List[int], target_years: List[int]) -> pd.DataFrame:
        """Runs the extraction pipeline across a list of companies and fiscal years."""
        results = []
        total_queries = len(target_ciks) * len(target_years) * len(self.metrics_config)
        logging.info(f"Starting Tier 1 sweep across {total_queries} data points...")

        for cik in target_ciks:
            # Look up ticker and corporate name from mapping.csv
            map_row = self.mapping_df[self.mapping_df["cik"] == cik]
            ticker = str(map_row["ticker"].values[0]) if not map_row.empty else "N/A"
            name = str(map_row["name"].values[0]) if not map_row.empty else f"CIK_{cik}"

            for year in target_years:
                for metric_name, config in self.metrics_config.items():
                    xbrl_col = config["xbrl_column"]
                    res = self.extract_single_metric(cik, ticker, name, year, metric_name, xbrl_col)
                    results.append(asdict(res))

        df_results = pd.DataFrame(results)
        
        # Calculate summary statistics
        success_count = len(df_results[df_results["winning_tier"] == "TIER_1_XBRL"])
        logging.info(f"Sweep Complete! Tier 1 successfully extracted {success_count} / {total_queries} metrics.")
        return df_results


if __name__ == "__main__":
    # Ensure results directory exists
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Initialize and load data
    extractor = Tier1Extractor()
    extractor.load_resources()

    # Test Sample: Using CIKs from sample subset (SanDisk, AEP Industries, Scott Technologies; 2014-2016)
    test_ciks = [1000180, 785787, 720032]
    test_years = [2014, 2015, 2016]

    # Execute the sweep
    output_df = extractor.run_sweep(target_ciks=test_ciks, target_years=test_years)


#FOR TESTING PURPOSES, UNCOMMENT:

    # Display console preview of the extractions for testing purposes
    print("\n--- TIER 1 EXTRACTION PREVIEW ---")
    preview_cols = ["extraction_id", "company_name", "fiscal_year", "metric_name", "extracted_value", "winning_tier"]
    print(output_df[preview_cols].to_string(index=False))


#FOR FULL SWEEP ACROSS ALL COMPANIES, UNCOMMENT:

    # Pull ALL unique company CIKs from mapping.csv
    #all_ciks = extractor.mapping_df["clk"].unique().tolist()
    
    # Define your target historical year range (e.g., a 10-year sweep from 2011 to 2020)
    #target_years = list(range(2011, 2021))

    # Execute the full sweep across all companies
    #output_df = extractor.run_sweep(target_ciks=all_ciks, target_years=target_years)

    # Export results CSV 
    output_path = RESULTS_DIR / "extraction_results_tier1.csv"
    output_df.to_csv(output_path, index=False)
    print(f"\nFull audit log saved successfully to: {output_path}")