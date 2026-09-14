#!/usr/bin/python3

import re
import sys

F = sys.argv[1]

with open(F, 'rb') as f:
    content = f.read()

content = content.replace(br'\\\\', br'/') # c escape + regex escape = 2*2
content = content.replace(br'\\', br'/')
content = re.sub(br'/(\.[A-Za-z])', br'\\\\\1', content)

# --- AltStore issue #131: apps install, then crash at launch on iOS 26+ ---------------
#
# ldid truncates the CodeDirectory hash to 20 bytes BEFORE capturing the SHA-256 value that
# becomes the hash-agility attribute (OID 1.2.840.113635.100.9.2). The attribute therefore
# carries a SHA-256 hash chopped to 20 bytes, CoreTrust rejects the signature, and the app
# dies at launch with no crash report -- while the INSTALL reports success, which is what
# makes this so deceptive to diagnose.
#
# Fix: capture the full hash first, then truncate for the cdhashes array (which genuinely
# wants 20 bytes). Two statements swapped; no behaviour change for the truncated array.
#
# Applied here rather than in upstream_repo/ldid/ldid.cpp because that is a submodule; this
# rewriter is the project's existing mechanism for patching vendored sources at build time.
_ldid_131_old = (
    b'algorithm(hash, blob.data(), blob.size());\r\n'
    b'\t\t\t\t\t\thash.resize(20);\r\n'
    b'\r\n'
    b'\t\t\t\t\t\tif (algorithm.type_ == CS_HASHTYPE_SHA256_256)\r\n'
    b'\t\t\t\t\t\t{\r\n'
    b'\t\t\t\t\t\t\talternateCDSHA256 = hash;\r\n'
    b'\t\t\t\t\t\t}\r\n'
)
_ldid_131_new = (
    b'algorithm(hash, blob.data(), blob.size());\r\n'
    b'\r\n'
    b'\t\t\t\t\t\tif (algorithm.type_ == CS_HASHTYPE_SHA256_256)\r\n'
    b'\t\t\t\t\t\t{\r\n'
    b'\t\t\t\t\t\t\talternateCDSHA256 = hash;\r\n'
    b'\t\t\t\t\t\t}\r\n'
    b'\r\n'
    b'\t\t\t\t\t\thash.resize(20);\r\n'
)

if F.endswith('ldid.cpp'):
    # Fail the build loudly rather than silently shipping broken signatures again.
    if content.count(_ldid_131_old) != 1:
        sys.stderr.write(
            "rewrite_ldid_source.py: the issue #131 hash-agility patch matched %d times, expected 1.\n"
            "  upstream ldid.cpp has changed. Re-check whether alternateCDSHA256 is still captured\n"
            "  after hash.resize(20) before removing this guard.\n" % content.count(_ldid_131_old))
        sys.exit(1)
    content = content.replace(_ldid_131_old, _ldid_131_new)

content = content.replace(br'int main(', br'int __main(')

sys.stdout.buffer.write(content)