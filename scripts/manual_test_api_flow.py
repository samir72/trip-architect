"""Manual end-to-end smoke test for the FastAPI service layer, against live
Azure OpenAI agents (not stubbed -- that's tests/test_api_flow.py's job).

Not part of the app or CI. Run: python scripts/manual_test_api_flow.py
"""

from fastapi.testclient import TestClient

from app.main import app


def main() -> None:
    client = TestClient(app)

    session_id = client.post("/sessions").json()["session_id"]
    print(f"session: {session_id}")

    turns = [
        "I want a relaxing week in Portugal in September with my partner, nothing too touristy.",
        "Budget is around $4000 total, flying out of JFK.",
        "September 5th to 12th. We love boutique hotels, good food, walkable neighborhoods.",
    ]
    for turn in turns:
        resp = client.post(f"/sessions/{session_id}/messages", json={"message": turn}).json()
        print(f"you: {turn}")
        print(f"agent: {resp['assistant_reply']}")
        print(f"complete: {resp['constraints_complete']}\n")

    plan = client.post(f"/sessions/{session_id}/compose").json()
    plan_id = plan["id"]
    itinerary_id = plan["candidate_order"][0]
    itinerary = plan["itineraries"][itinerary_id]
    print(f"plan {plan_id}: {len(plan['candidate_order'])} candidate(s)")
    print(f"  picked: {itinerary['title']} -- ${itinerary['total_cost_usd']} -- hotel: {itinerary['hotel']['name']}\n")

    plan = client.post(f"/plans/{plan_id}/itineraries/{itinerary_id}/approve").json()
    print(f"approved -- status: {plan['itineraries'][itinerary_id]['status']}\n")

    swap_result = client.post(
        f"/plans/{plan_id}/itineraries/{itinerary_id}/swap",
        json={
            "component_type": "hotel",
            "component_id": plan["itineraries"][itinerary_id]["hotel"]["id"],
            "feedback": "Something quieter and cheaper, even if less central.",
        },
    ).json()
    print(f"swapped hotel -> {swap_result['itinerary']['hotel']['name']}")
    print(f"  status after swap (should be demoted to proposed): {swap_result['itinerary']['status']}")
    print(f"  diff: {swap_result['diff']}")
    print(f"  warnings: {swap_result['warnings']}\n")

    plan = client.get(f"/plans/{plan_id}").json()
    print(f"before undo -- hotel: {plan['itineraries'][itinerary_id]['hotel']['name']}")
    plan = client.post(f"/plans/{plan_id}/undo").json()
    print(f"after undo  -- hotel: {plan['itineraries'][itinerary_id]['hotel']['name']}\n")

    plan = client.post(f"/plans/{plan_id}/itineraries/{itinerary_id}/approve").json()
    booking = client.post(f"/plans/{plan_id}/book", json={"itinerary_id": itinerary_id}).json()
    print(f"booked: {booking['booking_id']} -- status: {booking['itinerary']['status']}")

    locked = client.post(f"/plans/{plan_id}/itineraries/{itinerary_id}/approve")
    print(f"post-book approve attempt -- expected 409: got {locked.status_code}")


if __name__ == "__main__":
    main()
