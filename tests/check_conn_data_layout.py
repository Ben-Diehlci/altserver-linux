#!/usr/bin/env python3
"""Prove the usbmux network-address patch still classifies both sockaddr layouts correctly.

WHY THIS EXISTS. A usbmux provider hands the client a raw sockaddr in the byte layout of whatever
host produced it, and the two layouts disagree about the first two bytes:

    BSD / macOS   [0] = sa_len, [1] = sa_family        AF_INET 0x02, AF_INET6 0x1E
    Linux         [0..1] = sa_family, uint16 LE        AF_INET 02 00, AF_INET6 0A 00

Vendored libimobiledevice reads byte 1 as the family and byte 0 as a length, which is true only of
the BSD form. Against netmuxd -- how this project reaches the phone over Wi-Fi -- byte 1 is 0x00,
so the family matched nothing and every wireless refresh died with

    Failed to handle request:There was an error connecting to the device.

while the phone sat there plainly visible in the device list. makefiles/libimobiledevice-build/
rewrite_idevice_source.py fixes it. This test guards that fix in two independent ways:

  1. It runs the rewriter against the real submodule source. The rewriter exits non-zero if any of
     its patterns stops matching, so a submodule bump that moves the code fails here rather than
     silently producing a binary without the fix.
  2. It compiles the rewriter's own macros -- extracted from its output, not retyped -- and runs
     them against real captured bytes, including the exact address netmuxd emitted for the phone.

Retyping the macros into this test would defeat the point: the test must fail when the shipped
macros are wrong, not when a copy of them is.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REWRITER = os.path.join(ROOT, "makefiles", "libimobiledevice-build", "rewrite_idevice_source.py")
SOURCE = os.path.join(ROOT, "libraries", "libimobiledevice", "src", "idevice.c")

# Each case: label, the bytes as they arrive in conn_data, and what the parser must decide.
# "inet", "inet6", or None for "must be rejected".
CASES = [
    # Captured from `docker logs netmuxd` on the deployment this fix was written for:
    #   NetworkAddress: Data(02 00 00 00 C0 A8 08 2D ...)
    # sin_family = 2 (LE u16), sin_port = 0, sin_addr = 192.168.8.45. Byte 1 is 0x00 here, which
    # is what the unpatched code choked on.
    ("linux ipv4 (real netmuxd capture)",
     [0x02, 0x00, 0x00, 0x00, 0xC0, 0xA8, 0x08, 0x2D] + [0x00] * 20, "inet"),

    # What a macOS AltServer sees: sa_len = 16, sa_family = AF_INET = 2.
    ("bsd ipv4",
     [0x10, 0x02, 0x00, 0x00, 0xC0, 0xA8, 0x08, 0x2D] + [0x00] * 20, "inet"),

    # Linux AF_INET6 = 10 = 0x0A, little-endian u16.
    ("linux ipv6",
     [0x0A, 0x00, 0x00, 0x00] + [0x00] * 24, "inet6"),

    # BSD AF_INET6 = 30 = 0x1E, with sa_len = 28.
    ("bsd ipv6",
     [0x1C, 0x1E, 0x00, 0x00] + [0x00] * 24, "inet6"),

    # Neither layout. Must still be rejected rather than guessed at.
    ("garbage", [0xFF, 0x7F] + [0x00] * 26, None),
    ("zeroed", [0x00] * 28, None),
]

HARNESS = r"""
#include <stdio.h>
#include <stdint.h>
#include <string.h>

%(macros)s

/* The copy length is half the fix and is easy to regress by editing one number, which no amount
 * of counting the token ALTSERVER_CD_SIZE would notice. Pin the VALUE.
 *
 * 28 is sizeof(struct sockaddr_in6) on both layouts: family/len 2 + port 2 + flowinfo 4 +
 * address 16 + scope id 4. idevice_connect() copies 26 bytes from offset 2 for AF_INET6, so
 * anything under 28 reads past the allocation. Under 16 breaks IPv4 as well -- and 2, the value
 * the unpatched code computed from netmuxd's blob, is the original bug exactly.
 */
_Static_assert(ALTSERVER_CD_SIZE >= 28,
               "ALTSERVER_CD_SIZE must cover sockaddr_in6 plus its scope id (28 bytes); "
               "a smaller value restores the heap over-read this patch exists to remove");

int main(void) {
    int failures = 0;
%(cases)s
    if (failures) { printf("%%d case(s) failed\n", failures); return 1; }
    printf("all cases passed\n");
    return 0;
}
"""

CASE_TMPL = r"""
    {
        const uint8_t cd[] = {%(bytes)s};
        int is4 = ALTSERVER_CD_IS_INET(cd) ? 1 : 0;
        int is6 = ALTSERVER_CD_IS_INET6(cd) ? 1 : 0;
        int want4 = %(want4)d, want6 = %(want6)d;
        if (is4 != want4 || is6 != want6) {
            printf("FAIL %(label)s: is_inet=%%d (want %%d) is_inet6=%%d (want %%d)\n",
                   is4, want4, is6, want6);
            failures++;
        }
        if (is4 && is6) { printf("FAIL %(label)s: matched BOTH families\n"); failures++; }
        /* Reproduce the patched copy exactly: allocate/copy ALTSERVER_CD_SIZE bytes, then read
         * the address back out of THAT buffer rather than out of the original. A truncating
         * size loses the address here, which is what the real bug did. */
        if (is4 && %(check_ip)d) {
            uint8_t copied[ALTSERVER_CD_SIZE];
            uint8_t sa_data[14];
            memcpy(copied, cd, ALTSERVER_CD_SIZE);
            memcpy(sa_data, copied + 2, sizeof(sa_data));
            if (!(sa_data[2] == 0xC0 && sa_data[3] == 0xA8 && sa_data[4] == 0x04 && sa_data[5] == 0x2D)) {
                printf("FAIL %(label)s: address did not survive the copy (got %%u.%%u.%%u.%%u)\n",
                       sa_data[2], sa_data[3], sa_data[4], sa_data[5]);
                failures++;
            }
        }
    }
