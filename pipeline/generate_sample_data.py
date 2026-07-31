"""
Generate a realistic synthetic dataset for the Wai Wai / CG Foods Sentiment
Tracker, standing in for pipeline/collect.py in this environment (no live
API keys / scraping access — see DATA_SOURCES.md and PROJECT_INSTRUCTIONS.md
"Known Constraints").

Produces 200-400 rows spanning the ~24 month window defined in
pipeline/events.py, distributed across youtube/twitter/facebook, with text
that plausibly clusters around the known real-world events from CONTEXT.md
(price hikes, the 2024 quality/health fine, the Current Noodles trademark
dispute, competitor gains, India expansion) as well as steady-state
praise/complaint chatter (taste, nostalgia, spice, packaging, availability).

Output matches the "Suggested Schema (post-collection, pre-cleaning)" table
in DATA_SOURCES.md: id, platform, text_raw, timestamp, source_ref,
engagement, language_guess (left blank here; populated by pipeline/clean.py).
Adds a pseudonymous author_id (per DATA_SOURCES.md's "Legal / Ethical
Notes": no PII beyond what's needed for dedup) and a gold_sentiment column
used only as a synthetic-data proxy for the METHODOLOGY.md gold-sample
comparison (see classify.py), since there is no human researcher available
in this environment to hand-label a real 50-100 record gold sample. This
column is clearly synthetic-only and would not exist when running the real
pipeline/collect.py path.
"""

import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from events import KNOWN_EVENTS, WINDOW_START, WINDOW_END

RNG_SEED = 42
N_ROWS = 320
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "raw_comments.csv"

PLATFORM_WEIGHTS = {"youtube": 0.55, "twitter": 0.28, "facebook": 0.17}

# ---------------------------------------------------------------------------
# Template banks. Each bank is tagged with a theme (matching CONTEXT.md's
# seed dictionary: price, spice, packaging, quality, taste, nostalgia,
# availability, competitor, legal) and an intended sentiment used only as a
# synthetic gold-label proxy for the classifier comparison step.
# ---------------------------------------------------------------------------

PRICE_COMPLAINTS = [
    "Why did Wai Wai raise the price again? A packet used to be so much cheaper.",
    "20% price hike for instant noodles is rough in this economy.",
    "Wai Wai price le dukhcha yar, ali ali garera sabai mahenga bhayo.",
    "Paying almost double for the same small packet of Wai Wai now.",
    "The price to quantity ratio has gotten so bad since the hike.",
    "Even instant noodles aren't affordable anymore, thanks CG Foods.",
    "Price badhyo tara quantity ustai xa, feeling a bit cheated about it.",
    "The Rs price jump per packet doesn't make sense when the size stayed the same.",
    "Noodle prices going up every few months now, wages sure aren't.",
    "Wai Wai ekdam mahango vayo, pahile kati sasto thiyo yaad xa.",
    "Instant noodles used to be the cheap meal option, not anymore.",
    "Another price bump on Wai Wai, at this rate it's not a budget snack.",
]

THAILAND_PRICE_COMPLAINTS = [
    "Wai Wai prices went up here in Thailand too, blaming palm oil and wheat costs apparently.",
    "Energy costs, wheat flour, palm oil - now noodles are caught up in it too.",
    "Noticed the Thai packs of Wai Wai got pricier this month.",
    "Cost of living crisis reaching even the instant noodle aisle now.",
    "Wheat and palm oil prices going up, guess who pays for it - us, the noodle buyers.",
]

