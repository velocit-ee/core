"""velocitee.yml parsing → VNEIntent, and OPNsense config.xml generation."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from vne import config as vne_config
from vne import config_xml

_GOOD_YML = """
velocitee:
  provisioner: "velocitee-native"
vne:
  wan_interface: "ens18"
  lan_interface: "ens19"
  opnsense_version: "24.7"
  opnsense_iso_url: "https://mirror.example.org/OPNsense-24.7-dvd-amd64.iso"
  opnsense_iso_checksum: "sha256:{h}"
  vlans:
    - {{ id: 10, name: mgmt, cidr: 10.10.10.0/24, dhcp_start: 10.10.10.100, dhcp_end: 10.10.10.200 }}
  dns:
    upstream: ["1.1.1.1"]
    domain: "lab.local"
  opnsense_vm:
    vmid: 100
    storage_pool: "local-lvm"
""".format(h="ab" * 32)


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "velocitee.yml"
    p.write_text(text)
    return p


def test_load_and_translate(tmp_path):
    cfg = vne_config.load_file(_write(tmp_path, _GOOD_YML))
    intent = vne_config.to_intent(cfg, proxmox_host="192.168.1.10")
    assert intent.proxmox_host == "192.168.1.10"
    assert [v.id for v in intent.network.vlans] == [10]
    # VNE convention: first usable host in the CIDR is the gateway.
    assert intent.network.vlans[0].gateway == "10.10.10.1"


def test_bad_checksum_prefix_rejected(tmp_path):
    bad = _GOOD_YML.replace("sha256:" + "ab" * 32, "md5:whatever")
    with pytest.raises(vne_config.ConfigError) as exc:
        vne_config.load_file(_write(tmp_path, bad))
    assert "sha256" in str(exc.value) or "sha512" in str(exc.value)


def test_missing_vne_block_is_clear_error(tmp_path):
    with pytest.raises(vne_config.ConfigError) as exc:
        vne_config.load_file(_write(tmp_path, "velocitee:\n  provisioner: x\n"))
    assert "vne" in str(exc.value)


def test_config_xml_is_well_formed(intent):
    xml = config_xml.render_config_xml(
        intent, root_password="s3cret!", api_key="KEYKEY", api_secret="SECSEC",
    )
    ET.fromstring(xml)  # raises on malformed XML
    assert "<api>" in xml and "<enabled>1</enabled>" in xml


def test_config_xml_credentials_round_trip(intent):
    """extract_api_credentials must parse the very template render_config_xml
    produces (regression guard for the <apikey> vs <key> tag mismatch)."""
    xml = config_xml.render_config_xml(
        intent, root_password="pw", api_key="MYKEY123", api_secret="MYSECRET456",
    )
    key, secret_hash = config_xml.extract_api_credentials(xml)
    assert key == "MYKEY123"
    # The secret is stored hashed in the XML, so we get the hash back, not the plaintext.
    assert secret_hash and secret_hash != "MYSECRET456"


def test_config_xml_no_plaintext_password(intent):
    xml = config_xml.render_config_xml(intent, root_password="sup3rsecret")
    assert "sup3rsecret" not in xml  # only the crypt hash should appear
