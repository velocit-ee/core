"""OPNsense first-boot config.xml generator.

OPNsense is a stock OPNsense ISO. The first boot needs an existing config.xml
on the boot media to skip the interactive installer/setup wizard, assign WAN
and LAN, set the root password, and crucially enable the REST API so the
velocitee-native and Ansible renderers can drive the rest of the configuration
over HTTPS.

This module is deliberately *only* the first-boot baseline — VLANs, DHCP, DNS,
and firewall rules are all applied later through the API. Putting them in
config.xml works but couples the renderer to a less stable surface and is
harder to make idempotent.

The generated XML has been verified against an OPNsense 24.x reference config
captured from a fresh install. If the OPNsense version targeted in
`velocitee.yml` diverges materially we'll need version-specific templates.
"""

from __future__ import annotations

import re
import secrets
from xml.sax.saxutils import escape

# Python 3.13 removed the stdlib `crypt` module. Prefer passlib if installed,
# otherwise fall back to the deprecated stdlib import for older runtimes.
try:
    from passlib.hash import sha512_crypt as _sha512_crypt  # type: ignore[import-not-found]
    _USE_PASSLIB = True
except ImportError:
    _USE_PASSLIB = False
    try:
        import crypt as _crypt  # type: ignore[import-not-found]
    except ImportError:
        _crypt = None  # type: ignore[assignment]

from shared.schema import VNEIntent

# Use SHA-512 ($6$) — supported by all modern OPNsense releases.
_SALT_ALPHABET = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

# OPNsense versions we have validated this template against. We don't refuse
# to render for others — config.xml format is largely stable across 24.x — but
# we warn so debugging is faster when something does diverge.
_VALIDATED_VERSIONS = {"24.7"}


def _make_salt(length: int = 16) -> str:
    return "".join(secrets.choice(_SALT_ALPHABET) for _ in range(length))


def hash_password(plaintext: str) -> str:
    """Produce a $6$ (SHA-512) crypt hash of plaintext, suitable for OPNsense."""
    if not plaintext:
        raise ValueError("password must be non-empty")
    if _USE_PASSLIB:
        return _sha512_crypt.using(rounds=5000).hash(plaintext)
    if _crypt is None:
        raise RuntimeError(
            "neither passlib nor stdlib crypt is available — install passlib "
            "(`pip install passlib`) so VNE can hash the OPNsense root password"
        )
    salt = f"$6${_make_salt()}"
    return _crypt.crypt(plaintext, salt)


def _safe_apikey() -> str:
    """OPNsense API key — base64-ish but their UI accepts any URL-safe string."""
    return secrets.token_urlsafe(48)


def _safe_apisecret() -> str:
    return secrets.token_urlsafe(64)


def render_config_xml(
    intent: VNEIntent,
    *,
    root_password: str,
    api_key: str | None = None,
    api_secret: str | None = None,
) -> str:
    """Return a fully rendered OPNsense config.xml as a string.

    Caller passes the plaintext root password; we hash it before writing.
    api_key/api_secret are generated if not supplied — caller stores them in the
    velocitee-native state file so subsequent runs reuse the same credentials.
    """
    if intent.opnsense.version not in _VALIDATED_VERSIONS:
        # Soft warn via a comment in the file. We don't refuse — the user
        # explicitly chose this version and config.xml is largely stable.
        version_note = (
            f"<!-- velocitee: target OPNsense version {intent.opnsense.version} "
            f"is outside the validated set {sorted(_VALIDATED_VERSIONS)}; "
            f"verify the resulting first boot manually. -->"
        )
    else:
        version_note = ""

    pw_hash = hash_password(root_password)
    api_key = api_key or _safe_apikey()
    api_secret = api_secret or _safe_apisecret()

    wan_if = escape(intent.network.wan_interface)
    lan_if = escape(intent.network.lan_interface)

    # We do not assign a static LAN IP here — DHCP on first boot picks up an
    # address from the Proxmox bridge, then the renderer reads it from the
    # qemu-guest-agent and reconfigures over the API. Keeping config.xml
    # minimal reduces version coupling.
    return _XML_TEMPLATE.format(
        version_note=version_note,
        pw_hash=escape(pw_hash),
        api_key=escape(api_key),
        api_secret_hash=escape(hash_password(api_secret)),
        wan_if=wan_if,
        lan_if=lan_if,
        hostname=escape(f"opnsense-{intent.opnsense.vm.vmid}"),
        domain=escape(intent.network.dns.domain),
    )


