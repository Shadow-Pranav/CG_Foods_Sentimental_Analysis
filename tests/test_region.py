"""
Region assignment logic (step 3): pipeline/generate_sample_data.py's
pick_region() weighting and pipeline/collect.py's location-text heuristic.
"""

import random
from collections import Counter

import pytest

from pipeline.collect import guess_region_from_location_text
from pipeline.generate_sample_data import pick_region


def test_pick_region_returns_only_valid_labels():
    rng = random.Random(1)
    for _ in range(200):
        assert pick_region(rng, (0.3, 0.3, 0.4)) in {"nepal", "india", "unknown"}


def test_pick_region_respects_extreme_weights():
    rng = random.Random(2)
    results = {pick_region(rng, (1.0, 0.0, 0.0)) for _ in range(100)}
    assert results == {"nepal"}


@pytest.mark.parametrize("weights", [(0.75, 0.10, 0.15), (0.15, 0.65, 0.20), (0.05, 0.05, 0.90)])
def test_pick_region_empirical_distribution_matches_weights(weights):
    rng = random.Random(42)
    n = 4000
    counts = Counter(pick_region(rng, weights) for _ in range(n))
    empirical = (counts["nepal"] / n, counts["india"] / n, counts["unknown"] / n)
    for expected, actual in zip(weights, empirical):
        assert abs(expected - actual) < 0.04  # generous tolerance for a 4000-sample draw


@pytest.mark.parametrize("location,expected", [
    ("Kathmandu, Nepal", "nepal"),
    ("Pokhara", "nepal"),
    ("Guwahati, Assam", "india"),
    ("Mumbai, India", "india"),
    ("North East India", "india"),
    ("", "unknown"),
    (None, "unknown"),
    ("probably somewhere on earth", "unknown"),
    ("Wellington, New Zealand", "unknown"),
])
def test_guess_region_from_location_text(location, expected):
    assert guess_region_from_location_text(location) == expected


def test_guess_region_does_not_false_positive_on_bare_country_code_substrings():
    """'in' and 'np' as bare substrings inside unrelated words shouldn't
    trigger a match -- e.g. 'skiing' contains 'in', 'napkin' contains
    'np'-adjacent letters. The heuristic is intentionally conservative."""
    assert guess_region_from_location_text("I enjoy skiing and hiking") == "unknown"
