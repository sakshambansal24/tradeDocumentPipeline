import { useEffect } from "react";
import { API_BASE_URL } from "../api/client";
import type { ShipmentEvent } from "../api/types";

export function useShipmentEvents(onEvent: (event: ShipmentEvent) => void) {
  useEffect(() => {
    const eventSource = new EventSource(`${API_BASE_URL}/shipments/events`);

    const handleMessage = (message: MessageEvent<string>) => {
      try {
        onEvent(JSON.parse(message.data) as ShipmentEvent);
      } catch {
        // Ignore malformed events; the next valid event will refresh the UI.
      }
    };

    const eventTypes = [
      "shipment_started",
      "doc_processed",
      "cross_validation_done",
      "shipment_completed",
      "shipment_failed",
      "draft_confirmed",
      "shipment_deleted"
    ];
    eventTypes.forEach((eventType) => {
      eventSource.addEventListener(eventType, handleMessage);
    });

    return () => {
      eventTypes.forEach((eventType) => {
        eventSource.removeEventListener(eventType, handleMessage);
      });
      eventSource.close();
    };
  }, [onEvent]);
}
