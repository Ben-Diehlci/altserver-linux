ARG IMAGE=arm64v8/alpine:3.15

FROM $IMAGE

RUN  apk add zsh git curl wget g++ clang boost-static ninja boost-dev cmake make sudo bash vim libressl-dev util-linux-dev zlib-dev zlib-static

RUN mkdir /buildenv

WORKDIR /buildenv

RUN curl -JO 'https://developer.apple.com/file/?file=security&agree=Yes' -H 'Referer: https://developer.apple.com/security/' \
 && unzip -q corecrypto.zip \
 && rm -rf __MACOSX \
 && if [ ! -d corecrypto ]; then mv corecrypto-* corecrypto; fi \
 && test -f corecrypto/CMakeLists.txt
WORKDIR /buildenv/corecrypto

# Apple's corecrypto distribution include()s a code-coverage helper it does not ship:
# scripts/ contains only the testvector converters. CODE_COVERAGE is off by default, so
# this include is the only reference that actually breaks configure. Cf. upstream PR #85.
RUN sed -i -E 's|^include\(scripts/code-coverage\.cmake\)|#&|' CMakeLists.txt

# Apple's 2024 distribution moved ccrng_static.c to the tree root but left CoreCryptoSources.cmake
# still pointing at the old corecrypto_static/ subdirectory, which no longer exists. CMake reports
# this as "Cannot find source file: corecrypto_static/ccrng_static.c" and then, confusingly, as
# "No SOURCES given to target: corecrypto_static" -- it is the second message people notice and
# the first one that actually explains it. Exactly one entry is affected.
RUN test -f ccrng_static.c && ! test -d corecrypto_static \
 && sed -i 's|"corecrypto_static/ccrng_static.c"|"ccrng_static.c"|' CoreCryptoSources.cmake \
 && ! grep -q 'corecrypto_static/ccrng_static.c' CoreCryptoSources.cmake

RUN mkdir build; cd build; CC=clang CXX=clang++ cmake ..;
WORKDIR /buildenv/corecrypto/build
RUN sed -i -E 's|^(all: CMakeFiles\/corecrypto_perf)|#\1|' CMakeFiles/Makefile2; sed -i -E 's|^(all: CMakeFiles\/corecrypto_test)|#\1|' CMakeFiles/Makefile2
RUN make -j6; make install

WORKDIR /buildenv

RUN git clone --recursive https://github.com/microsoft/cpprestsdk; 
WORKDIR /buildenv/cpprestsdk
RUN sed -i 's|-Wcast-align||' "./Release/CMakeLists.txt"
RUN mkdir build; cd build; cmake -DBUILD_SHARED_LIBS=0 ..; make -j6; make install

WORKDIR /buildenv/

RUN git clone https://github.com/nih-at/libzip && cd libzip; mkdir build; cd build; cmake -DBUILD_SHARED_LIBS=0 ..; make -j6; make install


