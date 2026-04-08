# Getting Started with VME

VME installs an operating system onto a server for you — automatically, over the network. You plug in the machine, run one command, and walk away.

---

## What you need

**The seed machine** — the computer that runs VME. Usually a laptop, Raspberry Pi, or spare desktop. It needs:

- Ubuntu 22.04 or newer (Debian 12+ also works)
- A network cable connected to the same switch as your target machine
- About 15 GB of free disk space for the OS image
- An internet connection for the initial download

**The target machine** — the server you're provisioning. It needs:

- At least 4 GB RAM and 64 GB storage (Proxmox VE minimum; Ubuntu Server works with less)
- A wired network card
- The ability to boot from the network — covered in step 3 below

**A network switch** connecting both machines. A basic unmanaged switch is fine.

---

## Step 1 — Install VME

On your seed machine, open a terminal and run:

```bash
curl -fsSL https://raw.githubusercontent.com/velocit-ee/core/main/vme/install.sh | bash
```

This installs Docker, Python, downloads VME, and creates the `vme` command. It takes a few minutes and will ask for your password once.

When it finishes:

```
  VME installed successfully.

  To get started:

    cd ~/vme/vme
    vme setup
```

> **`vme` not found after install?** Close and reopen your terminal, then try again.

---

## Step 2 — Run the setup wizard

The wizard asks you a few questions and writes your config file. No YAML editing required.

```bash
cd ~/vme/vme
vme setup
```

The wizard has five steps.

---

### Step 1 of 5 — Provisioning network

VME needs to know which network port is connected to the switch your target machine is on.

```
  Detected network interfaces:

    [1]  eth0         192.168.1.50      (connected)
    [2]  eth1         no IP             (connected)

  Which interface is connected to your provisioning switch? [2]:
```

Press Enter to accept the suggestion, or type the number of the right interface. The one labelled **no IP** and **connected** is usually the right choice — it has a cable but no address yet, which is exactly what you want.

Next it asks for the seed machine's IP on that interface:

```
  Seed machine IP on this interface [192.168.100.1]:
```

The default works for most setups. Press Enter.

Then the temporary address range for target machines during provisioning:

```
  First IP to hand out to target machines [192.168.100.100]:
  Last IP in that range [192.168.100.200]:
```

Press Enter twice to accept the defaults.

**Firewall:** If you have UFW active, the wizard will detect it and offer to open the required ports automatically. Say yes.

---

### Step 2 of 5 — Target machine

```
  Which OS do you want to install?
    [1]  Proxmox VE
    [2]  Ubuntu Server
```

Choose `1` for Proxmox VE (runs virtual machines) or `2` for Ubuntu Server (general purpose Linux).

Then fill in the permanent settings for the installed machine:

| Field | What it means |
|-------|--------------|
| Hostname | The machine's name on your network (e.g. `node-01`) |
| Fixed IP | Its permanent IP after provisioning — should be outside the `.100`–`.200` range, so `.10` is safe |
| Gateway | Your router's address |
| DNS server | `8.8.8.8` works everywhere |
| Install disk | The disk to wipe and install onto — `/dev/sda` for most hardware, `/dev/nvme0n1` for NVMe drives |
| Timezone | Used by the installed OS (e.g. `Europe/Berlin`) |

> **Warning:** VME wipes the install disk completely. Make sure there's nothing on it you want to keep.

---

### Step 3 of 5 — User account

The installer needs a password for the main user account even if you're using SSH keys.

```
  Password:
  Confirm:
```

The password is hashed immediately — the plaintext is never stored anywhere.

---

### Step 4 of 5 — SSH access

SSH is how you'll connect after provisioning. The wizard looks for an existing key:

```
  Found: ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... you@machine

  Use this key? [Y/n]:
```

Press Enter to use the found key. If you don't have one yet:

```bash
ssh-keygen -t ed25519
```

Press Enter three times for defaults. Then run `cat ~/.ssh/id_ed25519.pub` and paste the output into the wizard.

---

### Step 5 of 5 — Saving config

The wizard writes `vme-config.yml` and prints next steps:

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

The target needs to load from the network before its hard drive. This is a one-time BIOS change.

Power on the target and immediately press the key your hardware uses to enter the boot menu or BIOS:

| Brand | Key |
|-------|-----|
| Dell | F12 (boot menu) or F2 (BIOS) |
| HP | F9 (boot menu) or F10 (BIOS) |
| Lenovo | F12 (boot menu) or F1 (BIOS) |
| ASRock / ASUS / MSI / Gigabyte | F11 or F8 (boot menu) or Del (BIOS) |
| Supermicro | F11 (boot menu) |

Once in the boot settings:

1. Find **Boot Order** or **Boot Priority**
2. Move **Network** or **PXE** to the top (above the hard drive)
3. Save and exit — usually F10 → Yes

Power the machine off and leave it for now.

---

## Step 4 — Deploy

```bash
vme deploy
```

VME runs a quick pre-flight check, downloads the OS image if it's not already cached, then starts the provisioning services. Once it's ready:

```
  Seed stack is running.
  Power on the target machine now.
  Watching for key events. Full logs → ~/.velocitee/logs/deploy-node-01-...log
  Press Ctrl+C to stop manually.
```

Power on your target machine. VME shows what's happening as it goes:

```
  [10:32:45]  Target discovered       MAC 00:0c:29:0a:66:5e
  [10:32:45]  DHCP lease granted      192.168.100.168
  [10:32:46]  iPXE binary delivered   via TFTP
  [10:32:47]  Boot menu loaded        via HTTP
  [10:32:47]  OS image streaming      ubuntu-24.04.4-live-server-amd64.iso
```

The install runs automatically — no interaction needed on the target machine. It typically takes 10–20 minutes. When it finishes, the target powers itself off and VME shows a summary:

```
  ════════════════════════════════════════════════════════════
  Provisioning complete.

  Hostname:   node-01
  IP:         192.168.100.10
  SSH user:   ubuntu
  Duration:   12m 34s
  ════════════════════════════════════════════════════════════
```

---

## Step 5 — Fix the boot order and connect

The target powered off after install. Before powering it back on, go back into its BIOS and move the **Hard Disk** above **Network** in the boot order — otherwise it will try to PXE boot again and loop.

Once you've done that, power it on and connect:

```bash
ssh ubuntu@192.168.100.10
```

(Use `root` instead of `ubuntu` if you installed Proxmox VE.)

---

## What next?

After provisioning completes, VME will ask:

```
  What next?
    [1]  Continue to VNE — configure networking
    [2]  Done
```

- **VNE** is the next engine in the velocit.ee stack. It configures the machine's networking. If it's installed, VME hands off to it automatically.
- **Done** closes VME and leaves a manifest file at `manifests/output/node-01-<timestamp>.json` with everything that was provisioned. You can pass this to any engine later.

---

## Other useful commands

```bash
# Download OS images ahead of time
vme images pull ubuntu-server
vme images pull proxmox-ve

# See what's cached
vme images list

# Delete a specific cached image (frees up disk space)
vme images clean proxmox-ve

# Run pre-flight checks without deploying
vme preflight

# See full compose logs during deployment (for troubleshooting)
vme deploy --verbose

# Wipe everything and start fresh
vme reset

# Wipe everything including the downloaded OS images
vme reset --images
```

---

## Troubleshooting

**The target shows "No DHCP or proxyDHCP offers were received"**
VME's DHCP server didn't respond. Check:
- `vme deploy` is still running
- Both machines are on the same switch
- The firewall allows port 67/udp on the provisioning interface. The setup wizard handles this, but if you skipped it: `sudo ufw allow in on <interface> to any port 67 proto udp`

**Pre-flight fails with "Port 69 (TFTP) is already in use"**
Another TFTP server is running. Stop it: `sudo systemctl stop tftpd-hpa`

**Pre-flight fails with "ufw is active but port 67 or 80 may be blocked"**
The setup wizard normally opens these automatically. Run the commands shown in the preflight output. VME needs 67/udp (DHCP), 69/udp (TFTP), and 80/tcp (HTTP).

**Target shows "No boot device" or "PXE-E53"**
Not reaching VME — check both machines are on the same switch and `vme deploy` is running.

**Pre-flight fails with "Interface not found"**
The interface name in your config doesn't match what Linux sees. Run `ip link` and update `vme-config.yml`, or re-run `vme setup`.

**Pre-flight fails with "conflicting DHCP server detected"**
Something else on the network is handing out IPs (often a router on the same switch). Use a dedicated unmanaged switch isolated from your main network.

**Boot loop after install**
The target is still booting from the network. Go into BIOS and move Hard Disk above Network in the boot order.
