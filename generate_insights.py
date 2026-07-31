"""
generate_insights.py

STAGE B: turn verified numbers into analyst-style narrative commentary.

Important design rule, carried over from every earlier stage: the AI is
NEVER allowed to invent or calculate a number here. It only reads the
ratios that ratio_engine.py already calculated (plain, deterministic
code) and writes commentary about them. If the AI's narrative claims a
number that isn't in the data we gave it, that's a bug in the prompt,
not an acceptable "AI being helpful."

Usage:
    python generate_insights.py analysis_output.json
"""

import sys
import json
import anthropic

INSTRUCTIONS = """
You are a financial analyst writing commentary for a board audience,
based ONLY on the ratio data provided to you in the user message.

Hard rules - these are not optional:
- Every specific number you mention (a ratio, a percentage, a dollar
  figure) MUST come directly from the data you were given. Never
  calculate a new number, estimate one, or recall one from general
  knowledge about this company.
- If data for a year or field is missing (null / n/a), say so plainly
  rather than guessing or filling the gap.
- Do not invent qualitative facts (lawsuits, acquisitions, executives)
  that are not present in the numeric data - you were not given that
  context, so do not reference it.
- If a "bank_ratios" section is present in the data, this is a bank or
  financial institution: use those metrics (return on equity, return on
  assets, efficiency ratio, loans-to-deposits ratio, capital ratios)
  instead of leaning on current ratio / gross margin / free cash flow,
  which don't apply to how banks report and will show as not applicable.
- Keep the tone factual and measured, not promotional.

Write the following sections, using clear plain-English headers exactly
as shown:

## Executive Summary
2-3 sentences on overall financial health, referencing at least one
specific ratio or figure from the data.

## Strengths
3-5 bullet points, each citing a specific number from the data.

## Watch Items / Possible Red Flags
2-4 bullet points on anything the ratios suggest is worth monitoring
(e.g. declining ratios, missing data, high leverage). Only flag things
the numbers actually support - do not speculate beyond the data.

## Year-over-Year Trend
A short paragraph describing how the ratios changed across the years
present in the data.

## Overall Assessment
1-2 sentences of plain-English conclusion.

Respond in Markdown. Do not include anything outside these five sections.
"""


def generate_insights(analysis_data: dict, model: str = "claude-sonnet-4-6") -> str:
    client = anthropic.Anthropic()

    response = client.messages.create(
        model=model,
        max_tokens=1500,
        system=INSTRUCTIONS,
        messages=[{"role": "user", "content": json.dumps(analysis_data, indent=2)}],
    )

    return response.content[0].text.strip()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python generate_insights.py <path_to_analysis_output.json>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        analysis_data = json.load(f)

    narrative = generate_insights(analysis_data)
    print(narrative)

    with open("insights.md", "w") as f:
        f.write(narrative)
    print("\n\nSaved to insights.md")
