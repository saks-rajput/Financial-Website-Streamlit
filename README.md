# AI Financial Health Analyzer

Upload a company's annual report (PDF) and get an automated financial
health analysis: key figures, ratios, and AI-written commentary, all
traceable back to the numbers actually found in the document.

## Pipeline

1. `pdf_text_extractor.py` — turns a messy annual-report PDF into clean text.
   Tries the `pdftotext` command-line tool first (fast - under 1 second even
   on a 364-page filing), falling back to the pure-Python `pdfplumber`
   library if `pdftotext` isn't installed.
2. `extract_financials.py` — locates the income statement, balance sheet,
   cash flow statement (and, for banks, the Selected Financial Data table)
   inside the document and asks Claude to extract the figures into a
   strict JSON schema.
3. `ratio_engine.py` — plain, deterministic arithmetic (no AI) turning the
   raw figures into ratios: current ratio, margins, debt-to-equity, ROA,
   ROE, free cash flow, etc. - plus a separate bank-specific ratio set
   (ROE, ROA, efficiency ratio, loans-to-deposits, capital ratios) for
   financial institutions, where the standard ratio set doesn't apply.
4. `generate_insights.py` — asks Claude to write analyst-style commentary,
   constrained to only reference numbers that are actually in the ratio data.
5. `edgar_lookup.py` — optional: pulls extra years of history straight
   from SEC EDGAR's free structured filing data (no extra PDFs needed),
   for companies that file with the SEC. A single annual report only has
   2-3 years of figures built in, so this is what makes the trend chart
   worth looking at with 5-6+ years instead of 2-3.

## Getting more years of history

The app supports two ways to widen the Year-over-Year Trend chart beyond
whatever one PDF happens to contain:

- **Upload more than one annual report** at once (the file uploader
  accepts multiple files) - e.g. the 2020 and 2023 10-Ks together cover
  roughly 2018-2023. Years get merged automatically; if two files report
  the same year, the first file uploaded wins.
- **Enter a stock ticker** in the optional field below the uploader. This
  pulls additional years directly from SEC EDGAR (the SEC's own systems,
  not a third party) with zero manual downloading - only for US-listed
  companies. It fills in years your PDF(s) didn't cover; it never
  overwrites a year you actually uploaded. Bank-specific metrics (ROE,
  efficiency ratio, capital ratios) aren't standardized in SEC's XBRL
  data the way revenue/assets/equity are, so EDGAR-sourced years for a
  bank will correctly show those as "Undisclosed" rather than a guess.

## Running locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key_here
python app.py
```

For the fast PDF path locally, install poppler (`brew install poppler` on
macOS, `apt install poppler-utils` on Linux). Without it, the app still
works correctly via the pdfplumber fallback - just slower on large PDFs.

## Deploying on Render (free tier)

No credit card required. The tradeoff is the service spins down after 15
minutes of inactivity and takes about a minute to wake up on the next visit.

1. Push this repo to GitHub (all files in this folder).
2. On render.com, create a new **Web Service**, connect the GitHub repo.
3. Environment: Python 3. Build command: `pip install -r requirements.txt`.
   Start command: `python app.py`.
4. Under Environment → Environment Variables, add `ANTHROPIC_API_KEY`
   with your key. Never commit the key itself into the repo.
5. Instance type: Free.

Note: Render's free tier gives only 0.1 vCPU, so large annual reports will
run noticeably slower here than on Cloud Run below.

## Deploying on Google Cloud Run (recommended for speed)

Cloud Run gives a full (unthrottled) vCPU while a request is running and
has fast (2-5 second) cold starts, which matters for a tool built around
reading big, multi-hundred-page annual reports. This repo already
includes a `Dockerfile` (which installs `poppler-utils` for the fast PDF
path) and `.dockerignore`, so no extra setup files are needed.

1. Push this repo to GitHub.
2. Go to console.cloud.google.com and sign in (a credit card is required
   for identity verification - you won't be charged if usage stays within
   the free tier: 2 million requests / 180,000 vCPU-seconds per month,
   plenty for a personal project).
3. Create a new Project if you don't already have one.
4. Go to Cloud Run → "Create Service" → "Continuously deploy from a
   repository" → connect GitHub → select this repo.
5. Cloud Run will detect the `Dockerfile` automatically and build from it.
6. Under "Variables & Secrets," add `ANTHROPIC_API_KEY` with your key.
7. Under "Authentication," select "Allow unauthenticated invocations" (so
   the app is a normal public web page, not one requiring a Google login).
8. Leave CPU/memory at the defaults (1 vCPU / 512Mi is enough here) and
   deploy. You'll get a live `*.run.app` URL when it finishes.

## Alternative: Streamlit Community Cloud

If you'd rather not maintain a second UI, the original Streamlit version
of this app is also still fully free to host on Streamlit Community
Cloud - its earlier crashes were an infra reliability issue (traced to a
stale module-reload state after rapid redeploys), not a cost one, and a
full "Reboot app" from the dashboard generally clears it.
