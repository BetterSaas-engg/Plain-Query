"""Generate synthetic flight listings for the flights schema.

Produces ~2,000 rows with realistic correlations:
- Route distance drives base duration and price
- Stops add duration and reduce price
- Cabin class multiplies price
- Budget airlines are cheaper, fewer routes
- Month affects price (summer/holidays cost more)

Usage: python scripts/generate_flights.py
Output: data/flights.json
"""

import json
import random
from pathlib import Path

SEED = 3333
TARGET_ROWS = 2000

# Route definitions: (origin, destination, base_duration_hours, base_price_cad)
# Base = nonstop economy
ROUTES = {
    # Transatlantic
    ("Toronto", "London"):       (7,  800),
    ("Toronto", "Paris"):        (8,  850),
    ("Toronto", "Lisbon"):       (7,  780),
    ("Toronto", "Rome"):         (9,  900),
    ("Toronto", "Barcelona"):    (8,  870),
    ("Toronto", "Amsterdam"):    (7,  820),
    ("Toronto", "Dubai"):        (13, 1200),
    ("Montreal", "London"):      (7,  790),
    ("Montreal", "Paris"):       (7,  800),
    ("Montreal", "Lisbon"):      (7,  770),
    ("Montreal", "Amsterdam"):   (7,  810),
    ("Vancouver", "Tokyo"):      (10, 950),
    ("Vancouver", "London"):     (9,  900),
    ("Vancouver", "Paris"):      (10, 920),
    ("Vancouver", "Amsterdam"):  (10, 910),
    ("Calgary", "London"):       (9,  880),
    ("Calgary", "Tokyo"):        (11, 980),
    # Domestic / North America
    ("Toronto", "New York"):     (2,  250),
    ("Toronto", "Los Angeles"):  (5,  450),
    ("Toronto", "Miami"):        (3,  350),
    ("Toronto", "Cancun"):       (4,  400),
    ("Montreal", "New York"):    (2,  240),
    ("Montreal", "Miami"):       (4,  380),
    ("Montreal", "Cancun"):      (4,  420),
    ("Vancouver", "Los Angeles"):(3,  300),
    ("Vancouver", "Cancun"):     (5,  480),
    ("Calgary", "Los Angeles"):  (4,  350),
    ("Calgary", "Cancun"):       (5,  450),
    ("Ottawa", "New York"):      (2,  260),
    ("Ottawa", "Miami"):         (4,  390),
    ("Halifax", "New York"):     (3,  300),
    ("Halifax", "London"):       (6,  750),
    ("Halifax", "Miami"):        (4,  380),
}

# Airlines: (name, price_multiplier, allowed_cabin_classes, route_coverage_fraction)
AIRLINES = [
    ("Air Canada",  1.00, ["economy", "premium economy", "business", "first"], 1.0),
    ("WestJet",     0.90, ["economy", "premium economy", "business"], 0.7),
    ("Porter",      0.85, ["economy", "premium economy"], 0.4),
    ("Flair",       0.65, ["economy"], 0.3),
    ("Swoop",       0.60, ["economy"], 0.2),
]

CABIN_MULTIPLIERS = {
    "economy": 1.0,
    "premium economy": 1.6,
    "business": 3.0,
    "first": 5.5,
}

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# Seasonal price factors (1-indexed)
MONTH_FACTORS = {
    "January": 0.85, "February": 0.80, "March": 0.90, "April": 0.95,
    "May": 1.00, "June": 1.15, "July": 1.25, "August": 1.20,
    "September": 0.90, "October": 0.95, "November": 0.85, "December": 1.15,
}


def generate_row(row_id: int, rng: random.Random, routes_list: list) -> dict:
    origin, destination = rng.choice(routes_list)
    base_duration, base_price = ROUTES[(origin, destination)]

    # Pick airline — weighted toward Air Canada / WestJet
    airline_def = rng.choices(
        AIRLINES,
        weights=[35, 30, 15, 12, 8],
        k=1,
    )[0]
    airline_name, price_mult, cabin_classes, _ = airline_def

    cabin_class = rng.choices(
        cabin_classes,
        weights=[60] + [40 // max(1, len(cabin_classes) - 1)] * (len(cabin_classes) - 1),
        k=1,
    )[0]

    # Stops: 0–2, weighted toward nonstop for short routes
    if base_duration <= 3:
        stops = rng.choices([0, 1], weights=[80, 20], k=1)[0]
    elif base_duration <= 7:
        stops = rng.choices([0, 1, 2], weights=[50, 35, 15], k=1)[0]
    else:
        stops = rng.choices([0, 1, 2, 3], weights=[30, 40, 20, 10], k=1)[0]

    # Duration: base + stops add 2–4 hours each + noise
    duration_hours = base_duration + stops * rng.randint(2, 4)
    duration_hours = max(1, duration_hours + rng.randint(-1, 1))

    month = rng.choice(MONTHS)

    # Price: base * airline * cabin * season * noise + stop discount
    price = base_price * price_mult * CABIN_MULTIPLIERS[cabin_class]
    price *= MONTH_FACTORS[month]
    price *= rng.uniform(0.85, 1.15)
    # Stops reduce price slightly
    if stops > 0:
        price *= (1 - stops * 0.08)
    price = max(50, int(round(price, -1)))

    title = f"{airline_name} {origin} to {destination}"
    if stops == 0:
        title += " (nonstop)"
    else:
        title += f" ({stops} stop{'s' if stops > 1 else ''})"

    return {
        "id": row_id,
        "title": title,
        "origin": origin,
        "destination": destination,
        "airline": airline_name,
        "stops": stops,
        "price": price,
        "duration_hours": duration_hours,
        "cabin_class": cabin_class,
        "month": month,
    }


def main():
    rng = random.Random(SEED)

    # Build route list, filtering by airline coverage
    routes_list = list(ROUTES.keys())

    rows = [generate_row(i + 1, rng, routes_list) for i in range(TARGET_ROWS)]

    out_path = Path(__file__).resolve().parent.parent / "data" / "flights.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
