# The Android TV app

A thin native shell around the same `/tv` page a browser would load. Pairing, the
command socket and playback reporting all live in the web app and are shared with
every other screen, so there is one renderer implementation to keep correct
rather than two that drift.

What the shell adds is the handful of things a browser tab cannot do:

- an entry in the Android TV launcher, with a banner
- a screen that does not sleep in the middle of a film
- playback that starts without somebody pressing something first
- somewhere to put the server address

## Installing it on a box

Build it, then push it over ADB:

```bash
tools/build-tv-apk.sh                 # → build/homesh-tv.apk
adb connect <box-address>:5555        # enable ADB debugging on the box first
adb install -r build/homesh-tv.apk
```

Every CI run also attaches the APK to the workflow run as `homesh-tv-apk`, so a
box can be updated without a build environment.

On first launch the app asks where the server is. Type the address shown under
Settings on your phone — `http://` and the port are filled in automatically, so
`box-address` alone is enough. It checks the address before saving it, because a
typo saved is a black screen with only a remote control to fix it with.

Press **MENU** on the remote at any time to change the address.

## Signing, and why upgrades can break

The APK is signed with a key generated on the machine that built it, kept in
`.local/` and never committed. Android refuses to install an unsigned package,
and it also refuses to *upgrade* one signed by a different key.

So: installs from your own machine upgrade in place, and the first install from a
different machine — or from a CI artifact — needs the old one removed first:

```bash
adb uninstall com.homesh.tv
```

The alternative would be a shared signing key in a public repository, which is a
private key in a public repository.

## Why no Gradle

The app has no third-party dependencies — no AndroidX, no leanback library, just
`android.jar`. That makes the whole build four SDK tools, which is why
`tools/build-tv-apk.sh` is a readable eighty lines rather than a Gradle project,
runs on a CI runner without downloading a toolchain, and produces a 21 KB APK.

If the app ever needs real leanback UI, that trade changes. It does not need it
today: the TV shows one thing at a time and is driven from a phone.

## Cleartext HTTP

`res/xml/network_security_config.xml` permits cleartext, because the server is
reached over plain HTTP on the house network and Android blocks that by default
from API 28 onwards. It cannot be narrowed to one host here — the address is
DHCP-assigned and differs per install, which is the same reason it is not in the
source. When the server gains TLS, that file is what to tighten first.