QUALITY_COMPLAINTS = [
    "Did anyone see the news about Wai Wai failing the quality test? Kind of worried now.",
    "KMC fined CG Foods for Wai Wai not meeting acid value standards, that's scary for something I eat weekly.",
    "Stopped buying Wai Wai after hearing about the quality violation news.",
    "Health hazard headlines about Wai Wai are concerning as a regular customer.",
    "Quality test failed matlab hamro health sanga khelawad vairaxa jasto lagcha.",
    "This quality controversy is going to hurt their sales for sure.",
    "Wondering if the penalty changes anything about how they actually make the product now.",
    "First price hike, now a quality penalty? Rough year for Wai Wai.",
    "Read that the acid value test failed, not touching Wai Wai till they clarify.",
    "CG Foods needs to explain the quality report properly, can't just brush this off.",
    "Kathmandu Metropolitan City fining them over quality is a bigger deal than people realize.",
    "Genuinely reconsidering buying Wai Wai after the health standards news.",
    "Quality bata compromise garepachi mann pardaina malai.",
    "Anyone know if the acid value issue has been addressed since the penalty?",
]

SPICE_COMPLAINTS = [
    "The spice packet is nowhere near as strong as it used to be.",
    "Wai Wai masala ali halka bhayo, pahile jasto jhamjham hudaina.",
    "They shrank the chili oil sachet again, not a fan of that.",
    "Not spicy enough anymore, had to add my own chili flakes.",
    "Miss the old spice level, this batch tastes watered down.",
    "The tarka packet used to actually burn a little, now it's mild.",
]

PACKAGING_COMPLAINTS = [
    "New packaging looks cheaper, feels flimsier than before.",
    "Wrapper tears way too fast now compared to old packets.",
    "Why change the packet design if the product quality is questionable now.",
    "The plastic wrap seal quality dropped a lot recently.",
    "Packaging change right after the quality news looks more like spin than a real response.",
]

AVAILABILITY_COMPLAINTS = [
    "Couldn't find Wai Wai anywhere near my area in Assam for weeks.",
    "Shelves keep running out of Wai Wai at every store I check.",
    "Local shop stopped stocking Wai Wai, had to switch brands temporarily.",
    "Why is Wai Wai suddenly out of stock everywhere near me.",
]

COMPETITOR_COMPARISON = [
    "Switched to Current Noodles because it's spicier and cheaper too.",
    "2PM Noodles gives way more masala for the price these days.",
    "Current Noodles ekdam ramro xa, spicy khojne haru le try garnu parxa.",
    "Comparing Wai Wai vs Current Noodles, the latter wins on heat level for me.",
    "Everyone at my hostel switched to 2PM Noodles this year.",
    "Current Noodles is basically eating into Wai Wai's spot in the spicy segment.",
    "Tried Maggi vs Wai Wai side by side, still prefer Wai Wai for nostalgia but Maggi cooks faster.",
    "Current Noodles taste ali spicy xa, Wai Wai bhanda differently.",
    "Not saying Wai Wai is bad but 2PM Noodles nails the spicy craving better.",
    "My whole family switched to Current Noodles after the price hikes on Wai Wai.",
]

TRADEMARK_CHATTER = [
    "Heard the court ordered Current Noodles to stop copying Wai Wai's packaging.",
    "Kind of feel bad for Current Noodles, they were just getting popular and now this lawsuit.",
    "Wai Wai going after a smaller local brand feels like corporate bullying to me.",
    "Fair enough, the Current Noodles packet did look almost identical to Wai Wai's.",
    "Trademark case ruling was overdue, the packaging copy was pretty obvious.",
    "This lawsuit drama is getting more attention online than the noodles themselves.",
    "Patan High Court ruling against Current Noodles is all anyone's talking about today.",
    "Not sure who's right here, but the packaging really did look copied.",
    "Big brand vs small challenger always makes people take sides online.",
]

TASTE_PRAISE = [
    "Still the best masala noodles, taste never gets old.",
    "Wai Wai ko taste ustai maja cha, kehi pani change vako xaina.",
    "That tangy masala flavor hits different every single time.",
    "Nothing beats a hot bowl of Wai Wai on a rainy day.",
    "The flavor packet balance is spot on for me.",
    "Crunched it dry with the masala as a snack, still amazing.",
    "Been eating instant noodles for years and Wai Wai's flavor still wins.",
    "The masala mixed into dry noodles as a snack is unbeatable.",
]

