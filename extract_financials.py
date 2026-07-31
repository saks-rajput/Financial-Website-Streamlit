"""
extract_financials.py

STEP 2 of the pipeline: turn clean text into structured numbers.

This is the one piece that actually uses AI. Everything before it
(pdf_text_extractor.py) and everything after it (a future ratio_engine.py)
is plain, deterministic code - no AI, fully traceable. This is deliberate:
we only want AI doing the part it's genuinely good at (reading messy
prose/tables and mapping them to a schema), never the arithmetic - and
never the job of deciding "is this chunk of text actually the real
financial statement." That decision needs to be reliable and traceable
too, so it stays rule-based (see find_real_statement below).

Requires:
    pip install anthropic
    export ANTHROPIC_API_KEY=your_key_here   (get one at console.anthropic.com)

Usage:
    python extract_financials.py clean_text.txt
"""

import sys
import re
import json
import anthropic

SCHEMA_INSTRUCTIONS = """
You are a financial data extraction engine. You will be given raw text
from a company's annual report (income statement, balance sheet, and
cash flow statement sections may all be present, possibly with some
surrounding narrative text mixed in).

Extract the following line items for EVERY year you can find in the text.
Respond with ONLY valid JSON - no preamble, no markdown fences, no
commentary. If a figure is genuinely not present in the text, use null
rather than guessing.

JSON shape:
{
  "currency": "USD",
  "unit": "millions",
  "years": {
    "<year>": {
      "revenue": number or null,
      "cost_of_revenue": number or null,
      "operating_income": number or null,
      "net_income": number or null,
      "eps_diluted": number or null,
      "total_current_assets": number or null,
      "total_assets": number or null,
      "total_current_liabilities": number or null,
      "total_liabilities": number or null,
      "total_equity": number or null,
      "cash_and_equivalents": number or null,
      "accounts_receivable": number or null,
      "operating_cash_flow": number or null,
      "capital_expenditures": number or null,
      "bank_metrics": {
        "return_on_equity": number or null,
        "return_on_assets": number or null,
        "efficiency_ratio": number or null,
        "loans_to_deposits_ratio": number or null,
        "net_interest_margin": number or null,
        "common_equity_tier1_ratio": number or null,
        "tier1_capital_ratio": number or null,
        "total_capital_ratio": number or null
      }
    }
  }
}

Rules:
- Numbers must be plain numbers (no $ signs, no commas, no units in the value).
- Negative numbers shown in parentheses in the source, e.g. (32,251), must
  become negative numbers, e.g. -32251.
- Use the exact figures as printed. Do not calculate or estimate anything
  that isn't directly stated in the text.
- "total_equity" is total shareholders'/stockholders' equity (sometimes
  labeled "total stockholders' equity", "total shareholders' equity", or
  "total equity"), NOT total liabilities and equity combined.
- "bank_metrics" is ONLY for banks / financial institutions, and only if
  the text includes a "Selected Financial Data," "Financial Highlights,"
  or similar summary table that reports these as already-calculated
  ratios (do not derive them yourself from other line items - only fill
  these in if the ratio itself is printed in the text). For companies
  that aren't banks, or if this table isn't present, set the entire
  "bank_metrics" object to null.
- Express every bank_metrics ratio as a decimal fraction, e.g. a printed
  "17%" or "17" (in a column already labeled as a percentage) becomes
  0.17, not 17.
- "efficiency_ratio" may be labeled "efficiency ratio" or "overhead
  ratio" in the source - they are the same metric (noninterest expense
  as a share of revenue; lower is better). Map either label to this field.
- "return_on_equity" should be the plain "return on equity" / "return on
  common equity" figure, not "return on tangible common equity" (a
  related but different metric some banks also disclose).
"""


def _numeric_density(text: str, limit: int = 2000) -> int:
    """Counts how many "real financial figures" (comma-grouped numbers,
    or parenthesized negatives) appear in the first `limit` characters of
    a chunk. A real statement table is dense with these; a table of
    contents entry or a passing footnote mention is not."""
    return len(re.findall(r"\d{1,3}(?:,\d{3})+|\(\d{1,3}(?:,\d{3})*\)", text[:limit]))


def _anchors_present(text: str, anchor_groups: list) -> bool:
    """anchor_groups is a list of groups, where each group is itself a
    list of alternative regex patterns for one required concept (e.g.
    "net income OR net loss"). Every group must have at least one match
    somewhere in `text` for this to return True - this is what lets us
    tell a real income statement (which will mention both a revenue line
    and a net income/loss line) apart from a stray paragraph that only
    happens to mention "net income" once in passing."""
    for group in anchor_groups:
        if not any(re.search(pattern, text, re.IGNORECASE) for pattern in group):
            return False
    return True


INCOME_STATEMENT_ANCHORS = [
    ["net income", "net loss"],
    ["total revenue", "net revenue", "net sales", "total net sales", r"\brevenue\b"],
]

BALANCE_SHEET_ANCHORS = [
    ["total assets"],
    ["total liabilities"],
]

CASH_FLOW_ANCHORS = [
    ["cash (?:flows? )?from operating", "operating activities", "net cash (?:from|used in|provided by) operations?"],
    ["investing activities", "financing activities", "net cash (?:from|used in|provided by) (?:investing|financing)"],
]

