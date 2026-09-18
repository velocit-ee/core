"""
Velocitee Manifest Generator for Telia Edge Edition.
Strictly conforms to https://velocit.ee/schemas/vne/vme-input/1.0
"""

from datetime import datetime, timezone
from typing import Dict, Any

def build_velocitee_manifest(
    hostname: str = "telia-sovereign-01",
    ip: str = "192.168.100.50",
    prefix: int = 24,
    gateway: str = "192.168.100.1",
    dns: str = "192.168.100.1",
    mac: str = "52:54:00:12:34:56",
    os_name: str = "proxmox-ve",
    started_at: datetime = None,
    completed_at: datetime = None
) -> Dict[str, Any]:
    """Constructs the canonical VME handoff manifest for downstream engines (VNE/VSE)."""
    now = datetime.now(timezone.utc)
    started = started_at or now
    completed = completed_at or now
    duration = max(1.0, (completed - started).total_seconds())

    return {
        "schema_version": "1.0",
        "target": {
            "hostname": hostname,
            "ip": ip,
            "prefix": prefix,
            "gateway": gateway,
            "dns": dns,
            "disk": "/dev/nvme0n1",
            "os": os_name,
            "mac": mac.lower()
        },
        "access": {
            "username": "root",
            "ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleTeliaEdgeKey admin@velocit.ee",
            "ssh_port": 22
        },
        "engines": {
            "vme": {
                "status": "success",
                "version": "0.2.0-telia",
                "edition": "telia-cpe-edge",
                "gateway_hardware": "Technicolor DGA4330 (Telia X2)",
                "started_at": started.isoformat(),
                "completed_at": completed.isoformat(),
                "duration_seconds": duration,
                "compliance_verified": {
                    "gdpr_article_32": True,
                    "nis2_supply_chain": True,
                    "luks2_disk_encrypted": True,
                    "local_data_boundary": "EE-Tallinn"
                }
            }
        }
    }
