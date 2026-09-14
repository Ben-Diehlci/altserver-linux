#!/usr/bin/python3

import re
import sys

F = sys.argv[1]

with open(F, 'rb') as f:
    content = f.read()

content = re.sub(br'L("([^"\\]|\\.)*")', br'U(\1)', content)
content = content.replace(b'std::wstring', b'std::string')
content = content.replace(b'boost/filesystem.hpp', b'filesystem')
content = content.replace(b'boost::filesystem', b'std::filesystem')

content = content.replace(b'"%FT%T%z"', b'"%Y-%m-%dT%H:%M:%SZ"')
content = content.replace(b'localtime(', b'gmtime(')


# --- Make a non-200 from Apple's auth endpoint legible -------------------------------
# AppleAPI+Authentication.cpp logs the HTTP status and then DISCARDS it, feeding the body
# straight to plist_from_xml. A 429 body is not plist XML, so it fails to parse and the user
# is told "Server returned invalid response" (APIErrorCode::InvalidResponse, 17) -- which
# sends them looking for a protocol bug when Apple is simply rate limiting them.
# Observed for real: auth request 1 -> 200, request 2 -> 429, reported as "invalid response".
# Same defect class as the unchecked extract_json() this project already fixed in
# src/AnisetteDataManager.cpp: log the status, ignore it, mis-report the consequence.
_auth_old = (
    b'\t\t\t\todslog("Received auth response status code: " << response.status_code());\r\n'
)
_auth_new = (
    b'\t\t\t\todslog("Received auth response status code: " << response.status_code());\r\n'
    b'\t\t\t\tif (response.status_code() != 200)\r\n'
    b'\t\t\t\t{\r\n'
    b'\t\t\t\t\todslog("WARNING: Apple\'s auth endpoint returned HTTP " << response.status_code()\r\n'
    b'\t\t\t\t\t\t<< ". If this is 429, Apple is RATE LIMITING this machine or Apple ID: wait "\r\n'
    b'\t\t\t\t\t\t   "30-60 minutes and try ONCE more. Do NOT retry in a loop -- repeated failed "\r\n'
    b'\t\t\t\t\t\t   "attempts are how Apple IDs get locked. Any \'invalid response\' error below is "\r\n'
    b'\t\t\t\t\t\t   "misleading: the body is simply not a plist.");\r\n'
    b'\t\t\t\t}\r\n'
)

if F.endswith('AppleAPI+Authentication.cpp'):
    if content.count(_auth_old) != 1:
        sys.stderr.write(
            "rewrite_altsign_source.py: auth status-code patch matched %d times, expected 1.\n"
            "  upstream AppleAPI+Authentication.cpp changed; re-check before removing this guard.\n"
            % content.count(_auth_old))
        sys.exit(1)
    content = content.replace(_auth_old, _auth_new)

content = content.replace(b'winsock2.h', b'WinSock2.h')

sys.stdout.buffer.write(content)