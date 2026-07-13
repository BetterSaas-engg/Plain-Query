"""Generate synthetic hotel listings for the hotels schema.

Produces ~2,000 rows with realistic correlations:
- Star rating drives price range and guest rating
- Property type constrains star rating and price
- City→neighborhood pairings are real
- Multi-word names exercise substring matching

Usage: python scripts/generate_hotels.py
Output: data/hotels.json
"""

import json
import random
from pathlib import Path

SEED = 7777
TARGET_ROWS = 2000

CITIES_NEIGHBORHOODS = {
    "Toronto": [
        "Downtown", "Yorkville", "Queen West", "Liberty Village",
        "The Distillery District", "King West", "Entertainment District",
        "Harbourfront", "Midtown", "North York",
    ],
    "Vancouver": [
        "Downtown", "Gastown", "Yaletown", "Kitsilano", "West End",
        "Coal Harbour", "Mount Pleasant", "Granville Island",
        "Robson Street", "English Bay",
    ],
    "Montreal": [
        "Old Montreal", "Plateau Mont-Royal", "Downtown", "Mile End",
        "Griffintown", "Quartier Latin", "Westmount",
        "Little Italy", "Villeray", "Saint-Henri",
    ],
    "Calgary": [
        "Downtown", "Beltline", "Kensington", "Inglewood",
        "Mission", "Bridgeland", "East Village", "Eau Claire",
    ],
    "Ottawa": [
        "ByWard Market", "Downtown", "Centretown", "Glebe",
        "Westboro", "Sandy Hill", "Little Italy",
    ],
    "Halifax": [
        "Downtown", "Waterfront", "North End", "South End",
        "Spring Garden", "Quinpool",
    ],
    "Victoria": [
        "Inner Harbour", "Downtown", "James Bay", "Fernwood",
        "Oak Bay", "Fairfield",
    ],
    "Quebec City": [
        "Old Quebec", "Saint-Roch", "Montcalm", "Saint-Jean-Baptiste",
        "Petit Champlain", "Place Royale",
    ],
    "Winnipeg": [
        "Downtown", "Exchange District", "Osborne Village",
        "The Forks", "River Heights", "St. Boniface",
    ],
    "Edmonton": [
        "Downtown", "Old Strathcona", "Whyte Avenue", "West Edmonton",
        "Ice District", "Oliver",
    ],
}

# Property type configs: (star_range, base_price_range, breakfast_weight_yes)
PROPERTY_TYPES = {
    "hostel":    ((1, 2), (30, 80),    5),
    "bnb":       ((2, 4), (80, 200),  60),
    "apartment": ((2, 4), (90, 250),  10),
    "hotel":     ((2, 5), (120, 500), 40),
    "resort":    ((4, 5), (250, 800), 70),
}

# Name templates per property type
NAME_TEMPLATES = {
    "hostel": [
        "{city} Central Hostel", "{neighborhood} Backpackers",
        "The {city} Lodge", "{neighborhood} Budget Inn",
        "HI {city} Downtown", "{city} Travellers Hostel",
        "Hostel {neighborhood}", "Urban Base {city}",
    ],
    "bnb": [
        "{neighborhood} Bed and Breakfast", "The {neighborhood} Suite",
        "Cozy {neighborhood} BnB", "{city} Heritage BnB",
        "Charming {neighborhood} Stay", "The Little Inn {neighborhood}",
        "{neighborhood} Guesthouse", "Maple Leaf BnB {city}",
    ],
    "apartment": [
        "{neighborhood} Suites", "The {neighborhood} Apartment",
        "{city} Stay Apartments", "Urban Living {neighborhood}",
        "{neighborhood} Loft", "The Residence {neighborhood}",
        "Cityside Suites {city}", "{neighborhood} Flat",
    ],
    "hotel": [
        "Hotel {neighborhood}", "The {city} Grand Hotel",
        "{neighborhood} Inn and Suites", "Comfort Inn {neighborhood}",
        "Holiday Inn {city} {neighborhood}", "Best Western {city}",
        "The {neighborhood} Hotel", "Marriott {city} {neighborhood}",
        "Hilton {city} {neighborhood}", "Hyatt {neighborhood}",
        "Radisson {city}", "Delta {city} {neighborhood}",
        "Courtyard {city} {neighborhood}", "Fairmont {city}",
        "Chelsea Hotel {city}", "Westin {city} {neighborhood}",
        "Sheraton {city}", "Novotel {city} {neighborhood}",
    ],
    "resort": [
        "The {city} Resort and Spa", "{city} Lakeside Resort",
        "{neighborhood} Grand Resort", "Four Seasons {city}",
        "Ritz-Carlton {city}", "Shangri-La {city}",
        "The {neighborhood} Spa Resort", "Fairmont {city} Resort",
        "Rosewood {city}", "Mandarin Oriental {city}",
        "{city} Waterfront Resort", "The {neighborhood} Grand",
    ],
}

COLORS_NOT_NEEDED = True  # hotels don't have colors, just a reminder


def generate_row(row_id: int, rng: random.Random) -> dict:
    city = rng.choice(list(CITIES_NEIGHBORHOODS.keys()))
    neighborhood = rng.choice(CITIES_NEIGHBORHOODS[city])

    # Property type — weighted toward hotels
    prop_type = rng.choices(
        list(PROPERTY_TYPES.keys()),
        weights=[10, 15, 15, 45, 15],
        k=1,
    )[0]

    star_range, price_range, breakfast_yes_pct = PROPERTY_TYPES[prop_type]
    star_rating = rng.randint(star_range[0], star_range[1])

    # Price correlates with stars and property type
    price_low, price_high = price_range
    # Scale price up with star rating
    star_factor = (star_rating - 1) / 4  # 0.0 to 1.0
    base = price_low + (price_high - price_low) * star_factor
    price_per_night = max(1, int(base * rng.uniform(0.75, 1.30)))

    # Guest rating loosely tracks star rating (1–10 scale)
    # Base: star * 2, then noise
    guest_base = star_rating * 1.8 + rng.gauss(0.5, 0.8)
    guest_rating = max(1, min(10, int(round(guest_base))))

    # Breakfast
    breakfast = "yes" if rng.randint(1, 100) <= breakfast_yes_pct else "no"

    # Name from template
    template = rng.choice(NAME_TEMPLATES[prop_type])
    name = template.format(city=city, neighborhood=neighborhood)

    return {
        "id": row_id,
        "name": name,
        "city": city,
        "neighborhood": neighborhood,
        "star_rating": star_rating,
        "price_per_night": price_per_night,
        "guest_rating": guest_rating,
        "property_type": prop_type,
        "breakfast_included": breakfast,
    }


def main():
    rng = random.Random(SEED)
    rows = [generate_row(i + 1, rng) for i in range(TARGET_ROWS)]

    out_path = Path(__file__).resolve().parent.parent / "data" / "hotels.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
