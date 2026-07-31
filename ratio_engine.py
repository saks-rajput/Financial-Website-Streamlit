"""
ratio_engine.py

STEP 3 of the pipeline: turn raw numbers into the ratios analysts actually
use, e.g. current ratio, profit margins, debt-to-equity.

Notice there is NO AI anywhere in this file. This is 100% plain arithmetic,
the exact same formulas from your manual analysis. This is deliberate:
once the numbers are extracted and verified (Step 2), the math itself
should never be left to an AI to "calculate" - it should be code that
does the exact same division every single time, with zero chance of a
hallucinated number.

Usage:
    python ratio_engine.py result.json
"""

import sys
import json


def safe_div(numerator, denominator):
    """Division that returns None instead of raising, for any input that
    would normally blow up (missing figure, or a genuine zero
    denominator) - callers treat None as "can't be calculated" rather
    than crashing the whole report over one bad number."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


# Ratios that structurally don't apply to banks/financial institutions -
# they don't have a classified balance sheet (current vs. non-current) or
# a cost-of-revenue / operating-income line the way an ordinary company
# does, so these come back "not applicable" rather than "missing".
NOT_APPLICABLE_TO_BANKS = frozenset(
    {"current_ratio", "cash_ratio", "gross_margin", "operating_margin", "free_cash_flow"}
)

# Human-readable labels for the UI. Keys match the dict keys returned by
# calculate_ratios() / calculate_bank_ratios() / calculate_dupont_breakdown()
# exactly - this is purely a display-layer mapping. The underlying snake_case
# keys are unchanged everywhere else in the pipeline (extraction schema,
# insight generation, JSON export), so nothing downstream breaks.
RATIO_DISPLAY_NAMES = {
    "current_ratio": "Current Ratio",
    "cash_ratio": "Cash Ratio",
    "debt_to_equity": "Debt / Equity",
    "debt_to_assets": "Debt / Assets",
    "gross_margin": "Gross Margin",
    "operating_margin": "Operating Margin",
    "net_profit_margin": "Net Profit Margin",
    "roa": "Return on Assets",
    "roe": "Return on Equity",
    "free_cash_flow": "Free Cash Flow",
    "operating_cash_flow_margin": "Operating Cash Flow Margin",
    "cash_conversion_ratio": "Cash Conversion Ratio",
}

BANK_RATIO_DISPLAY_NAMES = {
    "return_on_equity": "Return on Equity",
    "return_on_assets": "Return on Assets",
    "efficiency_ratio": "Efficiency Ratio",
    "loans_to_deposits_ratio": "Loans / Deposits",
    "net_interest_margin": "Net Interest Margin",
    "common_equity_tier1_ratio": "Common Equity Tier 1 Ratio (CET1)",
    "tier1_capital_ratio": "Tier 1 Capital Ratio",
    "total_capital_ratio": "Total Capital Ratio",
}

DUPONT_DISPLAY_NAMES = {
    "net_profit_margin": "Net Profit Margin",
    "asset_turnover": "Asset Turnover",
    "equity_multiplier": "Equity Multiplier (Leverage)",
    "roe_from_dupont": "Implied ROE",
    "roa": "Return on Assets",
}


def display_name(key, bank=False, dupont=False):
    """Human-readable label for a ratio dict key, for use in the UI.
    Falls back to a generic snake_case -> Title Case conversion for any
    key not found in the lookup tables above, so a new/unmapped key never
    breaks the display - it just looks a little less polished."""
    if dupont:
        table = DUPONT_DISPLAY_NAMES
    elif bank:
        table = BANK_RATIO_DISPLAY_NAMES
    else:
        table = RATIO_DISPLAY_NAMES
    return table.get(key, key.replace("_", " ").title())


def is_financial_institution(year_data):
    """Banks/financial institutions are detected by the presence of a
    bank_metrics block with at least one populated field - that block only
    gets filled in when extract_financials.py found a Selected Financial
    Data / Financial Highlights table, which non-banks don't have."""
    bank_metrics = year_data.get("bank_metrics")
    return isinstance(bank_metrics, dict) and any(v is not None for v in bank_metrics.values())


def calculate_ratios(year_data):
    ca = year_data.get("total_current_assets")
    cl = year_data.get("total_current_liabilities")
    cash = year_data.get("cash_and_equivalents")
    ta = year_data.get("total_assets")
    tl = year_data.get("total_liabilities")
    revenue = year_data.get("revenue")
    cogs = year_data.get("cost_of_revenue")
    op_income = year_data.get("operating_income")
    net_income = year_data.get("net_income")
    ocf = year_data.get("operating_cash_flow")
    capex = year_data.get("capital_expenditures")

    gross_profit = None
    if revenue is not None and cogs is not None:
        gross_profit = revenue - cogs

    equity = None
    if ta is not None and tl is not None:
        equity = ta - tl

    free_cash_flow = None
    if ocf is not None and capex is not None:
        free_cash_flow = ocf + capex

    return {
        "current_ratio": safe_div(ca, cl),
        "cash_ratio": safe_div(cash, cl),
        "debt_to_equity": safe_div(tl, equity),
        "debt_to_assets": safe_div(tl, ta),
        "gross_margin": safe_div(gross_profit, revenue),
        "operating_margin": safe_div(op_income, revenue),
        "net_profit_margin": safe_div(net_income, revenue),
        "roa": safe_div(net_income, ta),
        "roe": safe_div(net_income, equity),
        "free_cash_flow": free_cash_flow,
        "operating_cash_flow_margin": safe_div(ocf, revenue),
        "cash_conversion_ratio": safe_div(ocf, net_income),
    }


