from app.supply.provider import search_activities, search_flights, search_hotels


def test_search_flights_scales_price_with_adults():
    solo = search_flights("JFK", "Lisbon", "2026-09-01", "2026-09-07", adults=1)
    pair = search_flights("JFK", "Lisbon", "2026-09-01", "2026-09-07", adults=2)
    assert len(solo) == len(pair) == 2
    assert pair[0]["price_usd"] == solo[0]["price_usd"] * 2
    assert pair[0]["origin"] == "JFK"
    assert pair[0]["destination"] == "Lisbon"


def test_search_flights_unknown_destination_returns_empty():
    assert search_flights("JFK", "Nowhereville", "2026-09-01", "2026-09-07") == []


def test_search_hotels_computes_total_and_cancellation_deadline():
    hotels = search_hotels("Lisbon", "2026-09-01", "2026-09-08")
    boutique = next(h for h in hotels if h["id"] == "lis-h-baixa-boutique")
    assert boutique["price_usd"] == boutique["nightly_rate_usd"] * 7
    # fixture: 3 days before check-in
    assert boutique["cancellation_deadline"] == "2026-08-29"


def test_search_hotels_sorts_by_vibe_tag_match():
    hotels = search_hotels("Lisbon", "2026-09-01", "2026-09-08", vibe_tags=["boutique", "food-forward"])
    assert hotels[0]["id"] == "lis-h-baixa-boutique"


def test_search_activities_sorts_by_vibe_tag_match():
    activities = search_activities("Barcelona", vibe_tags=["food-forward"])
    assert activities[0]["id"] in {"bcn-a-tapas-crawl", "bcn-a-flamenco-show"}
