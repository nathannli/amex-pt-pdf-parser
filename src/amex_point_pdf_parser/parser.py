from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

import pdfplumber


FOOD_DRINK_KEYWORDS = {
    "bakery",
    "bar",
    "cafe",
    "coffee",
    "dining",
    "doordash",
    "drink",
    "drinks",
    "carisma",
    "coco",
    "denovia",
    "dispatch",
    "food",
    "fresh",
    "galleria",
    "gong cha",
    "grocery",
    "groceries",
    "lcbo",
    "longo",
    "longo's",
    "metro",
    "restaurant",
    "restaurants",
    "skip the dishes",
    "tacos",
    "princesa",
    "tiger sugar",
    "uber eats",
}

TRANSACTION_STATUSES = {"Earned", "Adjusted", "Redeemed"}
AMOUNT_PATTERN = re.compile(r"-?\$?\(?\d[\d,]*\.\d{2}\)?")


@dataclass
class Transaction:
    date: str
    card: str
    status: str
    description: str
    amount: Decimal | None
    points: Decimal | None

    @property
    def is_food_drink(self) -> bool:
        haystack = self.description.lower()
        return any(keyword in haystack for keyword in FOOD_DRINK_KEYWORDS)


@dataclass
class Line:
    top: float
    words: list[dict]

    @property
    def texts(self) -> list[str]:
        return [word["text"] for word in self.words]

    def texts_in_range(self, start_x: float, end_x: float | None = None) -> list[str]:
        end = float("inf") if end_x is None else end_x
        return [word["text"] for word in self.words if start_x <= word["x0"] < end]

    def has_status(self) -> bool:
        return any(word["text"] in TRANSACTION_STATUSES for word in self.words)


def parse_decimal(value: str) -> Decimal | None:
    cleaned = value.strip().replace("$", "").replace(",", "")
    if not cleaned:
        return None
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def cluster_lines(words: list[dict], tolerance: float = 3.0) -> list[Line]:
    clusters: list[list[dict]] = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        if not clusters or abs(word["top"] - clusters[-1][0]["top"]) > tolerance:
            clusters.append([word])
        else:
            clusters[-1].append(word)

    lines: list[Line] = []
    for cluster in clusters:
        ordered = sorted(cluster, key=lambda item: item["x0"])
        top = sum(word["top"] for word in ordered) / len(ordered)
        lines.append(Line(top=top, words=ordered))
    return lines


def line_text(line: Line) -> str:
    return " ".join(line.texts).strip()


def find_neighbor_line(lines: list[Line], anchor_index: int, offset: int, max_gap: float = 22.0) -> Line | None:
    neighbor_index = anchor_index + offset
    if 0 <= neighbor_index < len(lines):
        neighbor = lines[neighbor_index]
        anchor = lines[anchor_index]
        if abs(neighbor.top - anchor.top) <= max_gap:
            return neighbor
    return None


def nearby_lines(lines: list[Line], index: int, max_distance: int = 2, max_gap: float = 25.0) -> list[Line]:
    anchor = lines[index]
    selected: list[Line] = []
    for offset in range(-max_distance, max_distance + 1):
        candidate_index = index + offset
        if 0 <= candidate_index < len(lines):
            candidate = lines[candidate_index]
            if abs(candidate.top - anchor.top) <= max_gap:
                selected.append(candidate)
    return selected


def extract_amounts(line: Line) -> tuple[Decimal | None, Decimal | None]:
    amount_words = line.texts_in_range(450, 520)
    points_words = line.texts_in_range(520, None)
    amount = parse_decimal(" ".join(amount_words)) if amount_words else None
    points = parse_decimal(" ".join(points_words)) if points_words else None
    return amount, points


def extract_date(lines: list[Line], index: int) -> str:
    month = ""
    day = ""
    for line in nearby_lines(lines, index):
        for token in line.texts_in_range(0, 90):
            if re.fullmatch(r"[A-Z][a-z]{2}\.", token):
                month = token
            elif re.fullmatch(r"\d{1,2}", token):
                day = token
    return " ".join(part for part in [month, day] if part).strip()


def extract_description(lines: list[Line], index: int) -> str:
    parts: list[str] = []
    for line in nearby_lines(lines, index):
        words = line.texts_in_range(220, 450)
        if words:
            parts.append(" ".join(words))
    return " ".join(parts).strip()


def is_header_or_footer(text: str) -> bool:
    lowered = text.lower()
    return (
        lowered.startswith("american express")
        or lowered.startswith("printed for")
        or lowered.startswith("nathan li")
        or lowered.startswith("date card status")
        or lowered.endswith("p.m.")
        or re.fullmatch(r"\d+\s+of\s+\d+", lowered) is not None
    )


def extract_transactions(pdf_path: Path) -> list[Transaction]:
    transactions: list[Transaction] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
            lines = cluster_lines(words)
            for index, line in enumerate(lines):
                if not line.has_status():
                    continue
                if is_header_or_footer(line_text(line)):
                    continue

                status = next(word["text"] for word in line.words if word["text"] in TRANSACTION_STATUSES)
                card_words = line.texts_in_range(90, 150)
                description = extract_description(lines, index)
                amount, points = extract_amounts(line)
                date = extract_date(lines, index)

                if not description or (amount is None and points is None):
                    continue

                transactions.append(
                    Transaction(
                        date=date,
                        card=" ".join(card_words).strip(),
                        status=status,
                        description=description,
                        amount=amount,
                        points=points,
                    )
                )
    return dedupe_transactions(transactions)


def dedupe_transactions(items: Iterable[Transaction]) -> list[Transaction]:
    seen: set[tuple[str, str, str, Decimal | None, Decimal | None]] = set()
    deduped: list[Transaction] = []
    for item in items:
        key = (item.date, item.status, item.description, item.amount, item.points)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def total_amount(items: Iterable[Transaction]) -> Decimal:
    total = Decimal("0")
    for item in items:
        if item.amount is not None:
            total += item.amount
    return total


def total_points(items: Iterable[Transaction]) -> Decimal:
    total = Decimal("0")
    for item in items:
        if item.points is not None:
            total += item.points
    return total


def build_summary(transactions: list[Transaction]) -> dict:
    food_drink = [item for item in transactions if item.is_food_drink]
    return {
        "transaction_count": len(transactions),
        "food_drink_transaction_count": len(food_drink),
        "food_drink_spend": f"{total_amount(food_drink):.2f}",
        "food_drink_points": f"{total_points(food_drink):.2f}",
        "food_drink_transactions": [asdict(item) for item in food_drink],
    }


def write_csv(path: Path, transactions: list[Transaction]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "card", "status", "description", "amount", "points", "is_food_drink"])
        for item in transactions:
            writer.writerow(
                [
                    item.date,
                    item.card,
                    item.status,
                    item.description,
                    f"{item.amount:.2f}" if item.amount is not None else "",
                    f"{item.points:.2f}" if item.points is not None else "",
                    "yes" if item.is_food_drink else "no",
                ]
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse Amex Membership Summary PDFs.")
    parser.add_argument("pdf", type=Path, help="Path to the American Express PDF")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    parser.add_argument("--csv", type=Path, help="Optional output CSV path")
    args = parser.parse_args()

    transactions = extract_transactions(args.pdf)
    if args.csv:
        write_csv(args.csv, transactions)

    summary = build_summary(transactions)
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(f"Transactions found: {summary['transaction_count']}")
        print(f"Food/drink transactions: {summary['food_drink_transaction_count']}")
        print(f"Food/drink spend: ${summary['food_drink_spend']}")
        print(f"Food/drink points: {summary['food_drink_points']}")

    return 0