NOSTALGIA_PRAISE = [
    "Grew up eating Wai Wai after school, still my comfort food.",
    "This takes me straight back to childhood, mummy used to make it every evening.",
    "Bachpan ko taste, kehi le replace garna sakdaina.",
    "My hostel days were basically fueled by Wai Wai packets.",
    "Nothing hits the nostalgia quite like the smell of Wai Wai masala.",
    "Every rainy season reminds me of Wai Wai with mom growing up.",
    "This brand is basically a core childhood memory for me at this point.",
]

AVAILABILITY_PRAISE = [
    "You can find Wai Wai literally everywhere in the North East, love that.",
    "Every small shop in my town stocks Wai Wai, so convenient.",
    "Never had trouble finding Wai Wai even in the smallest villages.",
    "Wai Wai being available at every corner shop is underrated.",
]

INDIA_EXPANSION_POSITIVE = [
    "Wai Wai shelves are expanding so much in India stores lately, great to see.",
    "Noticed way more Wai Wai stock in supermarkets here in India this year.",
    "Good to see Wai Wai pushing hard into the Indian market, well deserved.",
    "CG Foods India growth is showing, Wai Wai is everywhere in stores now.",
    "New Wai Wai displays popping up in Indian supermarkets, big expansion push clearly.",
    "Wai Wai is becoming a lot more visible in Indian stores outside the North East now.",
]

GENERAL_NEUTRAL = [
    "Anyone know the calorie count on a Wai Wai packet?",
    "What's the difference between Wai Wai chicken and veg masala?",
    "Is Wai Wai available outside South Asia?",
    "Making a video comparing instant noodle brands, Wai Wai included.",
    "Does Wai Wai use palm oil in the noodle cake?",
    "How many packets come in the family pack usually?",
    "What year did Wai Wai actually launch?",
    "Trying to find the nutrition label for Wai Wai chicken flavor.",
]

# (bank, theme, sentiment_proxy, sampling_weight). Weights bias the
# steady-state background mix toward a plausible baseline (taste/nostalgia
# praise slightly outweighing complaints day-to-day) so that the negative
# spikes clustered around known events (see event_allocation below) read as
# genuine dips rather than the whole dataset being uniformly negative.
# region_weights = (p_nepal, p_india, p_unknown), summing to 1.0. Reflects
# CONTEXT.md's framing: Wai Wai is the declining-at-home / growing-abroad
# brand, with Nepal being the smaller home base and India (particularly
# North East India, ~80% share there) the larger, growing volume source.
# Banks with explicit geographic content (India-expansion praise, North
# East availability mentions, Nepali-government-agency quality/legal
# content) are weighted accordingly rather than left uniform.
NEPAL_LEANING = (0.55, 0.20, 0.25)
INDIA_LEANING = (0.15, 0.65, 0.20)
STRONG_NEPAL = (0.75, 0.10, 0.15)
STRONG_INDIA = (0.05, 0.85, 0.10)
MIXED = (0.30, 0.40, 0.30)
THIRD_MARKET = (0.05, 0.05, 0.90)  # Thailand-operations content: neither Nepal nor India

THEME_BANKS = [
    (PRICE_COMPLAINTS, "price", "negative", NEPAL_LEANING, 1.0),
    (THAILAND_PRICE_COMPLAINTS, "price", "negative", THIRD_MARKET, 0.5),
    (QUALITY_COMPLAINTS, "quality", "negative", NEPAL_LEANING, 0.6),
    (SPICE_COMPLAINTS, "spice", "negative", MIXED, 0.8),
    (PACKAGING_COMPLAINTS, "packaging", "negative", MIXED, 0.6),
    (AVAILABILITY_COMPLAINTS, "availability", "negative", INDIA_LEANING, 0.6),
    (COMPETITOR_COMPARISON, "competitor", "neutral", NEPAL_LEANING, 1.0),
    (TRADEMARK_CHATTER, "legal", "neutral", NEPAL_LEANING, 0.5),
    (TASTE_PRAISE, "taste", "positive", MIXED, 1.6),
    (NOSTALGIA_PRAISE, "nostalgia", "positive", MIXED, 1.6),
    (AVAILABILITY_PRAISE, "availability", "positive", INDIA_LEANING, 1.0),
    (INDIA_EXPANSION_POSITIVE, "availability", "positive", STRONG_INDIA, 1.2),
    (GENERAL_NEUTRAL, "general", "neutral", MIXED, 0.8),
]
THEME_BANK_WEIGHTS = [w for *_rest, w in THEME_BANKS]


