"""Generate synthetic used-car listings for the cars schema.

Produces ~2,000 rows with realistic correlations:
- Real make→model pairings with multi-word variants
- Correct body_type per model
- Plausible fuel per model
- Correlated year/price/mileage (newer → pricier, lower km)

Usage: python scripts/generate_cars.py
Output: data/cars.json
"""

import json
import random
from pathlib import Path

SEED = 42
TARGET_ROWS = 2000

# --- Make → Model definitions ---
# Each model: (name, body_types, base_price_cad, fuel_options, variants)
#   base_price_cad: MSRP-ish for a new one; depreciation is applied per year
#   fuel_options: weighted list of (fuel, weight)
#   variants: list of suffix strings (applied to ~40% of rows)

CATALOG = {
    "Honda": [
        ("Civic", ["sedan", "hatchback"], 28000, [("gas", 85), ("hybrid", 15)],
         ["Sport", "Si", "Touring", "EX", "LX", "Sport Touring"]),
        ("Accord", ["sedan"], 35000, [("gas", 70), ("hybrid", 30)],
         ["Sport", "Touring", "EX-L", "Sport 2.0T"]),
        ("CR-V", ["suv"], 38000, [("gas", 60), ("hybrid", 40)],
         ["EX", "EX-L", "Touring", "Sport"]),
        ("Pilot", ["suv"], 45000, [("gas", 90), ("hybrid", 10)],
         ["EX-L", "Touring", "Sport", "TrailSport"]),
    ],
    "Toyota": [
        ("Corolla", ["sedan", "hatchback"], 25000, [("gas", 80), ("hybrid", 20)],
         ["LE", "SE", "XSE", "Nightshade"]),
        ("Camry", ["sedan"], 33000, [("gas", 65), ("hybrid", 35)],
         ["LE", "SE", "XSE", "TRD", "Nightshade"]),
        ("RAV4", ["suv"], 36000, [("gas", 50), ("hybrid", 40), ("ev", 10)],
         ["LE", "XLE", "XSE", "Trail", "Prime"]),
        ("Tacoma", ["truck"], 40000, [("gas", 95), ("diesel", 5)],
         ["SR5", "TRD Sport", "TRD Off-Road", "Limited"]),
    ],
    "Ford": [
        ("Focus", ["sedan", "hatchback"], 22000, [("gas", 95), ("ev", 5)],
         ["SE", "ST", "Titanium", "SEL"]),
        ("Escape", ["suv"], 35000, [("gas", 60), ("hybrid", 30), ("ev", 10)],
         ["SE", "SEL", "Titanium", "ST-Line"]),
        ("F-150", ["truck"], 48000, [("gas", 70), ("hybrid", 15), ("ev", 10), ("diesel", 5)],
         ["XLT", "Lariat", "King Ranch", "Platinum", "Lightning"]),
        ("Mustang", ["coupe"], 42000, [("gas", 95), ("ev", 5)],
         ["EcoBoost", "GT", "Mach 1", "Dark Horse"]),
    ],
    "BMW": [
        ("3 Series", ["sedan"], 50000, [("gas", 75), ("hybrid", 20), ("ev", 5)],
         ["330i", "330i xDrive", "M340i", "330e"]),
        ("X3", ["suv"], 52000, [("gas", 70), ("hybrid", 20), ("ev", 10)],
         ["xDrive30i", "M40i", "xDrive30e"]),
        ("X5", ["suv"], 72000, [("gas", 70), ("hybrid", 25), ("ev", 5)],
         ["xDrive40i", "xDrive45e", "M50i"]),
        ("4 Series", ["coupe"], 55000, [("gas", 90), ("hybrid", 10)],
         ["430i", "430i xDrive", "M440i"]),
    ],
    "Chevrolet": [
        ("Cruze", ["sedan", "hatchback"], 22000, [("gas", 95), ("diesel", 5)],
         ["LS", "LT", "Premier", "RS"]),
        ("Equinox", ["suv"], 33000, [("gas", 80), ("ev", 20)],
         ["LS", "LT", "RS", "Premier"]),
        ("Silverado", ["truck"], 45000, [("gas", 75), ("diesel", 20), ("ev", 5)],
         ["Custom", "LT", "RST", "LTZ", "High Country"]),
        ("Malibu", ["sedan"], 27000, [("gas", 90), ("hybrid", 10)],
         ["LS", "RS", "LT", "Premier"]),
    ],
    "Hyundai": [
        ("Elantra", ["sedan"], 24000, [("gas", 80), ("hybrid", 20)],
         ["SE", "SEL", "N Line", "Limited"]),
        ("Tucson", ["suv"], 34000, [("gas", 55), ("hybrid", 35), ("ev", 10)],
         ["SE", "SEL", "N Line", "Limited"]),
        ("Santa Fe", ["suv"], 42000, [("gas", 55), ("hybrid", 35), ("ev", 10)],
         ["SE", "SEL", "XRT", "Limited", "Calligraphy"]),
        ("Kona", ["suv"], 28000, [("gas", 50), ("ev", 40), ("hybrid", 10)],
         ["SE", "SEL", "N Line", "Limited", "Electric"]),
    ],
    "Mazda": [
        ("Mazda3", ["sedan", "hatchback"], 26000, [("gas", 100)],
         ["GX", "GS", "GT", "Sport"]),
        ("CX-5", ["suv"], 35000, [("gas", 100)],
         ["GX", "GS", "GT", "Sport", "Signature"]),
        ("CX-9", ["suv"], 44000, [("gas", 100)],
         ["GS", "GS-L", "GT", "Signature"]),
        ("MX-5", ["coupe"], 35000, [("gas", 100)],
         ["Sport", "GS-P", "GT", "RF"]),
    ],
    "Subaru": [
        ("Impreza", ["sedan", "hatchback"], 25000, [("gas", 100)],
         ["Base", "Sport", "Touring", "RS"]),
        ("Outback", ["suv"], 37000, [("gas", 100)],
         ["Base", "Touring", "Limited", "Wilderness", "Premier"]),
        ("Forester", ["suv"], 35000, [("gas", 100)],
         ["Base", "Touring", "Sport", "Limited", "Premier"]),
        ("Crosstrek", ["suv"], 30000, [("gas", 80), ("hybrid", 20)],
         ["Base", "Touring", "Sport", "Limited"]),
    ],
    "Nissan": [
        ("Sentra", ["sedan"], 23000, [("gas", 100)],
         ["S", "SV", "SR", "SR Premium"]),
        ("Rogue", ["suv"], 34000, [("gas", 100)],
         ["S", "SV", "SL", "Platinum"]),
        ("Altima", ["sedan"], 30000, [("gas", 100)],
         ["S", "SV", "SR", "SL", "Platinum"]),
        ("Leaf", ["hatchback"], 38000, [("ev", 100)],
         ["S", "SV", "SL Plus"]),
    ],
    "Volkswagen": [
        ("Jetta", ["sedan"], 25000, [("gas", 100)],
         ["S", "SE", "SEL", "GLI"]),
        ("Tiguan", ["suv"], 36000, [("gas", 100)],
         ["S", "SE", "SE R-Line", "SEL", "SEL R-Line"]),
        ("Golf", ["hatchback"], 30000, [("gas", 80), ("ev", 20)],
         ["TSI", "GTI", "R"]),
        ("Atlas", ["suv"], 44000, [("gas", 100)],
         ["S", "SE", "SE w/Tech", "SEL", "SEL Premium"]),
    ],
}