def extract_api_credentials(xml: str) -> tuple[str | None, str | None]:
    """Return (api_key, api_secret_hash) from a previously rendered config.xml.

    Useful when resuming a deployment — we want to reuse the same credentials,
    not rotate them on every run.
    """
    key = re.search(r"<apikey>([^<]+)</apikey>", xml)
    sec = re.search(r"<apisecret>([^<]+)</apisecret>", xml)
    return (key.group(1) if key else None,
            sec.group(1) if sec else None)


# ---------------------------------------------------------------------------
# Template — minimal OPNsense 24.x config.xml.
# ---------------------------------------------------------------------------
# Notes on what's in here:
#   - One admin user (root) with a SHA-512 hashed password.
#   - One API token bound to root, used by the renderer for all subsequent
#     configuration. The secret is stored hashed (OPNsense convention).
#   - WAN + LAN interfaces named after the user-supplied vmbr-attached vNICs.
#     LAN starts on DHCP; WAN starts on DHCP. Static addressing happens later.
#   - sshd enabled on LAN with key-auth only (root login allowed because that's
#     the only configured user; we narrow this in a later API call).
#   - api.enabled = 1 — the whole point. Without this we have no way back in.
#
# Anything you add here you also have to be willing to re-do over the API
# during resume — config.xml only runs on the very first boot.

_XML_TEMPLATE = """<?xml version="1.0"?>
<opnsense>
  {version_note}
  <theme>opnsense</theme>
  <hostname>{hostname}</hostname>
  <domain>{domain}</domain>

  <system>
    <optimization>normal</optimization>
    <hostname>{hostname}</hostname>
    <domain>{domain}</domain>
    <timezone>Etc/UTC</timezone>
    <language>en_US</language>
    <dnsallowoverride>1</dnsallowoverride>
    <user>
      <name>root</name>
      <descr>System Administrator</descr>
      <scope>system</scope>
      <groupname>admins</groupname>
      <password>{pw_hash}</password>
      <uid>0</uid>
      <expires/>
      <authorizedkeys/>
      <ipsecpsk/>
      <apikeys>
        <item>
          <key>{api_key}</key>
          <secret>{api_secret_hash}</secret>
        </item>
      </apikeys>
    </user>
    <group>
      <name>admins</name>
      <description>System Administrators</description>
      <scope>system</scope>
      <gid>1999</gid>
      <member>0</member>
      <priv>page-all</priv>
    </group>
    <nextuid>2000</nextuid>
    <nextgid>2000</nextgid>
    <ssh>
      <enabled>enabled</enabled>
      <permitrootlogin>1</permitrootlogin>
      <passwordauth>0</passwordauth>
      <interfaces>lan</interfaces>
    </ssh>
    <webgui>
      <protocol>https</protocol>
      <ssl-certref>auto</ssl-certref>
    </webgui>
  </system>

  <interfaces>
    <wan>
      <enable>1</enable>
      <if>{wan_if}</if>
      <ipaddr>dhcp</ipaddr>
      <ipaddrv6>dhcp6</ipaddrv6>
      <descr>WAN</descr>
      <blockpriv>1</blockpriv>
      <blockbogons>1</blockbogons>
    </wan>
    <lan>
      <enable>1</enable>
      <if>{lan_if}</if>
      <ipaddr>dhcp</ipaddr>
      <descr>LAN</descr>
    </lan>
  </interfaces>

  <dhcpd>
    <lan>
      <enable>0</enable>
    </lan>
  </dhcpd>

  <unbound>
    <enable>1</enable>
  </unbound>

  <filter>
    <rule>
      <type>pass</type>
      <interface>lan</interface>
      <ipprotocol>inet</ipprotocol>
      <descr>Default allow LAN to any (replaced by VNE on first config pass)</descr>
      <source><network>lan</network></source>
      <destination><any/></destination>
    </rule>
  </filter>

  <api>
    <enabled>1</enabled>
  </api>
</opnsense>
"""
