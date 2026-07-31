FROM python:3.11-slim

# poppler-utils gives us the `pdftotext` command-line tool, which is
# dramatically faster than pure-Python PDF parsing for the large,
# 200-400 page annual reports this app is built to handle (benchmarked
# at roughly 40x faster on a 364-page 10-K). pdf_text_extractor.py uses
# it as the primary path and falls back to pdfplumber (already in
# requirements.txt) automatically if this binary is ever unavailable.
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run sets $PORT (typically 8080) and requires the container to
# listen on 0.0.0.0 at that port. This app is Streamlit, not a plain
# Python script that reads $PORT itself - it needs to be launched with
# `streamlit run`, with --server.port/--server.address passed explicitly,
# or Streamlit defaults to port 8501 on localhost only and the container
# never becomes reachable.
ENV PORT=8080
EXPOSE 8080

CMD streamlit run app.py --server.port $PORT --server.address 0.0.0.0
