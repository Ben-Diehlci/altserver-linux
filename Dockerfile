# AltServer-Linux — self-contained image.
#
# Builds the binary from this repo and installs every RUNTIME prerequisite, so nothing has to be
# apt-installed on the host by hand. The two that are easy to miss, and both fail SILENTLY:
#
#   python3                       — AltServer is a -static binary and therefore cannot dlopen
#                                   Bonjour itself. It shells out to python3, which dlopens
#                                   libdns_sd.so on its behalf (libraries/dnssd_loader).
#   libavahi-compat-libdnssd-dev  — NOT ...-libdnssd1. The runtime package ships only
#                                   libdns_sd.so.1, while the code dlopens the UNVERSIONED
#                                   soname, whose symlink comes from the -dev package.
#
# Without either, the server starts, reports nothing wrong, and is permanently undiscoverable
# by the phone. The image verifies both at build time so that cannot ship broken.

# ---------------------------------------------------------------------------------------------
# Build stage — uses the project's own alpine toolchain, which carries the static corecrypto,
# cpprestsdk, boost and libzip this build needs. Override BUILDER for a different architecture:
#   ..._amd64 (default) | ..._aarch64 | ..._armv7 | ..._i386
# ---------------------------------------------------------------------------------------------
ARG BUILDER=ghcr.io/nyamisty/altserver_builder_alpine_amd64

FROM ${BUILDER} AS build

WORKDIR /src
COPY . /src

# The binary is named for the gcc triple (AltServer-x86_64, AltServer-aarch64, …), so normalise
# it to a single known path for the runtime stage to copy.
RUN set -eux; \
    rm -rf build; mkdir -p build /out; \
    cd build; \
    make -f ../Makefile -j"$(nproc)"; \
    cp AltServer-* /out/AltServer; \
    chmod +x /out/AltServer; \
    ls -la /out/AltServer

# ---------------------------------------------------------------------------------------------
# Runtime stage
# ---------------------------------------------------------------------------------------------
FROM debian:bookworm-slim

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        python3 \
        libavahi-compat-libdnssd-dev \
        ca-certificates \
        curl \
        tzdata; \
    rm -rf /var/lib/apt/lists/*; \
    # Fail the BUILD rather than ship an image that cannot advertise. This is the exact call
    # dnssd_loader.cpp makes, so if it works here it works at runtime.
    python3 -c "from ctypes import CDLL; CDLL('libdns_sd.so')"; \
    echo "libdns_sd.so loads OK"

COPY --from=build /out/AltServer /usr/local/bin/AltServer

# Fetches the current AltStore Classic IPA, resolving the URL from AltStore's own catalogue rather
# than a hardcoded one -- a pinned URL silently installs an ever-older AltStore.
# The setup web UI: status dashboard, pairing wizard and the install flow. Run it with
#   docker exec altserver python3 /opt/altserver-web/server.py --host 0.0.0.0
# It is NOT started automatically -- it accepts an Apple ID password, so exposing it should be a
# deliberate act rather than a side effect of deploying.
COPY web/ /opt/altserver-web/

COPY web/fetch_altstore.py /usr/local/bin/fetch-altstore
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint
RUN chmod +x /usr/local/bin/fetch-altstore /usr/local/bin/docker-entrypoint

# AltServerApp writes to the RELATIVE path ./AltServerData, resolved against the working
# directory — so the workdir is load-bearing, not cosmetic. Mount a volume here to persist it.
WORKDIR /data

# Sane defaults; override in compose.
#   ALTSERVER_ANISETTE_SERVER is REQUIRED and deliberately has no default: the server that used
#   to be hardcoded is dead, and a shared anisette identity can get Apple IDs locked.
ENV ALTSERVER_ANISETTE_SERVER=""

# Documents intent only. mDNS needs the host network, so compose must set network_mode: host —
# a published port cannot help, and Bonjour does not cross a bridge.
EXPOSE 51820

# The entrypoint refreshes /data/AltStore.ipa, then execs AltServer with whatever arguments were
# given. No IPA argument = daemon mode; add one (or docker exec) for a one-time install.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint"]
