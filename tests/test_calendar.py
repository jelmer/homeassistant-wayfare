"""Tests for the wayfare calendar."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.wayfare.api import Reservation
from custom_components.wayfare.calendar import reservation_event

from . import (
    BASE_URL,
    CANCELLED_HOTEL_IN_OSAKA,
    FLIGHT_TO_TOKYO,
    JAPAN,
    LISBON,
    PARIS,
    mock_api,
)

ENTITY_ID = "calendar.wayfare_trips"

LISBON_EVENT = {
    "start": "2026-05-14",
    "end": "2026-05-18",
    "summary": "Trip to Lisbon",
    "description": f"{BASE_URL}/trip/2026-05-lisbon",
    "location": "Lisbon",
}
JAPAN_EVENT = {
    "start": "2026-09-28",
    "end": "2026-10-10",
    "summary": "Autumn in Japan",
    "description": f"{BASE_URL}/trip/2026-09-japan",
    "location": "Japan",
}
ROTTERDAM_EVENT = {
    "start": "2026-11-02",
    "end": "2026-11-03",
    "summary": "Trip to Rotterdam",
    "description": f"{BASE_URL}/trip/2026-11-rotterdam",
    "location": "Rotterdam",
}
BERLIN_EVENT = {
    "start": "2027-01-10",
    "end": "2027-01-15",
    "summary": "Trip to Berlin and Prague",
    "description": f"{BASE_URL}/trip/2027-01-berlin",
    "location": "Berlin, Prague",
}


def _state(hass: HomeAssistant) -> tuple[str, dict[str, Any]]:
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    attributes = dict(state.attributes)
    del attributes["friendly_name"]
    return state.state, attributes


async def _events(hass: HomeAssistant, start: str, end: str) -> Any:
    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {"entity_id": ENTITY_ID, "start_date_time": start, "end_date_time": end},
        blocking=True,
        return_response=True,
    )
    assert response is not None
    calendar: Any = response[ENTITY_ID]
    return calendar["events"]


async def test_the_calendar_is_on_during_a_trip(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    assert _state(hass) == (
        STATE_ON,
        {
            "message": "Autumn in Japan",
            "all_day": True,
            "start_time": "2026-09-28 00:00:00",
            "end_time": "2026-10-10 00:00:00",
            "location": "Japan",
            "description": f"{BASE_URL}/trip/2026-09-japan",
        },
    )


async def test_between_trips_the_calendar_shows_the_next_one(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    tick: Callable[[], Awaitable[None]],
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to("2026-10-10 12:00:00+00:00")
    await tick()

    assert _state(hass) == (
        STATE_OFF,
        {
            "message": "Trip to Rotterdam",
            "all_day": True,
            "start_time": "2026-11-02 00:00:00",
            "end_time": "2026-11-03 00:00:00",
            "location": "Rotterdam",
            "description": f"{BASE_URL}/trip/2026-11-rotterdam",
        },
    )


async def test_with_nothing_booked_the_calendar_shows_no_event(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    tick: Callable[[], Awaitable[None]],
    config_entry: MockConfigEntry,
) -> None:
    mock_api(aioclient_mock, trips=[LISBON])

    await tick()

    assert _state(hass) == (STATE_OFF, {})


@pytest.mark.parametrize(
    ("start", "end", "events"),
    [
        (
            "2026-01-01 00:00:00",
            "2028-01-01 00:00:00",
            [LISBON_EVENT, JAPAN_EVENT, ROTTERDAM_EVENT, BERLIN_EVENT],
        ),
        ("2026-10-01 00:00:00", "2026-12-01 00:00:00", [JAPAN_EVENT, ROTTERDAM_EVENT]),
        # A trip takes up the whole of its last day, and none of the next.
        ("2026-10-09 23:00:00", "2026-10-09 23:30:00", [JAPAN_EVENT]),
        ("2026-10-10 00:00:00", "2026-11-02 00:00:00", []),
        ("2026-11-02 00:00:00", "2026-11-02 01:00:00", [ROTTERDAM_EVENT]),
    ],
)
async def test_events_are_the_trips_that_overlap_the_range(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    start: str,
    end: str,
    events: list[dict[str, Any]],
) -> None:
    assert await _events(hass, start, end) == events


async def test_a_trip_without_destinations_has_no_location(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    tick: Callable[[], Awaitable[None]],
    config_entry: MockConfigEntry,
) -> None:
    empty = {
        "id": "2027-03-somewhere",
        "start": "2027-03-01",
        "end": "2027-03-03",
        "reservations": [],
    }
    mock_api(aioclient_mock, trips=[empty])

    await tick()

    assert await _events(hass, "2027-03-01 00:00:00", "2027-03-02 00:00:00") == [
        {
            "start": "2027-03-01",
            "end": "2027-03-04",
            "summary": "2027-03-somewhere",
            "description": f"{BASE_URL}/trip/2027-03-somewhere",
        }
    ]


async def test_a_cancelled_trip_is_no_event(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    tick: Callable[[], Awaitable[None]],
    config_entry: MockConfigEntry,
) -> None:
    mock_api(aioclient_mock, trips=[JAPAN, PARIS])

    await tick()

    assert _state(hass)[1]["message"] == "Autumn in Japan"
    assert await _events(hass, "2026-10-01 00:00:00", "2026-12-01 00:00:00") == [
        JAPAN_EVENT
    ]


BOOKINGS_ID = "calendar.wayfare_bookings"

FLIGHT_EVENT = {
    # 18:00 UTC to 06:50 UTC, in the US/Pacific time zone the tests run in.
    "start": "2026-09-28T11:00:00-07:00",
    "end": "2026-09-28T23:50:00-07:00",
    "summary": "NH212: LHR to HND",
    "description": "ANA\nRef J7K2LM",
    "location": "LHR to HND",
}
HOTEL_EVENT = {
    "start": "2026-09-30",
    "end": "2026-10-09",
    "summary": "Stay at Hotel Granvia Kyoto",
    "description": "Ref 88341",
    "location": "901 Higashi-Shiokoji-cho, Kyoto",
}
TRAIN_EVENT = {
    # Without an offset, so taken to be in Home Assistant's time zone.
    "start": "2026-11-02T08:16:00-08:00",
    "end": "2026-11-02T12:32:00-08:00",
    "summary": "9114: London St Pancras Int'l to Rotterdam Centraal",
    "description": "Eurostar\nRef M2GRPC",
    "location": "London St Pancras Int'l to Rotterdam Centraal",
}


async def _booking_events(hass: HomeAssistant, start: str, end: str) -> Any:
    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {"entity_id": BOOKINGS_ID, "start_date_time": start, "end_date_time": end},
        blocking=True,
        return_response=True,
    )
    assert response is not None
    calendar: Any = response[BOOKINGS_ID]
    return calendar["events"]


async def test_the_bookings_calendar_shows_the_booking_under_way(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    state = hass.states.get(BOOKINGS_ID)
    assert state is not None
    attributes = dict(state.attributes)
    del attributes["friendly_name"]
    assert (state.state, attributes) == (
        STATE_ON,
        {
            "message": "Stay at Hotel Granvia Kyoto",
            "all_day": True,
            "start_time": "2026-09-30 00:00:00",
            "end_time": "2026-10-09 00:00:00",
            "location": "901 Higashi-Shiokoji-cho, Kyoto",
            "description": "Ref 88341",
        },
    )


async def test_the_bookings_calendar_shows_the_next_booking(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    tick: Callable[[], Awaitable[None]],
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to("2026-10-10 12:00:00+00:00")
    await tick()

    state = hass.states.get(BOOKINGS_ID)
    assert state is not None
    assert (state.state, state.attributes["message"]) == (
        STATE_OFF,
        "9114: London St Pancras Int'l to Rotterdam Centraal",
    )


@pytest.mark.parametrize(
    ("start", "end", "events"),
    [
        (
            "2026-09-01 00:00:00",
            "2026-12-01 00:00:00",
            [FLIGHT_EVENT, HOTEL_EVENT, TRAIN_EVENT],
        ),
        ("2026-09-28 23:00:00", "2026-09-29 00:00:00", [FLIGHT_EVENT]),
        ("2026-10-09 00:00:00", "2026-11-02 08:00:00", []),
    ],
)
async def test_booking_events_are_those_that_overlap_the_range(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    start: str,
    end: str,
    events: list[dict[str, Any]],
) -> None:
    assert await _booking_events(hass, start, end) == events


async def test_bookings_are_asked_of_wayfare_with_a_day_to_spare(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    aioclient_mock.mock_calls.clear()

    await _booking_events(hass, "2026-09-01 00:00:00", "2026-12-01 00:00:00")

    [(method, url, _data, _headers)] = aioclient_mock.mock_calls
    assert (method, str(url)) == (
        "GET",
        f"{BASE_URL}/api/v1/reservations?from=2026-08-31&to=2026-12-02",
    )


async def test_a_cancelled_booking_is_no_event(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    mock_api(aioclient_mock, reservations=[FLIGHT_TO_TOKYO, CANCELLED_HOTEL_IN_OSAKA])

    assert await _booking_events(
        hass, "2026-09-01 00:00:00", "2026-12-01 00:00:00"
    ) == [FLIGHT_EVENT]


async def test_bookings_cannot_be_listed_while_wayfare_is_down(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE_URL}/api/v1/reservations", status=500)

    with pytest.raises(HomeAssistantError, match="Unable to fetch bookings"):
        await _booking_events(hass, "2026-09-01 00:00:00", "2026-12-01 00:00:00")


def _reservation(**fields: Any) -> Reservation:
    return Reservation(
        **{
            "source": "2026/booking.json",
            "kind": "train",
            "trip": "2026-11-rotterdam",
            "summary": "A train",
            "provider": None,
            "reservation_number": None,
            "start": datetime(2026, 11, 2, 8, 16),
            "end": None,
            "start_at": None,
            "end_at": None,
            "origin": None,
            "destination": None,
            "cancelled": False,
            **fields,
        }
    )


@pytest.mark.parametrize(
    ("fields", "start", "end"),
    [
        (
            {"start_at": datetime(2026, 11, 2, 8, 16, tzinfo=UTC)},
            datetime(2026, 11, 2, 8, 16, tzinfo=UTC),
            datetime(2026, 11, 2, 8, 16, tzinfo=UTC),
        ),
        ({"start": date(2026, 11, 2)}, date(2026, 11, 2), date(2026, 11, 3)),
        (
            {
                "kind": "lodging",
                "start": datetime(2026, 11, 2, 15, 0),
                "end": date(2026, 11, 4),
            },
            date(2026, 11, 2),
            date(2026, 11, 5),
        ),
        # Tokyo to San Francisco, as the clocks at either end tell it.
        (
            {"end": datetime(2026, 11, 2, 1, 30)},
            datetime(2026, 11, 2, 8, 16, tzinfo=UTC),
            datetime(2026, 11, 2, 8, 16, tzinfo=UTC),
        ),
    ],
)
async def test_reservation_event_times(
    hass: HomeAssistant,
    fields: dict[str, Any],
    start: date | datetime,
    end: date | datetime,
) -> None:
    await hass.config.async_set_time_zone("UTC")

    event = reservation_event(_reservation(**fields))

    assert (event.start, event.end) == (start, end)
