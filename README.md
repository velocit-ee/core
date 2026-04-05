# velocitee core

The open-source engine stack powering [velocit.ee](https://velocit.ee).

Four independent, modular engines — each useful on its own, each feeding the next. Takes hardware from bare metal to a fully documented, running network stack.

```
status: VME — phase 1, step 0, in active development
        VNE / VSE / VLE — planned
license: AGPL v3
```

---

## engines

| engine | phase | status | description |
|--------|-------|--------|-------------|
| [VME](vme/) | 1 | in development | bare metal provisioning — PXE boot + unattended OS install |
| VNE | 2 | planned | network config — OPNsense, VLANs, DHCP, firewall via Terraform + Ansible |
| VSE | 3 | planned | services — containerized stack deployment, idempotent Ansible roles |
| VLE | 4 | planned | lifecycle — monitoring, auto-docs, drift detection, single-command repair |

Each engine reads a config bundle and writes a **handoff manifest** on success. The next engine picks up the manifest where the last one left off. Enter the pipeline wherever your hardware already is.

---

## config sources

All engines accept config from:

- local raw file
- git repository
- velocit.ee authenticated registry *(SaaS tier — AI-assisted config generation)*

---

## license

The core engines are open source under the [GNU Affero General Public License v3.0](LICENSE).

- Self-hosted use: free, always
- Network service deployment: modifications must be released under AGPL v3
- Proprietary embedding: [commercial license available](docs/COMMERCIAL_LICENSE.md)

All contributors must agree to the [Contributor License Agreement](CLA.md).

The velocit.ee AI configuration generator (SaaS layer) is proprietary and not included in this repository.
