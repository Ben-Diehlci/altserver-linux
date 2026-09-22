# Draft comment for upstream PR #135

Post at <https://github.com/NyaMisty/AltServer-Linux/pull/135>.

**Before posting, check:** it contains no Apple ID, no device UDID, and no machine identifier.
The anisette `X-Mme-Device-Id` and your UDID are deliberately omitted below - don't paste raw logs
in without redacting them.

---

Independent end-to-end confirmation of this PR's premise, tested against live Apple
infrastructure rather than by `curl` alone. It reproduces in **both** directions.

Setup: AltServer-Linux built from `new` with this patch applied, running on Ubuntu 24.04
(x86_64), anisette supplied locally by `dadoum/anisette-v3-server`, real Apple ID, 2026-09-14.

The anisette server returns a client-info string containing the substring in question:

```
X-MMe-Client-Info: <MacBookPro13,2> <macOS;13.1;22C65> <com.apple.AuthKit/1 (com.apple.dt.Xcode/3594.4.19)>
```

I put the sanitization behind an env var so I could A/B it in place. Results, same machine, same
Apple ID, ~80 seconds apart:

**Sanitization ON** - client-info rewritten to `com.apple.akd`:

```
Device Description: <MacBookPro13,2> <macOS;13.1;22C65> <com.apple.AuthKit/1 (com.apple.akd/3594.4.19)>
Received auth response status code: 200
```

**Sanitization OFF** - `com.apple.dt.Xcode` sent verbatim:

```
Device Description: <MacBookPro13,2> <macOS;13.1;22C65> <com.apple.AuthKit/1 (com.apple.dt.Xcode/3594.4.19)>
Received auth response status code: 503
```

So `gsa.apple.com` rejects the request outright when the substring is present, and accepts it
when rewritten. Without this patch you don't get past the first GSA request at all.

Two things worth adding for anyone landing here:

**Sign-in still doesn't complete for me**, but the failure has moved and is unrelated to this
patch. With sanitization on I now get `200` on the first GSA request and **`429` on the second**,
consistently - identical across attempts 28 minutes apart, and on the very first attempt ever made
from this machine, so it doesn't look like ordinary volume throttling. That's a separate problem
from the one this PR fixes; I'm still digging. Mentioning it so nobody concludes this patch is
ineffective when they hit it.

**The error you'll see is misleading.** `AppleAPI+Authentication.cpp` logs the HTTP status and then
discards it, passing the body to `plist_from_xml` regardless. A 503 or 429 body isn't plist XML, so
it fails to parse and surfaces as `APIErrorCode::InvalidResponse` - "Server returned invalid
response", error 17 - which sends you looking for a protocol bug. Checking the status before
parsing makes both of these diagnosable immediately, and is worth doing independently of this PR.

Happy to provide more detail if useful.
