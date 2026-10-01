FROM debian:trixie-slim AS build
ARG ASTERISK_VERSION=22.11.0
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config curl ca-certificates bzip2 \
    libedit-dev libjansson-dev libxml2-dev libsqlite3-dev uuid-dev \
    libssl-dev libncurses-dev libcurl4-openssl-dev libsrtp2-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSL https://downloads.asterisk.org/pub/telephony/asterisk/releases/asterisk-${ASTERISK_VERSION}.tar.gz -o source.tar.gz \
    && tar -xzf source.tar.gz --strip-components=1 && rm source.tar.gz
RUN ./configure --with-pjproject-bundled --with-jansson-bundled=no \
    && make menuselect.makeopts \
    && menuselect/menuselect --disable BUILD_NATIVE --disable-category MENUSELECT_CORE_SOUNDS --disable-category MENUSELECT_MOH --disable-category MENUSELECT_EXTRA_SOUNDS menuselect.makeopts \
    && (make -j2 || (cat third-party/pjproject/source/pjnath/build/output/*/*.log third-party/pjproject/*log 2>/dev/null; make -j1 NOISY_BUILD=yes)) && make install DESTDIR=/install \
    && test -f /install/usr/lib/asterisk/modules/chan_websocket.so

FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates python3 libedit2 libjansson4 libxml2 libsqlite3-0 libuuid1 \
    libssl3t64 libncurses6 libcurl4t64 libsrtp2-1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /install/usr/ /usr/
COPY --from=build /install/var/lib/asterisk/ /var/lib/asterisk/
COPY deploy/sip-gateway/entrypoint.py /entrypoint.py
ENTRYPOINT ["python3", "/entrypoint.py"]
