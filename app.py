"""
app.py

STAGE C: the website - Streamlit.

This file contains almost no NEW logic - it just calls the same
functions from pdf_text_extractor.py, extract_financials.py,
ratio_engine.py, and generate_insights.py, all of which are UI-framework
agnostic. The interface is a coat of paint on a working engine, not a
rebuild.

Two things changed from the very first version of this file:
1. Bank/financial-institution support: the original version predates
   ratio_engine.py's is_financial_institution() / calculate_bank_ratios(),
   so it never showed the Financial Institution Metrics section - which
   is why JPMorgan's numbers looked "half missing" the first time it was
   tested here even after the backend already supported banks.
2. A defensive guard around `years` being empty: the original crashed the
   whole app (Streamlit's "Oh no" screen) on any filing where extraction
   came back with zero usable years, because `st.columns(len(years))`
   raises when len(years) is 0. That's now caught with a clean error
   message instead.

To run this locally:
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=your_key_here
    streamlit run app.py
"""

import json
import tempfile
import os

import streamlit as st
import pandas as pd

from pdf_text_extractor import extract_clean_text
from extract_financials import build_financial_statements_excerpt, extract_financials_from_text
from ratio_engine import (
    calculate_ratios,
    calculate_bank_ratios,
    calculate_dupont_breakdown,
    format_ratio,
    format_bank_ratio,
    format_dupont_metric,
    is_financial_institution,
    display_name,
)
from generate_insights import generate_insights

