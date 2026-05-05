"""Reserved adapter slugs.

Each stub registers a slug so users typing `--adapter pfsense` get a
"not yet implemented" message instead of "unknown adapter, did you mean
'opnsense'?" — the slug is a *known future*, not a typo.

When a vendor moves from stub to implementation, replace the body in a
sibling module and update the registry call. The CLI surface doesn't change.
"""

from __future__ import annotations

from .base import AdapterResult, RouterAdapter, register
from ..report import Capability


def _make_stub(slug: str, product: str, api_kind: str) -> type[RouterAdapter]:
    class _Stub(RouterAdapter):
        slug_attr = slug  # avoid confusing with class attr at metaclass time
        description = f"{product} adapter — slug reserved, not yet implemented."

        @classmethod
        def matches(cls, report) -> bool:  # type: ignore[no-untyped-def]
            # Empty api_kind on the stub means "we have no automatic
            # signature for this vendor yet" — never auto-match in that
            # case (otherwise stubs steal autopick from `unmanaged`).
            return bool(api_kind) and report.router.api_kind == api_kind

        def capabilities(self) -> list[Capability]:
            return [
                Capability(
                    name="documentation_only",
                    available=False,
                    reason=f"{product} adapter is not yet implemented in this build",
                )
            ]

        def execute(self) -> AdapterResult:
            raise NotImplementedError(
                f"router adapter '{self.slug}' is reserved but not implemented; "
                f"use --adapter unmanaged for now"
            )

    _Stub.__name__ = f"{slug.title().replace('-', '')}AdapterStub"
    _Stub.slug = slug
    return _Stub


_STUBS = (
    ("pfsense",   "pfSense",            "pfsense"),
    ("mikrotik",  "MikroTik RouterOS",  "mikrotik-rest"),
    ("unifi",     "Ubiquiti UniFi",     "unifi"),
    ("edgeos",    "Ubiquiti EdgeOS",    "edgeos"),
    ("openwrt",   "OpenWrt",            ""),
    ("cisco",     "Cisco IOS",          ""),
    ("fortigate", "Fortinet FortiGate", ""),
)

for _slug, _product, _api in _STUBS:
    register(_slug, _make_stub(_slug, _product, _api))
