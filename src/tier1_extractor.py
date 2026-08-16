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
    document_fiscal_year: int  
    metric_fiscal_year: int 
    period_start_date: str     
    period_end_date: str       
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
        xbrl_path = DATA_DIR / "original_xbrl_data.csv" if (DATA_DIR / "original_xbrl_data.csv").exists() else DATA_DIR / "xbrl.csv"
        self.xbrl_df = pd.read_csv(xbrl_path, low_memory=False)
        
        # Standardize column header names for internal checks
        cik_col = "entitycentralindexkey" if "entitycentralindexkey" in self.xbrl_df.columns else "EntityCentralIndexKey"
        fy_col = "documentfiscalyearfocus" if "documentfiscalyearfocus" in self.xbrl_df.columns else "DocumentFiscalYearFocus"

        self.xbrl_df[cik_col] = pd.to_numeric(self.xbrl_df[cik_col], errors="coerce").fillna(0).astype(int)
        self.xbrl_df[fy_col] = pd.to_numeric(self.xbrl_df[fy_col], errors="coerce").fillna(0).astype(int)
        
        # Store resolved column names
        self._cik_col = cik_col
        self._fy_col = fy_col

        logging.info("All resources successfully loaded into memory.")

    def _resolve_filing_metadata(self, cik: int, year: Any) -> tuple[str, str]:
        """Helps match the exact SEC filing URL and accession number from query.csv."""
        try:
            year_int = int(year)
        except (ValueError, TypeError):
            return "UNKNOWN", "UNKNOWN"

        # Try matching periodOfReport first (e.g., "2016-12-31")
        match = self.query_df[
            (self.query_df["cik"] == cik) &
            (self.query_df["formType"] == "10-K") &
            (self.query_df["periodOfReport"].astype(str).str.startswith(str(year_int)))
        ]

        # If periodOfReport fails, fallback to checking filedAt timestamp
        if match.empty:
            match = self.query_df[
                (self.query_df["cik"] == cik) &
                (self.query_df["formType"] == "10-K") &
                (self.query_df["filedAt"].astype(str).str.contains(f"{year_int}|{year_int+1}"))
            ]

        if not match.empty:
            return str(match["accessionNo"].values[0]), str(match["linkToHtml"].values[0])
        return "UNKNOWN", "UNKNOWN"

    def extract_single_metric(
        self, cik: int, ticker: str, company_name: str, year: Any, metric_name: str, xbrl_col: str
    ) -> ExtractionResult:
        """Executes the Tier 1 XBRL dataframe check for a single metric."""
        try:
            year_int = int(year)
        except (ValueError, TypeError):
            year_int = 0

        accession_no, edgar_url = self._resolve_filing_metadata(cik, year_int)

        # Query xbrl_df for facts matching CIK
        xbrl_match = self.xbrl_df[self.xbrl_df[self._cik_col] == cik].copy()

        extracted_val = None
        source_snippet = ""
        winning_tier = "MISS"
        start_date = "N/A"
        end_date = "N/A"

        if not xbrl_match.empty and xbrl_col in xbrl_match.columns:
            valid_rows = xbrl_match.dropna(subset=[xbrl_col]).copy()
            
            if not valid_rows.empty:
                # Identify date column if present
                date_col = None
                for candidate in ["end_date", "period_end_date", "ddate", "periodOfReport"]:
                    if candidate in valid_rows.columns:
                        date_col = candidate
                        break

                target_row = None

                # 1. Primary Filter: End-date starts with or contains requested fiscal year
                if date_col and year_int > 0:
                    date_matches = valid_rows[
                        valid_rows[date_col].astype(str).str.startswith(str(year_int))
                    ]
                    if not date_matches.empty:
                        target_row = date_matches.iloc[0]

                # 2. Secondary Filter: DocumentFiscalYearFocus == year_int (sorted descending by date)
                if target_row is None and year_int > 0:
                    fy_matches = valid_rows[valid_rows[self._fy_col] == year_int]
                    if not fy_matches.empty:
                        if date_col:
                            fy_matches = fy_matches.sort_values(by=date_col, ascending=False)
                        target_row = fy_matches.iloc[0]

                # 3. Final Fallback: First available valid row
                if target_row is None:
                    target_row = valid_rows.iloc[0]

                extracted_val = float(target_row[xbrl_col])
                winning_tier = "TIER_1_XBRL"
                source_snippet = f"xbrl.csv -> Column: {xbrl_col}"
                
                start_col = "start_date" if "start_date" in target_row else "period_start_date"
                start_date = str(target_row.get(start_col, "UNKNOWN"))
                end_date = str(target_row.get(date_col, "UNKNOWN")) if date_col else "UNKNOWN"

        # Determine resolved metric year from period_end_date if available
        resolved_metric_year = year_int
        if end_date[:4].isdigit():
            resolved_metric_year = int(end_date[:4])

        return ExtractionResult(
            extraction_id=f"{cik}_{year_int}_{metric_name}",
            cik=cik,
            ticker=ticker,
            company_name=company_name,
            document_fiscal_year=year_int,
            metric_fiscal_year=resolved_metric_year,
            period_start_date=start_date,
            period_end_date=end_date,
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
            map_row = self.mapping_df[self.mapping_df["cik"] == cik]
            ticker = str(map_row["ticker"].values[0]) if not map_row.empty else "N/A"
            name = str(map_row["name"].values[0]) if not map_row.empty else f"CIK_{cik}"

            for year in target_years:
                for metric_name, config in self.metrics_config.items():
                    xbrl_col = config["xbrl_column"]
                    res = self.extract_single_metric(cik, ticker, name, year, metric_name, xbrl_col)
                    results.append(asdict(res))

        df_results = pd.DataFrame(results)
        success_count = len(df_results[df_results["winning_tier"] == "TIER_1_XBRL"])
        logging.info(f"Sweep Complete! Tier 1 successfully extracted {success_count} / {total_queries} metrics.")
        return df_results


if __name__ == "__main__":
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    extractor = Tier1Extractor()
    extractor.load_resources()

    test_ciks = [1000180, 785787, 720032]
    test_years = [2014, 2015, 2016]

    output_df = extractor.run_sweep(target_ciks=test_ciks, target_years=test_years)

    print("\n--- TIER 1 EXTRACTION PREVIEW ---")
    preview_cols = [
        "extraction_id", 
        "company_name", 
        "document_fiscal_year", 
        "period_start_date", 
        "period_end_date", 
        "metric_name", 
        "extracted_value", 
        "winning_tier"
    ]
    print(output_df[preview_cols].to_string(index=False))

    output_path = RESULTS_DIR / "extraction_results_tier1.csv"
    output_df.to_csv(output_path, index=False)
    print(f"\nFull audit log saved successfully to: {output_path}")