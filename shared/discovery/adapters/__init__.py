"""Router adapters — vendor-specific glue used by VNE Path B (join).

A RouterAdapter takes the network's RouterInfo and returns:

  - capabilities the joined network exposes (managed firewall? VLAN API?
    DHCP scope CRUD?)
  - a JoinResult — the manifest fragment VNE writes for VSE/VLE

Adding support for a new router/firewall = subclass `RouterAdapter`,
register the slug.

Two adapters ship by default:

  - `unmanaged`  — works with *any* gateway; no API integration. Always
                   selectable. Used when no specific adapter recognises the
                   gateway *and* as the explicit user choice when the
                   operator doesn't want to grant API credentials.
  - `opnsense`   — uses the existing OPNsense API client to enrich the join
                   manifest (real VLAN list, DHCP scopes, firewall summary).

Stubs reserve the slug names for vendors we want to support next; they
return Capabilities with available=False and a clear reason. Calling
.execute() on a stub raises NotImplementedError — VNE never gets that far
because the capabilities gate stops the join with a friendly error.
"""

from __future__ import annotations

from .base import AdapterResult, RouterAdapter, lookup, register
from .opnsense import OPNsenseAdapter
from .unmanaged import UnmanagedAdapter

# Stubs — register slugs so `vne join --adapter X` errors with a helpful
# 'not yet implemented' message rather than 'unknown adapter'.
from . import _stubs as _  # noqa: F401  (side-effect: registers stubs)

__all__ = [
    "AdapterResult",
    "OPNsenseAdapter",
    "RouterAdapter",
    "UnmanagedAdapter",
    "lookup",
    "register",
]
