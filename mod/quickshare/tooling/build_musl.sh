#!/bin/sh
# Build source, not a downloaded camera executable. Run inside WSL Debian.
set -eu
base=${NX_QS_TOOLCHAIN_WORK:-"$HOME/nx300m-quickshare-toolchain"}
prefix=${NX_QS_MUSL_PREFIX:-"$base/musl-armv7"}
cross=${NX_QS_CROSS_ROOT:-/opt/nx300m-toolchain/usr}
archive="$base/musl-1.2.5.tar.gz"
expected=a9a118bbe84d8764da0ea0d28b3ab3fae8477fc7e4085d90102b8596fc7c75e4
mkdir -p "$base"
if ! printf '%s  %s\n' "$expected" "$archive" | sha256sum -c --status 2>/dev/null; then
    curl --fail --location --continue-at - --connect-timeout 15 --max-time 55 \
        https://musl.libc.org/releases/musl-1.2.5.tar.gz -o "$archive"
fi
printf '%s  %s\n' "$expected" "$archive" | sha256sum -c -
tar -xzf "$archive" -C "$base"
cd "$base/musl-1.2.5"
export LD_LIBRARY_PATH="$cross/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
CC="$cross/bin/arm-linux-gnueabi-gcc" CFLAGS='-Os -march=armv7-a -mfloat-abi=soft' \
    ./configure --target=arm-linux-musleabi --prefix="$prefix" --disable-shared
make -j"${NX_QS_JOBS:-4}" AR="$cross/bin/arm-linux-gnueabi-ar" RANLIB="$cross/bin/arm-linux-gnueabi-ranlib"
make install AR="$cross/bin/arm-linux-gnueabi-ar" RANLIB="$cross/bin/arm-linux-gnueabi-ranlib"
