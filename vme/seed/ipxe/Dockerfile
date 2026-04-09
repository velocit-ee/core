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
    gcc-multilib \
    liblzma-dev \
    libz-dev \
    mtools \
    ca-certificates \
    perl \
    libc6-dev \
    libc6-dev-i386 \
    gcc-x86-64-linux-gnu \
    binutils-x86-64-linux-gnu \
    && rm -rf /var/lib/apt/lists/*

# iPXE source — pinned to a well-known stable tag; update as needed.
# We use HTTPS and validate with ca-certificates.
ARG IPXE_VERSION=v1.21.1
WORKDIR /build
RUN git clone --depth 1 --branch ${IPXE_VERSION} https://github.com/ipxe/ipxe.git .

# Embed a minimal chainloader that fetches the rendered boot.ipxe from nginx
# at runtime using ${next-server} (DHCP siaddr — set to the seed machine IP).
# boot.ipxe itself is rendered at deploy time and served over HTTP by nginx.
COPY chain.ipxe /build/src/chain.ipxe

WORKDIR /build/src

# Build for x86-64 (always — needed for undionly.kpxe and EFI).
# Cross-compile if the build host is ARM64.
# iPXE v1.21.1 triggers several -Werror warnings under GCC 12 (Debian bookworm).
# Suppress them without touching the iPXE source.
ENV EXTRA_CFLAGS="-Wno-error=array-bounds -Wno-error=stringop-overflow -Wno-error=maybe-uninitialized -Wno-error=dangling-pointer"
RUN if [ "${TARGETARCH}" = "arm64" ]; then \
        CROSS_COMPILE=x86_64-linux-gnu- make bin/undionly.kpxe EMBED=chain.ipxe -j$(nproc) EXTRA_CFLAGS="${EXTRA_CFLAGS}"; \
        CROSS_COMPILE=x86_64-linux-gnu- make bin-x86_64-efi/ipxe.efi EMBED=chain.ipxe -j$(nproc) EXTRA_CFLAGS="${EXTRA_CFLAGS}"; \
    else \
        make bin/undionly.kpxe EMBED=chain.ipxe -j$(nproc) EXTRA_CFLAGS="${EXTRA_CFLAGS}"; \
        make bin-x86_64-efi/ipxe.efi EMBED=chain.ipxe -j$(nproc) EXTRA_CFLAGS="${EXTRA_CFLAGS}"; \
    fi

# Collect outputs.
RUN mkdir -p /out && \
    cp bin/undionly.kpxe /out/undionly.kpxe && \
    cp bin-x86_64-efi/ipxe.efi /out/ipxe.efi

# ---------------------------------------------------------------------------
# Final stage — just the built files.
# docker-compose copies them to the nginx tftp/ipxe volume.
# ---------------------------------------------------------------------------
FROM debian:bookworm-slim AS artifacts
COPY --from=builder /out/ /
