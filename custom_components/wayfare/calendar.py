"""Calendar platform for wayfare."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import Reservation, Trip, WayfareError
from .coordinator import WayfareConfigEntry, WayfareCoordinator
from .entity import WayfareEntity

TRAVEL_KINDS = frozenset({"flight", "train", "bus", "boat"})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WayfareConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the wayfare calendars."""
    async_add_entities(
        [
            TripCalendar(entry.runtime_data, entry),
            BookingCalendar(entry.runtime_data, entry),
        ]
    )


class TripCalendar(WayfareEntity, CalendarEntity):
    """Every trip, as an event that lasts the days it covers."""

    def __init__(
        self, coordinator: WayfareCoordinator, entry: WayfareConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "trips")

    def _event(self, trip: Trip) -> CalendarEvent:
        return CalendarEvent(
            summary=trip.title,
            start=trip.start,
            # The end of an all-day event is the day after its last.
            end=trip.end + timedelta(days=1),
            location=", ".join(trip.destinations) or None,
            description=self._trip_url(trip),
            uid=trip.id,
        )

    @property
    def event(self) -> CalendarEvent | None:
        """Return the trip under way, or else the next one."""
        today = dt_util.now().date()
        data = self.coordinator.data
        trip = data.current_trip(today) or data.next_trip(today)
        return None if trip is None else self._event(trip)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return the trips that overlap a range of time."""
        events = [self._event(trip) for trip in self.coordinator.data.trips]
        return [
            event
            for event in events
            if event.start_datetime_local < end_date
            and event.end_datetime_local > start_date
        ]


def _moment(local: date | datetime, instant: datetime | None) -> date | datetime:
    if instant is not None:
        return dt_util.as_local(instant)
    if isinstance(local, datetime):
        # A booking without an offset names no moment of its own. Take it as
        # Home Assistant's time, as a calendar does with a floating time.
        return local.replace(tzinfo=dt_util.get_default_time_zone())
    return local


def _day(moment: date | datetime) -> date:
    return moment.date() if isinstance(moment, datetime) else moment


def _location(reservation: Reservation) -> str | None:
    if reservation.kind in TRAVEL_KINDS:
        ends = [
            place.label
            for place in (reservation.origin, reservation.destination)
            if place is not None
        ]
        return " to ".join(ends) or None
    place = reservation.origin
    if place is None:
        return None
    return place.address or place.name or place.locality


def _description(reservation: Reservation) -> str | None:
    lines = []
    if reservation.kind in TRAVEL_KINDS and reservation.provider is not None:
        lines.append(reservation.provider)
    if reservation.reservation_number is not None:
        lines.append(f"Ref {reservation.reservation_number}")
    return "\n".join(lines) or None


def reservation_event(reservation: Reservation) -> CalendarEvent:
    """Describe a reservation as a calendar event, as wayfare's iCal feed does."""
    start = _moment(reservation.start, reservation.start_at)
    end = (
        start
        if reservation.end is None
        else _moment(reservation.end, reservation.end_at)
    )
    if isinstance(start, datetime) and isinstance(end, datetime):
        # Floating times at either end of a journey across time zones can
        # seem to run backwards.
        end = max(start, end)
    else:
        # A day rather than a time at either end, as for a stay with a time to
        # check in but only a day to check out, makes an all-day event. Its
        # end is the day after its last.
        start, end = _day(start), _day(end) + timedelta(days=1)
    return CalendarEvent(
        summary=reservation.summary,
        start=start,
        end=end,
        location=_location(reservation),
        description=_description(reservation),
        uid=f"{reservation.source}@{reservation.start.isoformat()}",
    )


class BookingCalendar(WayfareEntity, CalendarEntity):
    """Every flight, train, stay and other booking that was not cancelled."""

    def __init__(
        self, coordinator: WayfareCoordinator, entry: WayfareConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "reservations")

    @property
    def event(self) -> CalendarEvent | None:
        """Return the booking under way, or else the next one."""
        now = dt_util.now()
        events = [
            reservation_event(reservation)
            for reservations in self.coordinator.data.reservations.values()
            for reservation in reservations
        ]
        return min(
            (event for event in events if event.end_datetime_local > now),
            key=lambda event: event.start_datetime_local,
            default=None,
        )

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return the bookings that overlap a range of time.

        These are asked of wayfare, as the last poll only has those for the
        trips that are not over.
        """
        # wayfare goes by the date where a booking happens, which can be a day
        # either side of the date here.
        try:
            reservations = await self.coordinator.client.reservations(
                start=dt_util.as_local(start_date).date() - timedelta(days=1),
                end=dt_util.as_local(end_date).date() + timedelta(days=1),
            )
        except WayfareError as err:
            raise HomeAssistantError(
                f"Unable to fetch bookings from wayfare: {err}"
            ) from err
        events = [
            reservation_event(reservation)
            for reservation in reservations
            if not reservation.cancelled
        ]
        return [
            event
            for event in events
            if event.start_datetime_local < end_date
            and event.end_datetime_local > start_date
        ]
