"""Generate synthetic car rental listings for the car_rentals schema.

Produces ~2,000 rows with realistic correlations:
- Vehicle class drives price range, seats, and make/model pool
- Suppliers present in every city
- Modern fleet vehicles (2023–2026), no high-mileage beaters
- Transmission mostly automatic, manual only on economy/compact

Usage: python scripts/generate_car_rentals.py
Output: data/car_rentals.json
"""

import json
import random
from pathlib import Path

SEED = 5555
TARGET_ROWS = 2000

CITIES = [
    "Toronto", "Vancouver", "Montreal", "Calgary", "Ottawa",
    "Halifax", "Victoria", "Edmonton", "Winnipeg", "Quebec City",
]

SUPPLIERS = ["Hertz", "Avis", "Enterprise", "Budget", "National"]

# Vehicle class -> (price_low, price_high, seats_options, fuel_weights, transmission_weights, models)
# models: list of (make, model_name) — realistic fleet vehicles
CLASSES = {
    "economy": {
        "price": (35, 55),
        "seats": [4, 5],
        "fuel": [("gas", 80), ("hybrid", 15), ("ev", 5)],
        "transmission": [("automatic", 85), ("manual", 15)],
        "models": [
            ("Toyota", "Corolla"), ("Honda", "Civic"), ("Hyundai", "Elantra"),
            ("Kia", "Forte"), ("Nissan", "Sentra"), ("Chevrolet", "Spark"),
            ("Toyota", "Yaris"), ("Kia", "Rio"),
        ],
    },
    "compact": {
        "price": (45, 70),
        "seats": [5],
        "fuel": [("gas", 75), ("hybrid", 20), ("ev", 5)],
        "transmission": [("automatic", 90), ("manual", 10)],
        "models": [
            ("Honda", "Civic"), ("Toyota", "Corolla"), ("Hyundai", "Elantra"),
            ("Kia", "Forte"), ("Nissan", "Sentra"), ("Ford", "Focus"),
            ("Chevrolet", "Cruze"), ("Honda", "Civic Sport"),
        ],
    },
    "midsize": {
        "price": (55, 95),
        "seats": [5],
        "fuel": [("gas", 70), ("hybrid", 25), ("ev", 5)],
        "transmission": [("automatic", 98), ("manual", 2)],
        "models": [
            ("Toyota", "Camry"), ("Honda", "Accord"), ("Hyundai", "Sonata"),
            ("Kia", "K5"), ("Nissan", "Altima"), ("Ford", "Fusion"),
            ("Chevrolet", "Malibu"), ("Toyota", "Camry Hybrid"),
        ],
    },
    "suv": {
        "price": (75, 150),
        "seats": [5, 7],
        "fuel": [("gas", 65), ("hybrid", 30), ("ev", 5)],
        "transmission": [("automatic", 100)],
        "models": [
            ("Toyota", "RAV4"), ("Honda", "CR-V"), ("Hyundai", "Tucson"),
            ("Ford", "Escape"), ("Chevrolet", "Equinox"), ("Nissan", "Rogue"),
            ("Jeep", "Cherokee"), ("Jeep", "Grand Cherokee"),
            ("Toyota", "Highlander"), ("Ford", "Explorer"),
            ("Kia", "Sportage"), ("Hyundai", "Santa Fe"),
        ],
    },
    "luxury": {
        "price": (150, 280),
        "seats": [4, 5],
        "fuel": [("gas", 60), ("hybrid", 25), ("ev", 15)],
        "transmission": [("automatic", 100)],
        "models": [
            ("BMW", "3 Series"), ("BMW", "5 Series"), ("Mercedes-Benz", "C-Class"),
            ("Mercedes-Benz", "E-Class"), ("BMW", "X3"), ("Mercedes-Benz", "GLC"),
            ("BMW", "X5"),
        ],
    },
    "van": {
        "price": (90, 160),
        "seats": [7, 8, 12],
        "fuel": [("gas", 90), ("hybrid", 10)],
        "transmission": [("automatic", 100)],
        "models": [
            ("Dodge", "Grand Caravan"), ("Chrysler", "Pacifica"),
            ("Toyota", "Sienna"), ("Honda", "Odyssey"),
            ("Kia", "Carnival"), ("Ford", "Transit"),
        ],
    },
}


def _pick_weighted(options_weights, rng):
    options, weights = zip(*options_weights)
    return rng.choices(options, weights=weights, k=1)[0]


def generate_row(row_id: int, rng: random.Random) -> dict:
    city = rng.choice(CITIES)
    supplier = rng.choice(SUPPLIERS)
    vehicle_class = rng.choices(
        list(CLASSES.keys()),
        weights=[20, 18, 18, 22, 10, 12],
        k=1,
    )[0]

    cls = CLASSES[vehicle_class]
    make, model = rng.choice(cls["models"])
    seats = rng.choice(cls["seats"])
    fuel = _pick_weighted(cls["fuel"], rng)
    transmission = _pick_weighted(cls["transmission"], rng)

    price_low, price_high = cls["price"]
    price_per_day = rng.randint(price_low, price_high)

    # Unlimited mileage: most rentals include it, some budget don't
    if supplier == "Budget" and vehicle_class in ("economy", "compact"):
        unlimited = "yes" if rng.random() < 0.6 else "no"
    else:
        unlimited = "yes" if rng.random() < 0.9 else "no"

    title = f"{make} {model} ({vehicle_class.title()})"

    return {
        "id": row_id,
        "title": title,
        "pickup_city": city,
        "vehicle_class": vehicle_class,
        "make": make,
        "model": model,
        "price_per_day": price_per_day,
        "seats": seats,
        "transmission": transmission,
        "fuel": fuel,
        "supplier": supplier,
        "unlimited_mileage": unlimited,
    }


def main():
    rng = random.Random(SEED)
    rows = [generate_row(i + 1, rng) for i in range(TARGET_ROWS)]

    out_path = Path(__file__).resolve().parent.parent / "data" / "car_rentals.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
