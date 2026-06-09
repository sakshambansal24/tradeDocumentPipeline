import json
from datetime import UTC, datetime
from queue import Empty, Queue
from threading import Lock
from typing import Any

from pydantic import BaseModel, ConfigDict


class ShipmentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    shipment_id: str | None = None
    email_id: str | None = None
    customer_id: str | None = None
    status: str | None = None
    payload: dict[str, Any]
    emitted_at: datetime


class ShipmentEventBus:
    def __init__(self) -> None:
        self._subscribers: set[Queue[ShipmentEvent]] = set()
        self._lock = Lock()

    def publish(
        self,
        event_type: str,
        *,
        shipment_id: str | None = None,
        email_id: str | None = None,
        customer_id: str | None = None,
        status: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        event = ShipmentEvent(
            event_type=event_type,
            shipment_id=shipment_id,
            email_id=email_id,
            customer_id=customer_id,
            status=status,
            payload=payload or {},
            emitted_at=datetime.now(UTC),
        )
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            subscriber.put(event)

    def subscribe(self) -> Queue[ShipmentEvent]:
        subscriber: Queue[ShipmentEvent] = Queue()
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Queue[ShipmentEvent]) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)


def shipment_event_stream(event_bus: ShipmentEventBus):
    subscriber = event_bus.subscribe()
    try:
        yield ": connected\n\n"
        while True:
            try:
                event = subscriber.get(timeout=15)
            except Empty:
                yield ": keepalive\n\n"
                continue
            payload = json.dumps(event.model_dump(mode="json"))
            yield f"event: {event.event_type}\ndata: {payload}\n\n"
    finally:
        event_bus.unsubscribe(subscriber)
