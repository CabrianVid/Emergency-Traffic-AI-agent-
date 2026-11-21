"""`NavigationHandoff` describes a *production* integration only -- a crew's
existing Google Maps workflow continues unchanged; this class is never used
to route fictional SUMO vehicles (docs/DECISIONS.md). Google's documented
`maps/dir` deep link needs no API key, so this is informational text, not a
live network call.
"""

from __future__ import annotations


class MockNavigationHandoff:
    """Concrete `NavigationHandoff`. Builds the URL shape only; never called
    over the network and never includes patient-identifying data."""

    def build_url(self, destination_label: str) -> str:
        return f"https://www.google.com/maps/dir/?api=1&destination={destination_label}"