st.set_page_config(page_title="AI Analyser Tool", page_icon="🧮", layout="wide")

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    html, body, [class*="css"]  { font-family: 'Inter', sans-serif; }
    .stApp { background: #f4f5f7; }
    .hero {
        background: #0f172a;
        border-radius: 16px;
        padding: 28px 32px;
        margin-bottom: 24px;
    }
    .hero h1 { color: #ffffff; font-weight: 800; font-size: 28px; margin: 0 0 6px 0; }
    .hero p { color: #94a3b8; font-size: 15px; margin: 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="hero"><h1>📊 AI Financial Health Analyzer</h1>'
    "<p>Upload a company's annual report (PDF) and get an automated financial "
    "health analysis: key figures, ratios, and AI-written commentary — all "
    "traceable back to the numbers actually found in the document.</p></div>",
    unsafe_allow_html=True,
)


def _ratios_to_dataframe(ratios_by_year, is_bank_by_year):
    """Rows = ratios, columns = years, using the same formatting rules as
    the CLI's format_ratio (percentages for margins/ROA/ROE, wording that
    distinguishes genuinely missing data from "not applicable to banks").
    Row labels are the human-readable display names, not the raw
    snake_case dict keys."""
    years = sorted(ratios_by_year.keys())
    if not years:
        return pd.DataFrame()

    ratio_names = list(next(iter(ratios_by_year.values())).keys())
    rows = []
    for name in ratio_names:
        row = {"Ratio": display_name(name)}
        for year in years:
            value = ratios_by_year[year].get(name)
            formatted = format_ratio(name, value, is_bank=is_bank_by_year.get(year, False)).strip()
            row[year] = formatted[len(name):].strip()
        rows.append(row)
    return pd.DataFrame(rows).set_index("Ratio")


def _bank_ratios_to_dataframe(bank_ratios_by_year):
    years = sorted(bank_ratios_by_year.keys())
    if not years:
        return pd.DataFrame()

    ratio_names = list(next(iter(bank_ratios_by_year.values())).keys())
    rows = []
    for name in ratio_names:
        row = {"Ratio": display_name(name, bank=True)}
        for year in years:
            value = bank_ratios_by_year[year].get(name)
            formatted = format_bank_ratio(name, value).strip()
            row[year] = formatted[len(name):].strip()
        rows.append(row)
    return pd.DataFrame(rows).set_index("Ratio")


def _dupont_to_dataframe(dupont_by_year, is_bank_by_year):
    """Same row/column layout as the other two tables, for the DuPont ROE
    breakdown. Bank-years only have 3 rows (ROA x Equity Multiplier =
    Implied ROE); non-bank years have 4 (Net Profit Margin x Asset
    Turnover x Equity Multiplier = Implied ROE)."""
    years = sorted(dupont_by_year.keys())
    if not years:
        return pd.DataFrame()

    metric_names = list(next(iter(dupont_by_year.values())).keys())
    rows = []
    for name in metric_names:
        row = {"Metric": display_name(name, dupont=True)}
        for year in years:
            value = dupont_by_year[year].get(name)
            is_bank = is_bank_by_year.get(year, False)
            formatted = format_dupont_metric(name, value).strip()
            row[year] = formatted[len(name):].strip()
        rows.append(row)
    return pd.DataFrame(rows).set_index("Metric")


def _headline_metrics(ratios_by_year, bank_ratios_by_year, is_bank_by_year, years):
    """4 st.metric() cards for the latest year, with a delta vs. the prior
    year where available. delta_color="inverse" is used for ratios where
    lower is generally better (debt/equity, efficiency ratio), so a
    decrease still shows green."""
    latest = years[-1]
    prior = years[-2] if len(years) > 1 else None

    if is_bank_by_year.get(latest):
        latest_vals = bank_ratios_by_year.get(latest, {})
        prior_vals = bank_ratios_by_year.get(prior, {}) if prior else {}
        specs = [
            ("Return on Equity", "return_on_equity", True, "normal"),
            ("Return on Assets", "return_on_assets", True, "normal"),
            ("Efficiency Ratio", "efficiency_ratio", True, "inverse"),
            ("Loans / Deposits", "loans_to_deposits_ratio", True, "off"),
        ]
    else:
        latest_vals = ratios_by_year.get(latest, {})
        prior_vals = ratios_by_year.get(prior, {}) if prior else {}
        specs = [
            ("Net Profit Margin", "net_profit_margin", True, "normal"),
            ("Current Ratio", "current_ratio", False, "off"),
            ("Return on Equity", "roe", True, "normal"),
            ("Debt / Equity", "debt_to_equity", False, "inverse"),
        ]

    cols = st.columns(4)
    for col, (label, key, is_pct, delta_color) in zip(cols, specs):
        value = latest_vals.get(key)
        if value is None:
            col.metric(f"{label} ({latest})", "n/a")
            continue

        display = f"{value * 100:.1f}%" if is_pct else f"{value:.2f}"

        delta = None
        prior_value = prior_vals.get(key)
        if prior_value is not None:
            diff = value - prior_value
            delta = f"{diff * 100:+.1f}pp" if is_pct else f"{diff:+.2f}"

        col.metric(f"{label} ({latest})", display, delta=delta, delta_color=delta_color)


def _trend_dataframe(ratios_by_year, bank_ratios_by_year, is_bank_by_year, years):
    """Net profit margin for ordinary companies, ROE for banks - picked
    because both are present across the widest range of filers."""
    rows = []
    for year in years:
        if is_bank_by_year.get(year):
            value = bank_ratios_by_year.get(year, {}).get("return_on_equity")
            label = "Return on Equity (%)"
        else:
            value = ratios_by_year.get(year, {}).get("net_profit_margin")
            label = "Net Profit Margin (%)"
        rows.append({"Year": year, label: round(value * 100, 2) if value is not None else None})
    return pd.DataFrame(rows).set_index("Year")


uploaded_file = st.file_uploader("Upload an annual report PDF", type=["pdf"])

if uploaded_file is not None:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(uploaded_file.read())
        tmp_path = tmp.name

    with st.spinner("Reading the PDF..."):
        try:
            full_text = extract_clean_text(tmp_path)
        except Exception as e:
            st.error(f"Could not read the PDF: {e}")
            st.stop()
    os.unlink(tmp_path)
    st.success(f"Read {len(full_text):,} characters from the report.")

    with st.spinner("Asking Claude to extract the financial figures..."):
        try:
            excerpt, missing_statements = build_financial_statements_excerpt(full_text)
            financials = extract_financials_from_text(excerpt)
        except Exception as e:
            st.error(f"Extraction failed: {e}")
            st.stop()

    if missing_statements:
        st.warning(
            "Could not confidently find these statement(s): "
            + ", ".join(missing_statements)
            + ". Fields depending on them will show as missing below."
        )

    years = sorted(financials.get("years", {}).keys())
    if not years:
        st.error(
            "Couldn't extract any usable financial data from this document. "
            "This can happen with scanned/image-only PDFs, or a report "
            "format not yet covered by the statement-locating logic."
        )
        st.stop()

    st.success(f"Extracted data for: {', '.join(years)}")

    is_bank_by_year = {year: is_financial_institution(financials["years"][year]) for year in years}
    any_bank_year = any(is_bank_by_year.values())
    if any_bank_year:
        st.info(
            "🏦 This looks like a bank/financial institution filing. Current "
            "ratio, cash ratio, gross margin, operating margin, and free cash "
            "flow don't apply to how banks report (no classified balance "
            "sheet, no cost-of-revenue line) - see the Financial Institution "
            "Metrics table below instead."
        )

    ratios_by_year = {year: calculate_ratios(financials["years"][year]) for year in years}
    bank_ratios_by_year = {
        year: calculate_bank_ratios(financials["years"][year]) for year in years if is_bank_by_year[year]
    }
    dupont_by_year = {
        year: calculate_dupont_breakdown(
            financials["years"][year],
            is_bank=is_bank_by_year[year],
            bank_ratios=bank_ratios_by_year.get(year),
        )
        for year in years
    }

    st.subheader("Headline Metrics")
    with st.container(border=True):
        _headline_metrics(ratios_by_year, bank_ratios_by_year, is_bank_by_year, years)

    if len(years) > 1:
        st.markdown("**Year-over-Year Trend**")
        trend_df = _trend_dataframe(ratios_by_year, bank_ratios_by_year, is_bank_by_year, years)
        st.line_chart(trend_df)

    with st.expander("See raw extracted figures"):
        st.dataframe(pd.DataFrame(financials["years"]))

    st.subheader("Key Ratios by Year")
    with st.container(border=True):
        st.dataframe(_ratios_to_dataframe(ratios_by_year, is_bank_by_year), width="stretch")

    st.subheader("DuPont ROE Breakdown")
    with st.container(border=True):
        st.caption(
            "ROE = Net Profit Margin × Asset Turnover × Equity Multiplier "
            "(financial leverage) for ordinary companies; ROE = Return on "
            "Assets × Equity Multiplier for banks. \"Implied ROE\" is the "
            "product of those factors - it should land close to the ROE "
            "shown above as a sanity check on the extracted figures."
        )
        st.dataframe(_dupont_to_dataframe(dupont_by_year, is_bank_by_year), width="stretch")

    if any_bank_year:
        st.subheader("🏦 Financial Institution Metrics")
        st.caption(
            "Shown only for banks/financial institutions, using the "
            "company's own reported figures (from its Selected Financial "
            "Data / Financial Highlights table) rather than derived "
            "estimates."
        )
        with st.container(border=True):
            st.dataframe(_bank_ratios_to_dataframe(bank_ratios_by_year), width="stretch")

    analysis_data = {
        "raw_financials": financials,
        "ratios": ratios_by_year,
        "bank_ratios": bank_ratios_by_year,
        "dupont": dupont_by_year,
    }

    with st.spinner("Writing analyst commentary..."):
        try:
            narrative = generate_insights(analysis_data)
        except Exception as e:
            st.error(f"Commentary generation failed: {e}")
            st.stop()

    st.subheader("AI-Written Analysis")
    with st.container(border=True):
        st.markdown(narrative)

    st.download_button(
        "Download full analysis (JSON)",
        data=json.dumps(analysis_data, indent=2),
        file_name="analysis_output.json",
        mime="application/json",
    )
else:
    st.info("Upload a PDF above to get started.")
