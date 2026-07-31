"""
edgar_lookup.py

OPTIONAL data source: pulls several years of history straight from SEC
EDGAR's structured XBRL filing data, so the Year-over-Year Trend chart
isn't limited to however many years happen to be printed in a single
uploaded annual report (usually just 2-3: SEC rules only require 3 years
of income statement / cash flow data and 2 years of balance sheet data
in any one 10-K).

This is NOT a replacement for the PDF-upload path - it's a same-shape,
free, no-signup data source (the SEC's own systems, not a third party)
that only covers companies that file with the SEC (i.e. US-listed public
companies), and it can't fill in bank_metrics (SEC's standard XBRL tags
don't have a settled "efficiency ratio" or "CET1 ratio" concept the way
they do for revenue/assets/equity), so a bank's EDGAR-sourced years will
correctly show those fields as "Undisclosed" rather than guessing.

Requires:
    pip install requests

Usage:
    python edgar_lookup.py AAPL
"""

import sys
import json
from datetime import date

import requests

TICKER_INDEX_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"

# For each schema field, the us-gaap XBRL tags that might carry it, tried
# in order - different companies (and different eras of the same company)
# use different tags for conceptually the same line item, so the first
# tag that actually has data for a given company wins.
CONCEPT_MAP = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "cost_of_revenue": [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
    ],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "total_current_assets": ["AssetsCurrent"],
    "total_assets": ["Assets"],
    "total_current_liabilities": ["LiabilitiesCurrent"],
    "total_liabilities": ["Liabilities"],
    "total_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "cash_and_equivalents": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "accounts_receivable": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capital_expenditures": ["PaymentsToAcquirePropertyPlantAndEquipment"],
}

# XBRL reports every dollar figure in whole dollars (e.g. 96995000000).
# Our schema (and the rest of the pipeline) works in millions, matching
# how PDF-extracted figures are printed in the actual filing - so every
# monetary field gets divided by 1e6. eps_diluted is a per-share dollar
# amount, not a magnitude figure, so it's left alone.
MONETARY_FIELDS = {
    "revenue", "cost_of_revenue", "operating_income", "net_income",
    "total_current_assets", "total_assets", "total_current_liabilities",
    "total_liabilities", "total_equity", "cash_and_equivalents",
    "accounts_receivable", "operating_cash_flow", "capital_expenditures",
}


def get_cik_for_ticker(ticker: str, headers: dict) -> str:
    """Downloads SEC's full ticker -> CIK index and looks up one ticker.
    Returns the zero-padded 10-digit CIK string SEC's other endpoints
    expect, or None if the ticker isn't a SEC-registered filer."""
    resp = requests.get(TICKER_INDEX_URL, headers=headers, timeout=15)
    resp.raise_for_status()
    index = resp.json()

    ticker = ticker.strip().upper()
    for row in index.values():
        if row.get("ticker", "").upper() == ticker:
            return str(row["cik_str"]).zfill(10)
    return None


def fetch_company_facts(cik10: str, headers: dict) -> dict:
    """The full XBRL "company facts" payload: every concept the company
    has ever tagged, across every filing, going back to whenever it
    started filing electronically with the SEC."""
    url = COMPANY_FACTS_URL.format(cik10=cik10)
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _select_one_entry_per_fiscal_year(concept_entries: list) -> dict:
    """From the raw list of XBRL fact entries for one concept, keep at
    most one entry per fiscal year: only entries that came from an actual
    annual report (form == "10-K", fp == "FY"). For duration facts (income
    statement / cash flow items, which report a start AND end date), also
    require the period to span roughly a full year - this filters out
    same-tagged quarterly or partial-period entries that would otherwise
    look like a match. If the same fiscal year got reported more than
    once (e.g. a restatement in a later 10-K), the most recently filed
    version wins."""
    by_year = {}
    for entry in concept_entries:
        if entry.get("form") != "10-K" or entry.get("fp") != "FY":
            continue
        fy = entry.get("fy")
        end = entry.get("end")
        if fy is None or end is None:
            continue

        start = entry.get("start")
        if start is not None:
            try:
                span_days = (date.fromisoformat(end) - date.fromisoformat(start)).days
            except ValueError:
                continue
            if not (300 <= span_days <= 400):
                continue

        existing = by_year.get(fy)
        if existing is None or entry.get("filed", "") > existing.get("filed", ""):
            by_year[fy] = entry

    return by_year


def extract_annual_series(company_facts: dict, num_years: int = 6) -> dict:
    """Turns the raw XBRL company-facts payload into the same
    {"<year>": {field: value, ...}} shape extract_financials_from_text()
    produces, for the most recent `num_years` fiscal years found. Fields
    with no matching tag (or no data for a given year) come back as None,
    same as a genuinely-missing field from PDF extraction - so everything
    downstream (ratio_engine, the Streamlit tables) treats EDGAR-sourced
    years no differently from PDF-sourced ones. "bank_metrics" is always
    None here (see module docstring)."""
    us_gaap = company_facts.get("facts", {}).get("us-gaap", {})

    per_field_by_year = {}
    for field, tags in CONCEPT_MAP.items():
        for tag in tags:
            concept = us_gaap.get(tag)
            if not concept:
                continue
            entries = concept.get("units", {}).get("USD", [])
            by_year = _select_one_entry_per_fiscal_year(entries)
            if by_year:
                per_field_by_year[field] = by_year
                break  # first tag with any usable data wins for this field

    all_fiscal_years = sorted(
        {fy for by_year in per_field_by_year.values() for fy in by_year}, reverse=True
    )
    recent_fiscal_years = all_fiscal_years[:num_years]

    years_out = {}
    for fy in recent_fiscal_years:
        year_data = {}
        for field in CONCEPT_MAP:
            entry = per_field_by_year.get(field, {}).get(fy)
            if entry is None:
                year_data[field] = None
                continue
            val = entry["val"]
            if field in MONETARY_FIELDS:
                val = val / 1_000_000
                if field == "capital_expenditures":
                    val = -abs(val)
            year_data[field] = val
        year_data["bank_metrics"] = None
        years_out[str(fy)] = year_data

    return years_out


def fetch_annual_history(ticker: str, num_years: int = 6, contact_email: str = "your_email@example.com"):
    """Convenience entry point: ticker in, (years_dict, error_message) out.
    error_message is None on success. contact_email is sent in the
    User-Agent header, per SEC's fair-access guidance for automated
    requests (sec.gov/os/webmaster-faq#developers) - swap in your own
    email before relying on this in production; SEC will still serve
    requests with a generic one, but a real contact address is the
    documented, polite way to use their API."""
    headers = {"User-Agent": f"AI Financial Health Analyzer {contact_email}"}

    try:
        cik10 = get_cik_for_ticker(ticker, headers)
    except requests.RequestException as e:
        return {}, f"Could not reach SEC EDGAR's ticker index: {e}"

    if cik10 is None:
        return {}, f"No SEC-registered company found for ticker '{ticker}'."

    try:
        facts = fetch_company_facts(cik10, headers)
    except requests.RequestException as e:
        return {}, f"Could not fetch SEC filing history for CIK {cik10}: {e}"

    years = extract_annual_series(facts, num_years=num_years)
    if not years:
        return {}, "SEC EDGAR didn't return any usable annual (10-K) figures for this company."

    return years, None


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python edgar_lookup.py <TICKER>", file=sys.stderr)
        sys.exit(1)

    years, error = fetch_annual_history(sys.argv[1])
    if error:
        print(error, file=sys.stderr)
        sys.exit(1)

    print(json.dumps(years, indent=2))
