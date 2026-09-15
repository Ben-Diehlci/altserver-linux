#!/usr/bin/python3
"""Teach libimobiledevice to read usbmux network addresses in either host's sockaddr layout.

WHY THIS EXISTS. A usbmuxd provider copies the device's sockaddr up to the client verbatim, so
its byte layout is whatever the host that produced it uses. libimobiledevice knows this and says
so, in a FIXME it never resolved: "Improve handling of this platform/host dependent connection
data". The two layouts differ only in their first two bytes:

    BSD / macOS   [0] = sa_len, [1] = sa_family      e.g.  10 02 <port> <ipv4> ...
    Linux         [0..1] = sa_family, uint16 LE      e.g.  02 00 <port> <ipv4> ...

The vendored code understands only the BSD one, which breaks Linux addresses in two places:

  * idevice_connect() reads byte 1 as the family. In the Linux layout that byte is 0x00, which
    matches neither AF_INET (0x02) nor AF_INET6 (0x1E), so it returns IDEVICE_E_UNKNOWN_ERROR.
    AltServer reports that to AltStore as "There was an error connecting to the device."
  * the two places that copy conn_data read byte 0 as a LENGTH. In the Linux layout byte 0 is
    the low half of the family, so the length comes out as 2: the address is truncated away and
    the result is a 2-byte heap buffer that idevice_connect() later reads 16 or 28 bytes out of.

This is not hypothetical. netmuxd -- how this project reaches the phone over Wi-Fi -- gates the
BSD form on target_os = "macos" and emits the native Linux form otherwise, so every wireless
refresh got as far as the device connection and died there, with the phone plainly visible in
the device list.

Byte 1 is zero in the Linux layout and non-zero in the BSD one, so the two are distinguishable
without ambiguity, and everything from offset 2 onward (port at 2, address at 4) is already
identical. Only the family test and the copy length need to change.

Patches to vendored code live here rather than in the submodule so `git submodule update`
cannot quietly drop them. Every substitution below is mandatory: if upstream moves and a pattern
stops matching, this exits non-zero and takes the build down with it, rather than emitting a
binary that is silently missing the fix.
"""

import sys

PATH = sys.argv[1]

with open(PATH, 'r', encoding='utf-8') as f:
    content = f.read()


def replace_once(text, old, new, what):
    """Substitute, or fail the build. A silent miss here costs days to rediagnose."""
    found = text.count(old)
    if found != 1:
        sys.stderr.write(
            'rewrite_idevice_source.py: expected exactly 1 match for the "%s" pattern in %s, '
            'found %d.\n'
            'The vendored libimobiledevice has moved and the usbmux network-address layout fix '
            'no longer applies.\n'
            'Re-derive it before shipping: without it, wireless refresh fails with '
            '"There was an error connecting to the device."\n' % (what, PATH, found)
        )
        sys.exit(1)
    return text.replace(old, new)


HELPERS = '''
/* --- AltServer-Linux patch: host-independent usbmux network addresses --------------------
 * conn_data is a raw sockaddr copied verbatim from whichever host produced it:
 *     BSD/macOS   [0] = sa_len,  [1] = sa_family       (AF_INET 0x02, AF_INET6 0x1E)
 *     Linux       [0..1] = sa_family as a uint16 LE    (AF_INET 02 00, AF_INET6 0A 00)
 * Byte 1 is zero only in the Linux layout, so the two forms can be told apart exactly.
 * Everything from offset 2 on -- port at 2, address at 4 -- is identical in both.
 */
#define ALTSERVER_CD_B(cd, i) (((const uint8_t*)(cd))[(i)])
#define ALTSERVER_CD_IS_INET(cd)  (ALTSERVER_CD_B(cd, 1) == 0x02 || (ALTSERVER_CD_B(cd, 1) == 0x00 && ALTSERVER_CD_B(cd, 0) == 0x02))
#define ALTSERVER_CD_IS_INET6(cd) (ALTSERVER_CD_B(cd, 1) == 0x1E || (ALTSERVER_CD_B(cd, 1) == 0x00 && ALTSERVER_CD_B(cd, 0) == 0x0A))

/* Copy a fixed 28 bytes -- the largest form idevice_connect() reads back (sockaddr_in6 plus
 * scope id) -- rather than trusting byte 0 as a length, which it only is in the BSD layout.
 * usbmuxd_device_info_t::conn_data is a fixed uint8_t[200], so this is always in bounds.
 */
#define ALTSERVER_CD_SIZE 28
/* --- end AltServer-Linux patch ----------------------------------------------------------- */
'''

content = replace_once(
    content,
    '#include "common/debug.h"\n',
    '#include "common/debug.h"\n' + HELPERS,
    'helper injection anchor',
)

# idevice_get_device_list_extended(): byte 0 is not a length on Linux.
content = replace_once(
    content,
    '\t\t\tsize_t addrlen = ((uint8_t*)dev_list[i].conn_data)[0];\n'
    '\t\t\tnewlist[newcount]->conn_data = malloc(addrlen);\n'
    '\t\t\tmemcpy(newlist[newcount]->conn_data, dev_list[i].conn_data, addrlen);',
    '\t\t\tnewlist[newcount]->conn_data = malloc(ALTSERVER_CD_SIZE);\n'
    '\t\t\tmemcpy(newlist[newcount]->conn_data, dev_list[i].conn_data, ALTSERVER_CD_SIZE);',
    'device-list conn_data copy',
)

# idevice_new_from_mux_device(): same truncation, and this is the path AltServer takes.
content = replace_once(
    content,
    '\t\tsize_t len = ((uint8_t*)muxdev->conn_data)[0];\n'
    '\t\tdevice->conn_data = malloc(len);\n'
    '\t\tmemcpy(device->conn_data, muxdev->conn_data, len);',
    '\t\tdevice->conn_data = malloc(ALTSERVER_CD_SIZE);\n'
    '\t\tmemcpy(device->conn_data, muxdev->conn_data, ALTSERVER_CD_SIZE);',
    'mux-device conn_data copy',
)

# idevice_connect(): accept both layouts when deciding the address family.
content = replace_once(
    content,
    '\t\tif (((char*)device->conn_data)[1] == 0x02) { // AF_INET',
    '\t\tif (ALTSERVER_CD_IS_INET(device->conn_data)) { // AF_INET (BSD or Linux layout)',
    'AF_INET family test',
)

content = replace_once(
    content,
    '\t\telse if (((char*)device->conn_data)[1] == 0x1E) { // AF_INET6 (bsd)',
    '\t\telse if (ALTSERVER_CD_IS_INET6(device->conn_data)) { // AF_INET6 (BSD or Linux layout)',
    'AF_INET6 family test',
)

# Byte 1 alone no longer identifies the family, so log both bytes or the message misleads.
content = replace_once(
    content,
    '\t\t\tdebug_info("Unsupported address family 0x%02x", ((char*)device->conn_data)[1]);',
    '\t\t\tdebug_info("Unsupported address family: leading bytes 0x%02x 0x%02x '
    '(BSD sets byte1 to 0x02 or 0x1E; Linux sets byte0 to 0x02 or 0x0A and byte1 to 0x00)", '
    'ALTSERVER_CD_B(device->conn_data, 0), ALTSERVER_CD_B(device->conn_data, 1));',
    'unsupported-family diagnostic',
)

sys.stdout.write(content)