"""


def strip_patch_block(patched_source):
    """Return the source with our own macro definitions removed.

    The macros necessarily mention conn_data byte offsets -- that is their whole job -- so they
    would match any "no raw byte indexing" check and mask a real regression in the call sites.
    """
    start = patched_source.find("/* --- AltServer-Linux patch:")
    end = patched_source.find("/* --- end AltServer-Linux patch")
    if start == -1 or end == -1:
        return patched_source
    end = patched_source.find("\n", end)
    return patched_source[:start] + patched_source[end:]


def extract_macros(patched_source):
    """Pull the shipped macro block out of the rewriter's output, verbatim."""
    lines = patched_source.splitlines()
    wanted = ("#define ALTSERVER_CD_B", "#define ALTSERVER_CD_IS_INET",
              "#define ALTSERVER_CD_IS_INET6", "#define ALTSERVER_CD_SIZE")
    out = [ln for ln in lines if ln.startswith(wanted)]
    return "\n".join(out)


def main():
    # Skipping is fine on a developer machine with no submodules checked out. In CI it is not:
    # a guard that quietly passes because its input is missing is worse than no guard, since it
    # reports green for exactly the situation it exists to catch.
    strict = bool(os.environ.get("CI"))

    if not os.path.exists(SOURCE):
        msg = ("libraries/libimobiledevice is not checked out "
               "(git submodule update --init --recursive)")
        if strict:
            print("FAIL: " + msg + "\n      Refusing to skip under CI.")
            return 1
        print("SKIP: " + msg)
        return 0

    # 1. The rewriter must still apply. It exits non-zero if a pattern stopped matching.
    proc = subprocess.run([sys.executable, REWRITER, SOURCE],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        print("FAIL: the idevice.c rewriter no longer applies:\n")
        print(proc.stderr.rstrip())
        return 1
    patched = proc.stdout
    print("rewriter applies cleanly to the vendored idevice.c")

    # The rewriter matching its patterns is NOT the same as the fix being present. Assert the
    # shape of the result structurally, so a rewriter that still applies but no longer fixes
    # anything is caught. (An earlier version of this check looked for a literal substring with a
    # trailing semicolon, and a mutant that reintroduced the byte-0 read as a malloc() argument --
    # no semicolon -- sailed straight through it.)
    body = strip_patch_block(patched)

    raw_index = re.findall(r"conn_data\s*\)?\s*\[\s*[01]\s*\]", body)
    if raw_index:
        print("FAIL: patched source still indexes conn_data by raw byte offset %r.\n"
              "      Byte 0 is a length only in the BSD layout and byte 1 is the family only "
              "there too;\n      both must go through the ALTSERVER_CD_* macros." % raw_index)
        return 1

    for token, want in (("malloc(ALTSERVER_CD_SIZE)", 2),
                        ("ALTSERVER_CD_IS_INET(device->conn_data)", 1),
                        ("ALTSERVER_CD_IS_INET6(device->conn_data)", 1)):
        got = body.count(token)
        if got != want:
            print("FAIL: expected %d occurrence(s) of %r in the patched source, found %d"
                  % (want, token, got))
            return 1

    memcpy_sized = len(re.findall(r"memcpy\([^;]*ALTSERVER_CD_SIZE\s*\)", body))
    if memcpy_sized != 2:
        print("FAIL: expected 2 ALTSERVER_CD_SIZE-bounded memcpy calls, found %d" % memcpy_sized)
        return 1

    print("patched source is structurally correct: no raw byte indexing, both copies bounded")

    macros = extract_macros(patched)
    if macros.count("#define") != 4:
        print("FAIL: expected 4 ALTSERVER_CD_* macros in the rewriter output, found:\n" + macros)
        return 1

    cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")
    if not cc:
        if strict:
            print("FAIL: no C compiler available. Refusing to skip the byte-layout cases under CI.")
            return 1
        print("SKIP: no C compiler available to run the byte-layout cases")
        return 0

    # 2. Compile the SHIPPED macros and run them against real captured bytes.
    case_src = []
    for label, data, want in CASES:
        assert len(data) == 28, "%s: conn_data cases are 28 bytes" % label
        case_src.append(CASE_TMPL % {
            "bytes": ", ".join("0x%02X" % b for b in data),
            "want4": 1 if want == "inet" else 0,
            "want6": 1 if want == "inet6" else 0,
            "label": label,
            "check_ip": 1 if "ipv4" in label else 0,
        })

    program = HARNESS % {"macros": macros, "cases": "".join(case_src)}

    tmp = tempfile.mkdtemp(prefix="conn_data_")
    try:
        csrc = os.path.join(tmp, "t.c")
        exe = os.path.join(tmp, "t")
        with open(csrc, "w") as f:
            f.write(program)
        build = subprocess.run([cc, "-O0", "-Wall", "-Werror", "-o", exe, csrc],
                               capture_output=True, text=True)
        if build.returncode != 0:
            print("FAIL: the shipped macros do not compile:\n" + build.stderr.rstrip())
            return 1
        run = subprocess.run([exe], capture_output=True, text=True)
        sys.stdout.write(run.stdout)
        if run.returncode != 0:
            print("\nFAIL: the shipped macros misclassify at least one real address layout.")
            return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nBoth sockaddr layouts classify correctly, including the captured netmuxd address.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
