# Getting Started with VME

VME installs an operating system onto a server for you — automatically, over your network. You plug in the machine, run one command, and walk away. VME handles everything else.

This guide walks you through installation and first-time setup. No prior Linux or networking experience required.

---

## What you need

**The seed machine** — the computer that runs VME. This is usually a laptop, Raspberry Pi, or spare computer you have on hand. It needs:

- Ubuntu 22.04 or newer (or Debian 12+)
- A network cable connection to the same switch as your target machine
- About 15 GB of free disk space (for the OS image VME downloads)
- An internet connection (to download Docker, VME, and the OS image)

**The target machine** — the server you want VME to provision. It needs:

- At least 4 GB RAM and 64 GB storage (Proxmox VE minimum; Ubuntu Server can run on less)
- A network card (most computers from the past 15 years work)
- Set to boot from the network — see [Setting the target to network boot](#setting-the-target-to-network-boot) below

**A network switch** connecting both machines. A simple unmanaged switch is fine.

---

## Step 1 — Install VME

On your seed machine, open a terminal and run:

```bash
curl -fsSL https://raw.githubusercontent.com/velocit-ee/core/main/vme/install.sh | bash
```

The installer will:

1. Install Docker (the container platform VME uses to run its services)
2. Install Python 3.11 or newer
3. Download VME from GitHub
4. Create the `vme` command so you can use it anywhere

The whole process takes a few minutes. You may be asked for your password once — this is normal, it needs administrator access to install software.

When it finishes you should see:

```
  VME installed successfully.

  To get started:

    cd ~/vme/vme
    vme setup
```

> **If `vme` is not found after install:** Close and reopen your terminal, then try again.

---

## Step 2 — Run the setup wizard

The setup wizard asks you a handful of questions and writes your configuration file. You do not need to edit any files by hand.

Navigate to the VME directory and run it:

```bash
cd ~/vme/vme
vme setup
```

The wizard has four steps.

---

### Wizard step 1 of 4 — Provisioning network

VME needs to know which network port on your seed machine is connected to the switch that your target machine is plugged into.

The wizard lists all detected network interfaces and picks a sensible default. An interface labelled **connected** has a live cable. One labelled **no IP** has no address assigned yet — which is what you want for the provisioning port, since VME will manage addresses on it.

```
  Detected network interfaces:

    [1]  eth0         192.168.1.50      (connected)
    [2]  eth1         no IP             (connected)

  Which interface is connected to your provisioning switch? [2]:
```

Press Enter to accept the suggestion, or type the number of the correct interface.

**Next**, the wizard asks for the IP address your seed machine should use on that interface:

```
  eth1 has no IP address.
  VME needs an IP on this interface so target machines can reach the seed stack.

  Seed machine IP on this interface [192.168.100.1]:
```

The default (`192.168.100.1`) works for most setups. Press Enter to accept it.

**Then** it asks for the range of addresses to hand out to target machines during provisioning:

```
  First IP to hand out to target machines [192.168.100.100]:
  Last IP in that range [192.168.100.200]:
```

The defaults are fine. Press Enter twice.

> **What is this?** Your target machine needs a temporary IP address to download the OS installer. VME's built-in DHCP server hands this out automatically. The range you set here is a pool of temporary addresses — you can leave the defaults unless something else on your network already uses `192.168.100.x`.

---

### Wizard step 2 of 4 — Target machine

This is where you describe the machine you want to provision.

```
  Which OS do you want to install?
    [1]  Proxmox VE
    [2]  Ubuntu Server

  Which OS do you want to install? [1]:
```

Choose `1` for Proxmox VE (a hypervisor for running virtual machines) or `2` for Ubuntu Server (a general-purpose Linux server).

Then the wizard asks for the permanent settings the installed OS will use:

```
  Hostname for this machine [node-01]:
```
The hostname is the machine's name on your network. You can leave the default or type something like `server-01`.

```
  Fixed IP address for the installed OS [192.168.100.10]:
```
This is the permanent IP address the machine will have after provisioning is complete. It should be outside the temporary DHCP range you set in step 1 (which was `.100`–`.200`), so `.10` is safe.

```
  Gateway for the installed OS [192.168.100.1]:
```
Your network's router address. The default matches the seed machine IP from step 1 — this is correct if your seed machine is acting as a gateway, or if your switch has a separate router at that address.

```
  DNS server [8.8.8.8]:
```
Where the installed machine looks up domain names. `8.8.8.8` is Google's public DNS and works everywhere. Press Enter.

```
  Install disk on the target machine [/dev/sda]:
```
The disk VME will install the OS onto. The default (`/dev/sda`) is correct for most machines. If your target has an NVMe drive, it may be `/dev/nvme0n1` — check your hardware specs if unsure.

> **Warning:** VME erases this disk completely during install. Make sure there is no data on it you want to keep.

---

### Wizard step 3 of 4 — SSH access

SSH is how you will log into the machine after provisioning. The wizard looks for an existing key in your home directory:

```
  Found: ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... you@machine

  Use this key? [Y/n]:
```

Press Enter to use the found key. If no key was found:

```
  No SSH key found in ~/.ssh/
  Generate one with: ssh-keygen -t ed25519

  Paste your SSH public key:
```

Generate one first, then come back:

```bash
ssh-keygen -t ed25519
```

Press Enter three times to accept all defaults. Then run `cat ~/.ssh/id_ed25519.pub` and paste the output into the wizard prompt.

---

### Wizard step 4 of 4 — Saving config

The wizard writes your answers to `vme-config.yml` and prints a summary:

```
  Config written to vme-config.yml

  ══════════════════════════════════════════════════════
  Setup complete.

  Next steps:
    1. Power off the target machine.
    2. Set it to network-boot (PXE) in its BIOS/UEFI settings.
    3. Run:  vme deploy
  ══════════════════════════════════════════════════════
```

---

## Step 3 — Set the target to network boot

The target machine needs to be told to load its operating system from the network instead of a local disk. This setting lives in the machine's BIOS or UEFI firmware.

**How to get there:** Power on the target machine and immediately press the key shown on screen — usually `F2`, `F10`, `F12`, `Del`, or `Esc`. The exact key depends on your hardware brand.

Common brands:
| Brand | Key |
|-------|-----|
| Dell | F12 (boot menu) or F2 (BIOS) |
| HP | F9 (boot menu) or F10 (BIOS) |
| Lenovo | F12 (boot menu) or F1 (BIOS) |
| ASRock / ASUS / MSI / Gigabyte | F11 or F8 (boot menu) or Del (BIOS) |
| Supermicro | F11 (boot menu) |

Once in the boot menu or BIOS:

1. Look for **Boot Order** or **Boot Priority**
2. Move **Network** or **PXE** to the top of the list (above the hard drive)
3. Save and exit — usually **F10** → **Yes**

The machine will power off or restart. Leave it off for now.

---

## Step 4 — Deploy

Back on your seed machine, run:

```bash
vme deploy
```

VME will:
1. Run a quick set of checks to make sure everything is ready
2. Download the OS image if it hasn't been cached yet (this can take a few minutes on a slow connection — Proxmox VE is about 1.2 GB, Ubuntu Server is about 2 GB)
3. Start the provisioning services

When you see:

```
Seed stack is running.
Power on the target machine now. It will PXE boot and install automatically.
Press Ctrl+C here when provisioning is complete to stop the seed stack.
```

Power on your target machine. It will:
1. Request a network address from VME
2. Load the VME boot menu over the network
3. Begin installing the OS automatically — no keyboard or mouse interaction needed on the target

The install typically takes 10–20 minutes depending on hardware speed. You can watch progress in the log output on your seed machine.

When the target machine reboots on its own and the installer is no longer running, provisioning is complete. Press **Ctrl+C** on the seed machine to stop VME.

---

## After provisioning

Log into your newly provisioned machine:

```bash
ssh root@192.168.100.10
```

(Replace `192.168.100.10` with whatever IP you set in step 2 of the wizard.)

VME also writes a manifest file describing the machine it just provisioned:

```
manifests/output/node-01.yml
```

This file is used by VNE (the next engine in the velocit.ee stack) to configure networking on the machine.

---

## Troubleshooting

**The target machine shows "No DHCP or proxyDHCP offers were received"**
The target sent a PXE boot request but VME's DHCP server didn't respond. Check that:
- `vme deploy` is still running in the terminal — if it exited, restart it before powering on the target
- Both machines are on the same network switch or virtual network
- If you have a firewall (UFW) active, the preflight check will flag this. Fix it with:
  ```bash
  sudo ufw allow in on <your-interface> to any port 67 proto udp
  ```

**The pre-flight check fails with "Port 69 (TFTP) is already in use"**
Another TFTP server (`tftpd-hpa` or `in.tftpd`) is running on the seed machine. Stop it:
```bash
sudo systemctl stop tftpd-hpa
```

**The pre-flight check fails with "ufw is active but port 67 or port 80 may be blocked"**
The setup wizard normally opens these ports automatically. If you skipped that step or added a new interface, run the commands shown in the preflight output, then retry `vme deploy`. VME needs port 67/udp (DHCP) so target machines get an IP, port 69/udp (TFTP) for the initial iPXE binary, and port 80/tcp (HTTP) so iPXE can fetch the boot menu and OS image.

**The target machine shows a "No boot device" or "PXE-E53" error**
The target is not reaching VME. Check that:
- Both machines are connected to the same switch
- VME is running (`vme deploy` is active in another terminal)
- The interface you chose in the wizard is the one with the cable to the switch (`ip addr` will show which interfaces have a link)

**The pre-flight check fails with "Interface not found"**
The interface name in your config doesn't match what Linux sees. Run `ip link` to list interface names and update `vme-config.yml`, or re-run `vme setup`.

**The pre-flight check fails with "conflicting DHCP server detected"**
Another device on the provisioning network is already handing out IP addresses (common if the switch also connects to your main router). Connect the seed and target directly through a dedicated switch, or use a separate unmanaged switch isolated from your main network.

**The install starts but the machine reboots into the installer again (boot loop)**
The target's boot order still has Network first. After provisioning, go back into the BIOS and move the hard drive above Network in the boot order — or disable network boot entirely.

**`vme` command not found after install**
Close and reopen your terminal, then try again.