def pick_region(rng: random.Random, region_weights: tuple) -> str:
    return rng.choices(["nepal", "india", "unknown"], weights=region_weights, k=1)[0]

# Spam / low-value templates for pipeline/clean.py to filter out. Kept out
# of THEME_BANKS so they never get tagged with a "real" theme/sentiment.
SPAM_TEMPLATES = [
    "\U0001F525\U0001F525\U0001F525 CHECK MY PROFILE FOR FREE GIVEAWAY http://bit.ly/w1nnoodles \U0001F525\U0001F525\U0001F525",
    "Follow back for follow &amp; like <b>plz</b> www.spam-promo.net",
    "\U0001F600\U0001F600\U0001F600\U0001F600\U0001F600\U0001F600",
    "CLICK HERE http://cheap-followers.example.com/free \U0001F447\U0001F447\U0001F447",
    "\U0001F4B0 make money fast dm me now \U0001F4B0 www.getrichquick.example",
]

# Emoji + URL + HTML-entity clutter mixed into otherwise-genuine comments, so
# clean.py has real stripping work to do beyond the dedicated spam rows.
CLUTTER_SUFFIXES = [
    " \U0001F60D\U0001F60D http://instagram.com/p/xyz123",
    " &amp; loving it tbh <3",
    " \U0001F602\U0001F602\U0001F602",
    " check the review here: https://youtu.be/dQw4w9WgXcQ",
    " &lt;3 &lt;3",
    " \U0001F44D\U0001F44D",
]

FIRST_NAMES = [
    "sam", "riya", "kiran", "anish", "priya", "deepak", "sunita", "bibek",
    "asha", "rohan", "mina", "sagar", "kabita", "nirajan", "puja", "arjun",
    "sabina", "manish", "dikshya", "prakash",
]


def daterange_days(start: str, end: str) -> int:
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    return (d1 - d0).days


def random_datetime_in_window(rng: random.Random, start: str, end: str) -> datetime:
    total_days = daterange_days(start, end)
    offset_days = rng.uniform(0, total_days)
    base = datetime.fromisoformat(start)
    dt = base + timedelta(days=offset_days, hours=rng.uniform(0, 24))
    return dt


def clustered_datetime(rng: random.Random, event_date: str, spread_days: int,
                        window_start: str, window_end: str) -> datetime:
    base = datetime.fromisoformat(event_date)
    lo = datetime.fromisoformat(window_start)
    hi = datetime.fromisoformat(window_end)
    # Triangular-ish spread via sum of two uniforms, biased around the event.
    offset = (rng.uniform(-1, 1) + rng.uniform(-1, 1)) / 2 * spread_days
    dt = base + timedelta(days=offset, hours=rng.uniform(0, 24))
    if dt < lo:
        dt = lo + timedelta(hours=rng.uniform(0, 48))
    if dt > hi:
        dt = hi - timedelta(hours=rng.uniform(0, 48))
    return dt


def pick_platform(rng: random.Random) -> str:
    r = rng.random()
    cum = 0.0
    for platform, weight in PLATFORM_WEIGHTS.items():
        cum += weight
        if r <= cum:
            return platform
    return "youtube"