COLORS = ["red", "blue", "black", "white", "silver", "grey", "green"]
COLOR_WEIGHTS = [8, 10, 22, 25, 18, 14, 3]  # white/black/silver dominate


def _pick_weighted(options_weights: list[tuple[str, int]], rng: random.Random) -> str:
    options, weights = zip(*options_weights)
    return rng.choices(options, weights=weights, k=1)[0]


def generate_row(row_id: int, rng: random.Random) -> dict:
    make = rng.choice(list(CATALOG.keys()))
    model_def = rng.choice(CATALOG[make])
    model_base, body_types, base_price, fuel_options, variants = model_def

    # Body type — pick from the model's allowed types
    body_type = rng.choice(body_types)

    # Fuel — weighted pick per model
    fuel = _pick_weighted(fuel_options, rng)

    # Model name — ~40% get a variant suffix
    if rng.random() < 0.40 and variants:
        variant = rng.choice(variants)
        model = f"{model_base} {variant}"
    else:
        model = model_base

    # Year: 2005–2026, weighted toward newer
    year = rng.choices(
        range(2005, 2027),
        weights=[1 + (y - 2005) for y in range(2005, 2027)],
        k=1,
    )[0]

    # Mileage: ~15,000 km/year with noise, floored at 0
    age = 2026 - year
    avg_km_per_year = rng.gauss(15000, 3000)
    mileage = max(0, int(age * avg_km_per_year + rng.gauss(0, 5000)))
    # New cars (2026) get very low mileage
    if year == 2026:
        mileage = rng.randint(0, 5000)

    # Price: depreciate from base
    # ~8-12% depreciation per year, plus mileage penalty
    annual_depreciation = rng.uniform(0.08, 0.12)
    depreciation_factor = (1 - annual_depreciation) ** age
    mileage_penalty = max(0, (mileage - 50000) * 0.02) if mileage > 50000 else 0
    price = base_price * depreciation_factor - mileage_penalty
    # Add noise (±10%)
    price = price * rng.uniform(0.90, 1.10)
    # BMW/luxury floor higher
    min_price = 4000 if make in ("BMW",) else 2500
    price = max(min_price, int(round(price, -2)))  # round to nearest 100

    color = rng.choices(COLORS, weights=COLOR_WEIGHTS, k=1)[0]

    title = f"{year} {make} {model}"

    return {
        "id": row_id,
        "title": title,
        "make": make,
        "model": model,
        "year": year,
        "price": price,
        "mileage": mileage,
        "body_type": body_type,
        "fuel": fuel,
        "color": color,
    }


def main():
    rng = random.Random(SEED)
    rows = [generate_row(i + 1, rng) for i in range(TARGET_ROWS)]

    out_path = Path(__file__).resolve().parent.parent / "data" / "cars.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
