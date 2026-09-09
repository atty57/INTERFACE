# STUB — design seam only. Not implemented: UIA / AX / AT-SPI perception. See REPORT §7.
"""A desktop surface, present to prove the seam is real rather than asserted.

Windows UIA, macOS AX and AT-SPI all expose role, name, and containment, so they would
produce the same ``ElementDigest`` shape. Adding desktop means implementing these three
methods — not touching the artifact schema, the replay engine, the policy gate, or the
escalation model. ``test_the_desktop_stub_satisfies_the_surface_interface`` in ``tests/test_digest.py``
asserts this class satisfies ``Surface``, so the claim is checked rather than written down.
"""

from .base import Action, Effect, ElementDigest, Located, LocatorDescriptor


class DesktopSurface:
    def __init__(self, application: str) -> None:
        self.application = application

    def snapshot(self) -> ElementDigest:
        raise NotImplementedError("DesktopSurface.snapshot: UIA/AX/AT-SPI walk not built")

    def locate(self, desc: LocatorDescriptor) -> Located:
        raise NotImplementedError("DesktopSurface.locate: UIA/AX/AT-SPI walk not built")

    def act(self, action: Action) -> Effect:
        raise NotImplementedError("DesktopSurface.act: UIA/AX/AT-SPI walk not built")
