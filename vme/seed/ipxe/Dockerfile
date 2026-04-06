# Builds iPXE from source.
# Produces: undionly.kpxe (legacy BIOS) and ipxe.efi (UEFI x86-64 / ARM64).
#
# Multi-arch: set TARGETARCH at build time.
#   docker build --build-arg TARGETARCH=amd64 .
#   docker build --build-arg TARGETARCH=arm64 .

FROM debian:bookworm-slim AS builder

ARG TARGETARCH=amd64

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    liblzma-dev \
    libz-dev \
    mtools \
    syslinux \
    ca-certificates \
    wget \
    gcc-x86-64-linux-gnu \
    binutils-x86-64-linux-gnu \
    && rm -rf /var/lib/apt/lists/*

# iPXE source — pinned to a well-known stable tag; update as needed.
# We use HTTPS and validate with ca-certificates.
ARG IPXE_VERSION=v1.21.1
WORKDIR /build
RUN git clone --depth 1 --branch ${IPXE_VERSION} https://github.com/ipxe/ipxe.git .

# Embed the boot script so iPXE fetches it from our nginx on DHCP-provided IP.
COPY boot.ipxe /build/src/boot.ipxe

WORKDIR /build/src

# Build for x86-64 (always — needed for undionly.kpxe and EFI).
# Cross-compile if the build host is ARM64.
RUN if [ "${TARGETARCH}" = "arm64" ]; then \
        CROSS_COMPILE=x86_64-linux-gnu- make bin/undionly.kpxe EMBED=boot.ipxe -j$(nproc); \
        CROSS_COMPILE=x86_64-linux-gnu- make bin-x86_64-efi/ipxe.efi EMBED=boot.ipxe -j$(nproc); \
    else \
        make bin/undionly.kpxe EMBED=boot.ipxe -j$(nproc); \
        make bin-x86_64-efi/ipxe.efi EMBED=boot.ipxe -j$(nproc); \
    fi

# Collect outputs.
RUN mkdir -p /out && \
    cp bin/undionly.kpxe /out/undionly.kpxe && \
    cp bin-x86_64-efi/ipxe.efi /out/ipxe.efi

# ---------------------------------------------------------------------------
# Final stage — just the built files.
# docker-compose copies them to the nginx tftp/ipxe volume.
# ---------------------------------------------------------------------------
FROM scratch AS artifacts
COPY --from=builder /out/ /
