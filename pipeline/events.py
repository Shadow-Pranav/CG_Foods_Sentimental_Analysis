"""
Known real-world events to overlay on the sentiment timeline, per CONTEXT.md.

This is the single source of truth for event dates/labels: both
generate_sample_data.py (to cluster synthetic chatter around them) and
app/main.py (to expose them to the dashboard as timeline markers) import
from here so the two never drift apart.

Dates are best-effort placements within the documented reporting windows in
CONTEXT.md (exact dates vary by outlet); see the `note` field on each event.
"""

KNOWN_EVENTS = [
    {
        "id": "price_hike_nepal_2024",
        "date": "2024-09-10",
        "label": "Nepal price hike (~20%)",
        "category": "price_hike",
        "note": (
            "Nepali instant noodle makers, including Wai Wai, raised retail "
            "prices roughly 20% citing NPR depreciation, higher raw material "
            "costs, and rising labor/wage costs."
        ),
    },
    {
        "id": "quality_fine_2024",
        "date": "2024-11-25",
        "label": "KMC quality/health fine",
        "category": "quality_controversy",
        "note": (
            "Kathmandu Metropolitan City fined Chaudhary Group after testing "
            "found Wai Wai noodles did not meet quality standards (acid "
            "value out of spec), raising public health concerns."
        ),
    },
    {
        "id": "trademark_ruling_2025",
        "date": "2025-02-18",
        "label": "Trademark ruling vs. Current Noodles",
        "category": "litigation",
        "note": (
            "Patan High Court (and later a higher court) ordered Yashoda "
            "Foods' 'Current Noodles' to stop imitating Wai Wai's "
            "trademark/packaging."
        ),
    },
    {
        "id": "thailand_price_hike_2025",
        "date": "2025-06-05",
        "label": "Thailand price hike",
        "category": "price_hike",
        "note": (
            "Wai Wai (via CG's Thai operations) sought/implemented price "
            "increases tied to wheat flour, palm oil, and energy cost "
            "spikes."
        ),
    },
    {
        "id": "competitor_surge_2025",
        "date": "2025-10-12",
        "label": "Current/2PM Noodles competitor surge",
        "category": "competitor_launch",
        "note": (
            "Rising popularity of Current Noodles and 2PM Noodles in the "
            "spicy-noodle segment, eating into Wai Wai's home-market "
            "position in Nepal."
        ),
    },
    {
        "id": "india_expansion_2026",
        "date": "2026-03-01",
        "label": "India expansion push",
        "category": "market_expansion",
        "note": (
            "Wai Wai's aggressive India growth push and increased retail "
            "presence, independent of the Nepal revenue-decline narrative."
        ),
    },
]

# Overall collection window used by the sample-data generator (~24 months,
# per PROJECT_INSTRUCTIONS.md's "minimum trailing 18-24 months" scope note).
WINDOW_START = "2024-07-15"
WINDOW_END = "2026-07-15"
