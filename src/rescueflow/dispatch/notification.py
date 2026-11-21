"""Mock outbound dispatch notification (docs/CONTRACTS.md -- no real paging,
SMS or MDT integration in the MVP; see also NavigationHandoff).

Created only for a **committed** change, after operator approval, never for
a candidate proposal (docs/CONTRACTS.md 9.4 step 7).
"""

from __future__ import annotations

import uuid

from rescueflow.models.core import DispatchMessage, WorldState


class MockDispatchNotificationAdapter:
    """Concrete `DispatchNotificationAdapter`. Logs a message; does not call
    any real messaging provider."""

    def __init__(self, origin: str = "rescueflow") -> None:
        self.origin = origin
        self.log: list[DispatchMessage] = []

    def notify(
        self, world: WorldState, vehicle_id: str, task_id: str, destination_label: str, plan_version: int
    ) -> DispatchMessage:
        message = DispatchMessage(
            message_id=f"msg-{uuid.uuid4().hex[:10]}",
            unit_id=vehicle_id,
            task_id=task_id,
            destination_label=destination_label,
            origin=self.origin,  # type: ignore[arg-type]
            plan_version=plan_version,
            sent_at_sim_s=world.sim_time_s,
            delivery_status="mock_sent",
        )
        self.log.append(message)
        world.dispatch_messages.append(message)
        return message
