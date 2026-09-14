#!/usr/bin/env bash
#
# Does Apple's GrandSlam edge refuse a SECOND request on a REUSED TCP connection?
#
# WHY THIS IS SAFE. It sends only `o=init`, the opening step of the SRP exchange, which carries
# NO password -- just a public ephemeral value and a username. It uses a deliberately nonexistent
# address, so no real account is touched and nothing can be locked out. Four requests total.
#
# WHAT IT DISTINGUISHES. AltServer-Linux issues its two GrandSlam requests through one pooled
# keep-alive connection (AppleAPI holds a singleton _gsaClient and never sets `Connection`), and
# the second comes back 429. This reproduces that shape without AltServer and without an Apple ID:
#
#   Run A -- both requests on ONE socket, via curl --next
#   Run B -- each request on its OWN socket, with Connection: close
#
# READING THE RESULT (the num_connects column is the point: 0 means the socket was reused):
#
#   A = 200,429  and  B = 200,200  -> CONFIRMED. Connection reuse is the cause. The fix in
#                                     rewrite_altsign_source.py (a fresh client per GSA call)
#                                     is correct.
#   A = 200,200  and  B = 200,200  -> REFUTED. Reuse is fine; the fault is in the o=complete
#                                     body instead, and the fix will not help.
#   A and B both 429 on request 2  -> Neither: something per-IP or per-identity. Investigate
#                                     upstream drift before spending more Apple attempts.
#   Anything 503                   -> The client-info block, not this. Check X-MMe-Client-Info.
#
# ABOUT -k / --insecure. It is REQUIRED here and is not a shortcut. gsa.apple.com is served with
# a certificate issued by "Apple Server Authentication CA" -- Apple's own private CA, which is in
# no public trust store, so curl rejects the chain as self-signed and never sends the request.
# AltServer hits the same wall and handles it the same way: AppleAPI.cpp sets
# config.set_validate_certificates(false) for exactly this client. Validating here would measure
# TLS trust rather than the connection behaviour we are testing.
#
# Usage:  bash docs/gsa-connection-probe.sh
# Needs:  curl. Nothing else, and no credentials.

set -u

ENDPOINT="https://gsa.apple.com/grandslam/GsService2"
PROBE_USER="altserver-probe@example.invalid"   # deliberately nonexistent; RFC 2606 reserved TLD

# Mirrors what AltServer actually sends. Client-info uses com.apple.akd rather than
# com.apple.dt.Xcode -- with the Xcode form Apple 503s the request outright and the probe would
# measure the wrong thing entirely.
CLIENT_INFO='<MacBookPro13,2> <macOS;13.1;22C65> <com.apple.AuthKit/1 (com.apple.akd/1.0)>'
UA='akd/1.0 CFNetwork/978.0.7 Darwin/18.7.0'

BODY_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE"' EXIT

# A well-formed o=init body. A2k is a dummy public ephemeral -- the server cannot tell it is not a
# real SRP value until it tries to use it, which is well after the edge has decided a status code.
cat > "$BODY_FILE" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Header</key>
  <dict><key>Version</key><string>1.0.1</string></dict>
  <key>Request</key>
  <dict>
    <key>A2k</key><data>QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE=</data>
    <key>cpd</key>
    <dict>
      <key>bootstrap</key><true/>
      <key>icscrec</key><true/>
      <key>pbe</key><false/>
      <key>prkgen</key><true/>
      <key>svct</key><string>iCloud</string>
    </dict>
    <key>o</key><string>init</string>
    <key>ps</key><array><string>s2k</string><string>s2k_fo</string></array>
    <key>u</key><string>altserver-probe@example.invalid</string>
  </dict>
</dict>
</plist>
PLIST

FMT='  request %{url_effective}\n    HTTP %{http_code}   new-connections=%{num_connects}   time=%{time_total}s\n'

echo "GrandSlam connection-reuse probe"
echo "  endpoint : $ENDPOINT"
echo "  user     : $PROBE_USER  (nonexistent -- no account is touched, no password is sent)"
echo "  tls      : validation disabled (-k), because Apple serves this endpoint from a private CA"
echo "             that is in no public trust store. AltServer does the same. Not a shortcut."
echo

echo "== Run A: two requests, ONE curl invocation (socket REUSED) =="
echo "   expect new-connections=1 then 0. A 429 on the second is the failure we are chasing."
curl -k -sS -o /dev/null -w "$FMT" \
  -X POST "$ENDPOINT" \
  -H "Content-Type: text/x-xml-plist" \
  -H "Accept: */*" \
  -H "User-Agent: $UA" \
  -H "X-Mme-Client-Info: $CLIENT_INFO" \
  --data-binary "@$BODY_FILE" \
  --next \
  -k -sS -o /dev/null -w "$FMT" \
  -X POST "$ENDPOINT" \
  -H "Content-Type: text/x-xml-plist" \
  -H "Accept: */*" \
  -H "User-Agent: $UA" \
  -H "X-Mme-Client-Info: $CLIENT_INFO" \
  --data-binary "@$BODY_FILE"

echo
echo "== Run B: two requests, SEPARATE connections (Connection: close) =="
echo "   expect new-connections=1 both times."
for i in 1 2; do
  curl -k -sS -o /dev/null -w "$FMT" \
    -X POST "$ENDPOINT" \
    -H "Content-Type: text/x-xml-plist" \
    -H "Accept: */*" \
    -H "User-Agent: $UA" \
    -H "X-Mme-Client-Info: $CLIENT_INFO" \
    -H "Connection: close" \
    --data-binary "@$BODY_FILE"
  sleep 1
done

echo
echo "Interpretation:"
echo "  A=200,429 and B=200,200  -> connection reuse CONFIRMED; the gsaClient() fix is right"
echo "  A=200,200 and B=200,200  -> REFUTED; the fault is the o=complete body, not the socket"
echo "  both runs 429 on req 2   -> per-IP or per-identity; do not spend more Apple attempts yet"
echo "  any 503                  -> client-info block instead; check X-MMe-Client-Info"
echo "  HTTP 000 everywhere      -> TLS or network, not Apple. Re-check that -k is present."