def calculate_bank_ratios(year_data):
    bank_metrics = year_data.get("bank_metrics") or {}
    return {
        "return_on_equity": bank_metrics.get("return_on_equity"),
        "return_on_assets": bank_metrics.get("return_on_assets"),
        "efficiency_ratio": bank_metrics.get("efficiency_ratio"),
        "loans_to_deposits_ratio": bank_metrics.get("loans_to_deposits_ratio"),
        "net_interest_margin": bank_metrics.get("net_interest_margin"),
        "common_equity_tier1_ratio": bank_metrics.get("common_equity_tier1_ratio"),
        "tier1_capital_ratio": bank_metrics.get("tier1_capital_ratio"),
        "total_capital_ratio": bank_metrics.get("total_capital_ratio"),
    }


def calculate_dupont_breakdown(year_data, is_bank=False, bank_ratios=None):
    """DuPont ROE decomposition: ROE = Net Profit Margin x Asset Turnover x
    Equity Multiplier (financial leverage). It's the same ROE number as
    calculate_ratios()'s "roe", just split into the three levers that
    actually drive it - handy for telling "ROE is high because the company
    is genuinely profitable" apart from "ROE is high because it's carrying
    a lot of debt."

    For banks, revenue-over-total-assets isn't a meaningful "asset
    turnover" (a bank's assets are loans and securities, not operating
    assets used to generate revenue the way a normal company's are), so a
    two-factor version is used instead: ROE = ROA x Equity Multiplier,
    using the bank's own reported ROA.

    Either way, roe_from_dupont (the product of the factors) also works as
    a cross-check against the ROE reported/calculated elsewhere - if the
    two numbers diverge by more than rounding, that's worth a second look
    at the extracted figures.
    """
    ta = year_data.get("total_assets")
    tl = year_data.get("total_liabilities")
    equity = ta - tl if (ta is not None and tl is not None) else None
    equity_multiplier = safe_div(ta, equity)

    if is_bank:
        roa = (bank_ratios or {}).get("return_on_assets")
        roe_from_dupont = None
        if roa is not None and equity_multiplier is not None:
            roe_from_dupont = roa * equity_multiplier
        return {
            "roa": roa,
            "equity_multiplier": equity_multiplier,
            "roe_from_dupont": roe_from_dupont,
        }

    revenue = year_data.get("revenue")
    net_income = year_data.get("net_income")
    net_profit_margin = safe_div(net_income, revenue)
    asset_turnover = safe_div(revenue, ta)

    roe_from_dupont = None
    if net_profit_margin is not None and asset_turnover is not None and equity_multiplier is not None:
        roe_from_dupont = net_profit_margin * asset_turnover * equity_multiplier

    return {
        "net_profit_margin": net_profit_margin,
        "asset_turnover": asset_turnover,
        "equity_multiplier": equity_multiplier,
        "roe_from_dupont": roe_from_dupont,
    }


def format_ratio(name, value, is_bank=False):
    if value is None:
        if is_bank and name in NOT_APPLICABLE_TO_BANKS:
            return f"  {name:28s} Not Applicable"
        return f"  {name:28s} Missing Value"
    if "margin" in name or "roa" in name or "roe" in name:
        return f"  {name:28s} {value * 100:.1f}%"
    if name == "free_cash_flow":
        return f"  {name:28s} {value:,.0f}"
    return f"  {name:28s} {value:.2f}"


def format_bank_ratio(name, value):
    """All bank_metrics fields are percentages/ratios expressed as decimals
    (e.g. 0.17 for 17%), so they're always formatted the same way."""
    if value is None:
        return f"  {name:28s} Undisclosed"
    return f"  {name:28s} {value * 100:.1f}%"


def format_dupont_metric(name, value):
    """net_profit_margin, roa, and roe_from_dupont are percentages;
    asset_turnover and equity_multiplier are "times" multipliers (e.g.
    1.8x), not percentages."""
    if value is None:
        return f"  {name:28s} Missing Value"
    if name in ("net_profit_margin", "roa", "roe_from_dupont"):
        return f"  {name:28s} {value * 100:.1f}%"
    return f"  {name:28s} {value:.2f}x"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python ratio_engine.py <path_to_result.json>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        data = json.load(f)

    for year in sorted(data.get("years", {}).keys()):
        year_data = data["years"][year]
        is_bank = is_financial_institution(year_data)
        print(f"\n=== {year} ===")

        ratios = calculate_ratios(year_data)
        for name, value in ratios.items():
            print(format_ratio(name, value, is_bank=is_bank))

        if is_bank:
            print("  -- Financial institution metrics (company-reported) --")
            bank_ratios = calculate_bank_ratios(year_data)
            for name, value in bank_ratios.items():
                print(format_bank_ratio(name, value))
            dupont = calculate_dupont_breakdown(year_data, is_bank=True, bank_ratios=bank_ratios)
        else:
            dupont = calculate_dupont_breakdown(year_data, is_bank=False)

        print("  -- DuPont ROE breakdown --")
        for name, value in dupont.items():
            print(format_dupont_metric(name, value))
