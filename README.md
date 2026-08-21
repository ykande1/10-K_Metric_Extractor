# SEC 10-K Financial Metric Extraction Pipeline

An automated, multi-tiered data pipeline designed to extract financial metrics (e.g., Net Income, R&D Expenses, Net Sales) from corporate SEC 10-K filings.

Because corporate SEC filings suffer from extreme formatting inconsistencies, this pipeline utilizes a cascading 3-tier architecture. It prioritizes fast, structured lookups first, algorithmic HTML scraping second, and falls back to a Retrieval-Augmented Generation (RAG) local language model for complex edge cases.


1. **Tier 1 (Structured Cache):** Direct lookup from local XBRL-derived CSV caches. Provides instant, 100% accurate extraction when a company uses standard XBRL tags.
2. **Tier 2 (HTML Scraper):** Parses the raw SEC HTML using `BeautifulSoup`. Uses strict priority alias matching, array alignment, and "poison word" filters (to avoid per-share or pre-tax metrics) to algorithmically extract numbers from complex financial tables.
3. **Tier 3 (Local LLM RAG):** For documents with broken HTML `colspan` tags or missing tables, this tier converts the HTML to Markdown, chunks the text, and stores it in a local **ChromaDB** vector database. It then retrieves the most relevant semantic chunks and prompts a local language model to extract the metric via structured JSON.

## 💻 System Requirements

* **OS:** Windows / macOS / Linux
* **CPU/RAM:** Intel i7 (or equivalent) with at least 16GB RAM for the Llama 3 8B configuration. *(See the Phi-3 Mini section below for lower RAM requirements).*
* **Python:** Python 3.9+
* **Ollama:** Required for running the local Tier 3 inference engine.

## ⚙️ Installation & Setup

### 1. Clone the Repository and Setup Virtual Environment

```bash
git clone https://github.com/your-username/sec-10k-extractor.git
cd sec-10k-extractor

# Create and activate your virtual environment
python -m venv venv

# Windows activation:
venv\Scripts\activate
# Mac/Linux activation:
source venv/bin/activate

```

### 2. Install Python Dependencies

Install the required packages for web scraping, vector databases, and data manipulation:

```bash
pip install pandas beautifulsoup4 requests chromadb

```

### 3. Install Ollama and the Language Model

Tier 3 requires a local inference engine to run without incurring API costs or sending financial data to third parties.

1. Download and install **Ollama** from [ollama.com](https://ollama.com/).
2. Open a terminal and download the default 4-bit quantized **Llama 3** model (approx. 4.7GB):

```bash
ollama pull llama3

```

### 4. Configure SEC Header (Important)

The SEC EDGAR database strictly enforces rate limits and requires users to declare their user agent.
Open `src/tier2_extractor.py` and `src/tier3_llm.py` and update the `HEADERS` dictionary with a valid email address:

```python
HEADERS = {
    "User-Agent": "WoosterDataProject your.real.email@example.com",
    "Accept-Encoding": "gzip, deflate"
}

```

---

## 📉 Running a Lighter Model (Phi-3 Mini)

If your system is struggling with RAM usage (or you want faster extraction speeds and don't mind a slight dip in formatting accuracy), you can easily swap the Tier 3 LLM from Llama 3 (8B) to Microsoft's **Phi-3 Mini (3.8B)**.

The quantized Phi-3 Mini model only requires about **2.2 GB** of RAM.

**Step 1: Download the model**
Open a terminal and pull the model via Ollama:

```bash
ollama pull phi3:mini

```

**Step 2: Update the Python Script**
Open `src/tier3_llm.py`, locate the LLM query function, and change the model payload target:

```python
    payload = {
        "model": "phi3:mini",  # Changed from "llama3"
        "prompt": prompt,
        "format": "json", 
        "stream": False,
        "options": {
            "temperature": 0.0 
        }
    }

```

*Trade-off Note: Phi-3 Mini evaluates prompts faster but is more prone to "babbling" conversational text instead of strict JSON, which may occasionally trigger a `PARSE_ERROR`.*

---

## 🚀 Usage

### Running the End-to-End Test

To verify that all three tiers are successfully passing data, you can run the testing verification script. This script samples the dataset, runs the cascade, and outputs a `csv` to the `/results` folder.

```bash
python src/testing_verification.py

```

### Running Individual Tiers

You can test the RAG ingestion and extraction process for a single, hardcoded company by running the Tier 3 file directly:

```bash
python src/tier3_llm.py

```

## ⚠️ Known Limitations & Troubleshooting

* **Tier 2 Array Misalignments:** Companies frequently use invisible HTML columns (`<td></td>`) or merged headers (`colspan`) for visual styling. If Tier 2 outputs a wrong year's data or a multiplier error, it is likely due to the HTML structure skewing the Python array. Tier 3 is designed to catch these.
* **Tier 3 Timeout Errors:** If the script throws an `LLM_ERROR`, the model likely exceeded the 180-second API timeout limit. Ensure no other heavy applications are running to free up CPU threads.
* **Context Window Overflow:** The ChromaDB retrieval is locked to `n_results=3`. Increasing this to 5 or 10 may sometimes yield better results, but it could also pull in conversational MD&A paragraphs, causing the LLM to experience the "Lost in the Middle" distraction effect and extract incorrect numbers.

---