def source_ref_for(rng: random.Random, platform: str, idx: int) -> str:
    if platform == "youtube":
        return f"yt:video_{rng.randint(1000, 9999)}"
    if platform == "twitter":
        return f"tw:tweet_{rng.randint(10 ** 8, 10 ** 9 - 1)}"
    return f"fb:post_{rng.randint(10000, 99999)}"


def engagement_for(rng: random.Random, platform: str) -> int:
    if platform == "youtube":
        return int(round(rng.paretovariate(1.3))) - 1 + rng.randint(0, 15)
    if platform == "twitter":
        return int(round(rng.paretovariate(1.1))) - 1 + rng.randint(0, 10)
    return rng.randint(0, 60)


def author_pool(rng: random.Random, n: int) -> list:
    authors = []
    for i in range(n):
        name = rng.choice(FIRST_NAMES)
        authors.append(f"user_{name}_{rng.randint(100, 9999)}")
    return authors


def maybe_add_clutter(rng: random.Random, text: str, p: float = 0.22) -> str:
    if rng.random() < p:
        return text + rng.choice(CLUTTER_SUFFIXES)
    return text


# Lead-in / trailer phrases used to naturally vary reused template sentences
# so that independently-drawn comments from the same small template bank
# don't all collapse into exact/near duplicates (real commenters phrase
# similar opinions differently). Long enough to meaningfully shift the
# near-duplicate similarity ratio, not just a one-word tag.
#
# Deliberately avoid any word with a nonzero VADER lexicon score here (e.g.
# "honestly"/"honest" score +2.0/+2.3, strong enough to flip a negative
# sentence positive) -- these are meant to be inert padding for dedup, not
# a second source of sentiment signal. Verified against
# vaderSentiment's lexicon before finalizing this list.
VARIATION_PREFIXES = [
    "Just my take but ",
    "As a longtime customer, ",
    "Been thinking about this - ",
    "Random thought, ",
    "From my experience, ",
    "Just saying, ",
    "Update from me: ",
    "Circling back to this - ",
    "One more thing - ",
]
VARIATION_SUFFIXES = [
    " Just something I noticed.",
    " That's just how it's been lately.",
    " Anyone else notice the same thing?",
    " Wondering what others think.",
    " Been noticing this for a while now.",
    " At least that's what I've seen.",
    " Might just be me though.",
]


def vary_template(rng: random.Random, text: str) -> str:
    result = text
    if rng.random() < 0.45:
        prefix = rng.choice(VARIATION_PREFIXES)
        result = prefix + result[0].lower() + result[1:]
    if rng.random() < 0.45:
        suffix = rng.choice(VARIATION_SUFFIXES)
        if result.endswith("."):
            result = result[:-1]
        result = result + suffix
    return result


