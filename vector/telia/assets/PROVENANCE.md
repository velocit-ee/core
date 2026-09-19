# Boot artefact provenance

The files listed in `SHA256SUMS` are **release assets, not source**. They are no
longer tracked in git (C-01): fetch them with `scripts/fetch-assets.sh`, which
refuses any file whose checksum does not match this manifest.

| Path | What it is (inspected 2026-09-18) | Source / build | Version |
|---|---|---|---|
| `boot/initrd` | gzip cpio initramfs, 98 MB uncompressed; Alpine Linux netboot payload (`apk-tools`, BusyBox) | **unknown — fill in** | **unknown** |
| `boot/vmlinuz` | PE32+ EFI application, AArch64 Linux kernel | **unknown — fill in** | **unknown** |
| `tftp/ipxe-arm64.efi`, `tftp/bootaa64.efi` | identical iPXE EFI builds for AArch64 | **unknown — fill in** (should be `vme/seed/ipxe/Dockerfile`) | **unknown** |
| `tftp/ipxe-x86_64.efi` | iPXE EFI build for x86-64 | **unknown — fill in** | **unknown** |
| `tftp/undionly.kpxe` | iPXE legacy-BIOS chainloader | **unknown — fill in** | **unknown** |

Rows marked **unknown** must be completed by whoever produced the binaries
before they are published as a release: source ISO or git tag, build command,
and the signing key used. Until then, the checksums pin *what* is served, not
*where it came from* (V-09, V-15).

Publishing: upload the six files as assets of a GitHub release tagged
`vector-assets-<date>` on `velocit-ee/core`, sign `SHA256SUMS` (minisign or
cosign), and point `VECTOR_ASSET_BASE_URL` at that release.
