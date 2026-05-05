"""Tests for the optional nmap enrichment layer.

We never invoke the real `nmap` binary in tests — these exercise the XML
parser and the merge logic only. The end-to-end behaviour (subprocess +
binary on PATH) is left to manual verification because CI environments
don't reliably ship nmap and we don't want to mask binary-presence bugs
behind a mock.
"""

from __future__ import annotations

from shared.discovery import Host, Service
from shared.discovery import nmap_probe


CANNED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<nmaprun scanner="nmap" args="nmap -oX -" version="7.94">
  <host>
    <address addr="192.168.1.1" addrtype="ipv4"/>
    <address addr="aa:bb:cc:dd:ee:ff" addrtype="mac" vendor="Foo Vendor"/>
    <ports>
      <port protocol="tcp" portid="22">
        <state state="open" reason="syn-ack"/>
        <service name="ssh" product="OpenSSH" version="9.3p1" extrainfo="protocol 2.0">
          <cpe>cpe:/a:openbsd:openssh:9.3p1</cpe>
        </service>
      </port>
      <port protocol="tcp" portid="443">
        <state state="open" reason="syn-ack"/>
        <service name="https" product="OPNsense" version="24.7" tunnel="ssl"/>
      </port>
      <port protocol="tcp" portid="9999">
        <state state="closed"/>
        <service name="-"/>
      </port>
    </ports>
    <os>
      <osmatch name="Linux 5.15" accuracy="98">
        <osclass type="general purpose" vendor="Linux" osfamily="Linux" osgen="5.X" accuracy="98"/>
      </osmatch>
      <osmatch name="Linux 5.10" accuracy="92">
        <osclass type="general purpose" vendor="Linux" osfamily="Linux" osgen="5.X" accuracy="92"/>
      </osmatch>
    </os>
  </host>
</nmaprun>
"""


def test_parse_xml_returns_open_tcp_only() -> None:
    parsed = nmap_probe.parse_nmap_xml(CANNED_XML)
    assert "192.168.1.1" in parsed
    services = parsed["192.168.1.1"]["services"]
    ports = sorted(s["port"] for s in services)
    assert ports == [22, 443]  # closed port 9999 excluded


def test_parse_xml_extracts_service_metadata() -> None:
    parsed = nmap_probe.parse_nmap_xml(CANNED_XML)
    ssh = next(s for s in parsed["192.168.1.1"]["services"] if s["port"] == 22)
    assert ssh["product"] == "OpenSSH"
    assert ssh["version"] == "9.3p1"
    assert ssh["extrainfo"] == "protocol 2.0"
    assert ssh["cpe"] == ["cpe:/a:openbsd:openssh:9.3p1"]


def test_parse_xml_extracts_os_guesses() -> None:
    parsed = nmap_probe.parse_nmap_xml(CANNED_XML)
    os_records = parsed["192.168.1.1"]["os"]
    assert os_records[0]["name"] == "Linux 5.15"
    assert os_records[0]["accuracy"] == 98
    assert os_records[0]["family"] == "Linux"


def test_parse_empty_xml_returns_empty_dict() -> None:
    assert nmap_probe.parse_nmap_xml("") == {}


def test_merge_fills_nmap_fields_on_existing_service() -> None:
    parsed = nmap_probe.parse_nmap_xml(CANNED_XML)
    host = Host(
        ip="192.168.1.1",
        services=[
            Service(port=22, name="ssh"),  # no banner yet — merge should fill product/version
            Service(port=443, name="https", product="Existing", version="0"),  # do not overwrite
        ],
    )
    nmap_probe._merge(parsed, [host])

    ssh = next(s for s in host.services if s.port == 22)
    assert ssh.nmap_product == "OpenSSH"
    assert ssh.nmap_version == "9.3p1"
    # product was empty, so promoted from nmap
    assert ssh.product == "OpenSSH"
    assert ssh.version == "9.3p1"

    https = next(s for s in host.services if s.port == 443)
    assert https.nmap_product == "OPNsense"
    # existing product/version preserved — both fields visible
    assert https.product == "Existing"
    assert https.version == "0"

    assert host.os_guesses
    assert host.os_guesses[0].name == "Linux 5.15"
    assert host.os_guesses[0].accuracy == 98
    # capped at 3
    assert len(host.os_guesses) <= 3


def test_merge_creates_service_for_unknown_port() -> None:
    """If nmap reports a port the stdlib pass missed, surface it."""
    parsed = nmap_probe.parse_nmap_xml(CANNED_XML)
    host = Host(ip="192.168.1.1", services=[])  # nothing pre-discovered
    nmap_probe._merge(parsed, [host])
    ports = sorted(s.port for s in host.services)
    assert ports == [22, 443]


def test_merge_ignores_unrelated_hosts() -> None:
    parsed = nmap_probe.parse_nmap_xml(CANNED_XML)
    other = Host(ip="10.0.0.5", services=[])
    nmap_probe._merge(parsed, [other])
    assert other.services == []
    assert other.os_guesses == []