BANK_METRICS_ANCHORS = [
    ["return on (common )?equity", r"\bROE\b"],
    ["return on assets", r"\bROA\b"],
    ["efficiency ratio", "overhead ratio"],
]


def find_real_statement(
    full_text: str,
    heading_variations: list,
    anchor_groups: list,
    window: int = 8000,
    min_density: int = 20,
    anchor_window: int = 3000,
) -> str:
    """v3 statement locator: for every occurrence of every heading
    variation, pull the next `window` characters and score it. A
    candidate only counts at all if the required anchor terms (e.g. both
    a revenue line AND a net income line) appear within the first
    `anchor_window` characters - this rules out a table-of-contents entry
    (which has the heading but none of the real numbers nearby) and a
    stray footnote (which might have ONE anchor term but not all of
    them). Among the candidates that pass that bar, the one with the
    highest numeric density - the most comma-formatted figures packed
    into the start of the chunk - wins, since a real statement table is
    dense with numbers in a way prose never is.

    Falls back to the best "weak" candidate (anchors present but below
    min_density) if nothing clears the density bar, so a real but
    sparsely-formatted statement still gets picked up rather than
    returning nothing.
    """
    strong_candidates = []
    weak_candidates = []

    for heading in heading_variations:
        pattern = re.compile(re.escape(heading), re.IGNORECASE)
        for match in pattern.finditer(full_text):
            idx = match.start()
            chunk = full_text[idx:idx + window]
            if not _anchors_present(chunk[:anchor_window], anchor_groups):
                continue
            density = _numeric_density(chunk, 2000)
            if density >= min_density:
                strong_candidates.append((density, chunk))
            else:
                weak_candidates.append((density, chunk))

    if strong_candidates:
        strong_candidates.sort(key=lambda c: c[0], reverse=True)
        return strong_candidates[0][1]
    if weak_candidates:
        weak_candidates.sort(key=lambda c: c[0], reverse=True)
        return weak_candidates[0][1]
    return ""


def build_financial_statements_excerpt(full_text: str) -> tuple:
    income_statement_headings = [
        "Consolidated Statements of Income",
        "Consolidated Statements of Operations",
        "Consolidated Income Statements",
        "Income Statements",
        "Statements of Operations",
        "Income Statement",
    ]

    balance_sheet_headings = [
        "Consolidated Balance Sheets",
        "Consolidated Balance Sheet",
        "Balance Sheets",
        "Balance Sheet",
        "Statements of Financial Position",
        "Statement of Financial Position",
    ]

    cash_flow_headings = [
        "Consolidated Statements of Cash Flows",
        "Consolidated Statement of Cash Flows",
        "Cash Flows Statements",
        "Statements of Cash Flows",
        "Statement of Cash Flows",
        "Cash Flow Statement",
    ]

    statement_types = [
        ("Income Statement", income_statement_headings, INCOME_STATEMENT_ANCHORS),
        ("Balance Sheet", balance_sheet_headings, BALANCE_SHEET_ANCHORS),
        ("Cash Flow Statement", cash_flow_headings, CASH_FLOW_ANCHORS),
    ]

    found_sections = []
    missing_statements = []
    for name, headings, anchors in statement_types:
        section = find_real_statement(full_text, headings, anchors)
        if section:
            found_sections.append(section)
        else:
            missing_statements.append(name)

    excerpt = "\n\n".join(found_sections)
    if not excerpt:
        raise ValueError(
            "Could not locate any of the three core financial statements in this "
            "document using any known heading wording. This company may use "
            "wording we haven't seen yet."
        )

    # Banks/financial institutions report a separate "Selected Financial
    # Data" / "Financial Highlights" summary table with already-calculated
    # ratios (ROE, ROA, efficiency ratio, capital ratios) that don't
    # appear anywhere in the three statements above - pull that in too,
    # if present, so extract_financials_from_text() can see it.
    bank_metrics_headings = [
        "Selected Financial Data",
        "Financial Highlights",
        "Summary of Consolidated Financial Highlights",
        "Selected financial data",
    ]
    bank_metrics_section = find_real_statement(
        full_text,
        bank_metrics_headings,
        BANK_METRICS_ANCHORS,
        window=6000,
        anchor_window=5500,
        min_density=12,
    )
    if bank_metrics_section:
        excerpt += "\n\n" + bank_metrics_section

    return excerpt, missing_statements


def extract_financials_from_text(text: str, model: str = "claude-sonnet-4-6") -> dict:
    client = anthropic.Anthropic()

    response = client.messages.create(
        model=model,
        max_tokens=2000,
        system=SCHEMA_INSTRUCTIONS,
        messages=[{"role": "user", "content": text}],
    )

    raw_reply = response.content[0].text.strip()

    if raw_reply.startswith("```"):
        raw_reply = raw_reply.strip("`")
        raw_reply = raw_reply.replace("json\n", "", 1)

    try:
        return json.loads(raw_reply)
    except json.JSONDecodeError as e:
        raise ValueError(f"Claude did not return valid JSON. Raw reply was:\n{raw_reply}") from e


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python extract_financials.py <path_to_clean_text.txt>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1], "r") as f:
        source_text = f.read()

    trimmed, missing = build_financial_statements_excerpt(source_text)
    if missing:
        print(f"WARNING: could not find: {', '.join(missing)}", file=sys.stderr)

    result = extract_financials_from_text(trimmed)
    print(json.dumps(result, indent=2))
