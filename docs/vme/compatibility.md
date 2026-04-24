# VME Platform and Hardware Compatibility

## Seed machine (runs VME)

| Platform | Status | Notes |
|----------|--------|-------|
| Linux x86-64 | Supported | Primary target. Docker Engine or Docker Desktop. |
| Linux ARM64 | Supported | Raspberry Pi 4/5, other ARM64 SBCs. Docker Engine. |
| macOS Intel | Supported | Docker Desktop required. `host` networking via VirtioNet. |
| macOS Apple Silicon | Supported | Docker Desktop with Rosetta. Same caveats as Intel. |
| Windows (WSL2) | Supported | Docker Desktop + WSL2 backend. Network mode may require bridged adapter. |

### Docker version requirements

- Docker Engine 24.0+ or Docker Desktop 4.20+
- Docker Compose v2 (bundled with above — `docker compose`, not `docker-compose`)

### macOS / Windows network mode caveat

dnsmasq uses `network_mode: host` to receive DHCP broadcast traffic.
On macOS and Windows, Docker Desktop runs a Linux VM — `host` mode binds to
the VM's network, not the host's physical NIC directly. To reach physical
hardware on the provisioning LAN, you must either:

1. Create a macvlan network in Docker Desktop and attach dnsmasq to it, **or**
2. Run VME from a Linux machine or VM with a real NIC on the provisioning LAN.

This is a Docker Desktop limitation, not a VME limitation.

---

## Target hardware

| Requirement | Minimum |
|-------------|---------|
| Architecture | x86-64 |
| PXE boot | Required on the provisioning NIC |
| RAM | 4 GB (Proxmox VE minimum) |
| Storage | 64 GB on the install target disk |
| Firmware | BIOS or UEFI (both supported via DHCP option 93 detection) |

### Tested hardware

Hardware is listed here as it is validated in real deployments.

| Hardware | Firmware | OS | Notes |
|----------|----------|----|-------|
| Generic x86-64 VM (KVM/QEMU) | BIOS + UEFI | Proxmox VE, Ubuntu | Development and CI testing target |

Add entries here as physical hardware is validated.

---

## OS image sources

VME always resolves the latest stable release at runtime from official sources:

| OS | Source | Version selection |
|----|--------|-------------------|
| Proxmox VE | `http://download.proxmox.com/iso/` | Highest `major.minor-patch` in directory listing |
| Ubuntu Server | Launchpad API → `releases.ubuntu.com` | Latest supported LTS series |

No version numbers are hardcoded. When Proxmox 9 or Ubuntu 26.04 LTS ship,
VME picks them up automatically.