def build_rows(rng: random.Random) -> list:
    rows = []
    authors = author_pool(rng, 140)

    # --- Event-clustered rows ---------------------------------------------
    # (count, pool, spread_days, gold_sentiment, region_weights) -
    # gold_sentiment is the dominant intended tone of the pool, used only
    # as a synthetic gold-label proxy (see module docstring). region_weights
    # reflects which market each event is actually about: the Nepal price
    # hike/quality fine/trademark case are Nepal-government/Nepal-court
    # events; the India expansion push is explicitly India-side; the
    # Thailand price hike is neither.
    event_allocation = {
        "price_hike_nepal_2024": (26, PRICE_COMPLAINTS + COMPETITOR_COMPARISON[:3], 25, "negative", STRONG_NEPAL),
        "quality_fine_2024": (32, QUALITY_COMPLAINTS, 20, "negative", STRONG_NEPAL),
        "trademark_ruling_2025": (18, TRADEMARK_CHATTER, 18, "neutral", STRONG_NEPAL),
        "thailand_price_hike_2025": (18, THAILAND_PRICE_COMPLAINTS + PRICE_COMPLAINTS[:4], 20, "negative", THIRD_MARKET),
        "competitor_surge_2025": (24, COMPETITOR_COMPARISON, 30, "neutral", NEPAL_LEANING),
        "india_expansion_2026": (28, INDIA_EXPANSION_POSITIVE + AVAILABILITY_PRAISE[:2], 35, "positive", STRONG_INDIA),
    }
    events_by_id = {e["id"]: e for e in KNOWN_EVENTS}

    for event_id, (count, pool, spread, gold_sentiment, region_weights) in event_allocation.items():
        event = events_by_id[event_id]
        for _ in range(count):
            text = rng.choice(pool)
            text = vary_template(rng, text)
            text = maybe_add_clutter(rng, text)
            dt = clustered_datetime(rng, event["date"], spread, WINDOW_START, WINDOW_END)
            platform = pick_platform(rng)
            rows.append({
                "text_raw": text,
                "timestamp": dt,
                "platform": platform,
                "author_id": rng.choice(authors),
                "gold_sentiment": gold_sentiment,
                "region": pick_region(rng, region_weights),
            })

    # --- Steady-state background rows --------------------------------------
    n_background = N_ROWS - len(rows) - len(SPAM_TEMPLATES) - 6  # reserve spam + dup slots
    for _ in range(n_background):
        bank, _theme, gold_sentiment, region_weights, _weight = rng.choices(
            THEME_BANKS, weights=THEME_BANK_WEIGHTS, k=1
        )[0]
        text = rng.choice(bank)
        text = vary_template(rng, text)
        text = maybe_add_clutter(rng, text)
        dt = random_datetime_in_window(rng, WINDOW_START, WINDOW_END)
        platform = pick_platform(rng)
        rows.append({
            "text_raw": text,
            "timestamp": dt,
            "platform": platform,
            "author_id": rng.choice(authors),
            "gold_sentiment": gold_sentiment,
            "region": pick_region(rng, region_weights),
        })

    # --- Spam rows (for clean.py's spam filter) -----------------------------
    spam_author = "user_promo_bot_001"
    for text in SPAM_TEMPLATES:
        dt = random_datetime_in_window(rng, WINDOW_START, WINDOW_END)
        platform = pick_platform(rng)
        rows.append({
            "text_raw": text,
            "timestamp": dt,
            "platform": platform,
            "author_id": spam_author,
            "gold_sentiment": "",
            "region": "unknown",
        })

    # --- Exact-duplicate rows (near-duplicate / copy-pasted spam for dedup) -
    dup_source = rng.choice(TASTE_PRAISE + NOSTALGIA_PRAISE)
    dup_author = "user_repost_bot_007"
    for _ in range(6):
        dt = random_datetime_in_window(rng, WINDOW_START, WINDOW_END)
        platform = pick_platform(rng)
        rows.append({
            "text_raw": dup_source,
            "timestamp": dt,
            "platform": platform,
            "author_id": dup_author,
            "gold_sentiment": "positive",
            "region": pick_region(rng, MIXED),
        })

    rng.shuffle(rows)
    return rows


def main():
    rng = random.Random(RNG_SEED)
    rows = build_rows(rng)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "id", "platform", "text_raw", "timestamp", "source_ref",
            "engagement", "language_guess", "author_id", "gold_sentiment", "region",
        ])
        for i, row in enumerate(rows):
            platform = row["platform"]
            record_id = f"{platform}_{i:05d}"
            writer.writerow([
                record_id,
                platform,
                row["text_raw"],
                row["timestamp"].strftime("%Y-%m-%dT%H:%M:%S"),
                source_ref_for(rng, platform, i),
                engagement_for(rng, platform),
                "",  # language_guess populated during cleaning
                row["author_id"],
                row["gold_sentiment"],
                row["region"],
            ])

    print(f"Wrote {len(rows)} synthetic rows to {OUT_PATH}")
    platform_counts = {}
    for row in rows:
        platform_counts[row["platform"]] = platform_counts.get(row["platform"], 0) + 1
    print("Platform breakdown:", platform_counts)


if __name__ == "__main__":
    main()
