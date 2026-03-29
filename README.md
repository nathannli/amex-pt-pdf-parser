# Amex Point PDF Parser

Small CLI for extracting table data from an American Express Membership Summary PDF
and estimating spend associated with food and drink transactions.

## Usage

Create or refresh the virtual environment:

```bash
uv sync --python 3.14
```

Run the parser against a statement PDF:

```bash
uv run parse-amex-points ~/Desktop/American\ Express\ -\ Membership\ Summary.pdf
```

Print the full JSON summary:

```bash
uv run parse-amex-points ~/Desktop/American\ Express\ -\ Membership\ Summary.pdf --json
```

Write the extracted transactions to CSV:

```bash
uv run parse-amex-points ~/Desktop/American\ Express\ -\ Membership\ Summary.pdf --csv transactions.csv
```
