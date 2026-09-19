# Homesh Connect — the phone app

A launcher, not an interface. The whole app is one screen with one job: get you
into Homesh, including on the day the way there is down.

## Why it exists

A phone reaches this server over Tailscale when it is away from the house, and
Android kills that VPN to save battery. The failure is silent and total: the
address stops resolving, the browser shows its own cached error, and it reads as
the server being down. It cost days before the cause was understood.

The fix every time was to open the Tailscale app, which reconnects on its own.
A web page cannot open another app — so the thing that could fix it was the one
thing the failing page could not reach. That is the entire reason this exists.

So the app checks both ways in, and when neither answers it offers the one
action that helps:

1. **The house address** first — faster, and no VPN involved.
2. **The Tailscale address** if that fails.
3. **Open Tailscale** if neither answers, then try again.

## Rendered in a Custom Tab

Three ways to show the site were on the table and only one works.

**A WebView** is the obvious choice and the wrong one. It is a rendering engine
with its own cookie jar and no browser around it: WebAuthn does not work there,
so a passkey cannot be used, and the session is not the one already signed in on
this phone. Wrapping the site that way looks like a real app and is impossible to
sign into — which is the worst arrangement, because it fails only once somebody
has committed to using it.

**A plain `ACTION_VIEW`** fixes sign-in and loses the app: Chrome opens as a
separate task with its own address bar, this screen disappears, and nothing feels
like it was opened.

**A Custom Tab** is the same Chrome — same engine, same cookies, same passkeys,
so signing in simply works — drawn inside this app's own task, with our toolbar
and a close button that comes back here. It needs no library: the protocol is an
`ACTION_VIEW` intent carrying a few documented extras, which suits an app that
has avoided every dependency so far.

## Installing it

From [the releases page](https://github.com/shahafdu/Homesh/releases/latest),
which is deliberately not the server — the app has to be installable when the
server cannot be reached, which is the situation it exists for. Your own server
offers the same build at `/phone`.

## Which version, and updating it

The version installed is shown at the bottom of the screen, under the buttons.
**Check for updates** asks whichever address answers — the house one at home,
the tailnet one away — what build the server offers at `/phone.json`, and if it
is newer the button becomes **Install**, which downloads it and hands it to
Android's own installer prompt. That prompt cannot be skipped by an ordinary app,
and should not be.

From 1.2.0. Earlier builds cannot check, so the move to 1.2.0 is one install by
hand; after that the app keeps itself current.

The update code is in its own file, `PhoneUpdates.java`, compiled into the phone
app only. The television app has its own updater, and its version guard hashes
the sources in its APK — so extending the TV's updater would have offered every
screen an update to code that changes nothing on a screen.

## Addresses

Both are entered once, on first launch, and checked before they are saved. A
typo saved is an app that cannot reach anything and no obvious way to fix it.

Neither address is in this repository or in the APK: they are typed in on the
device and stored on the device. CI checks that — `tools/scan-apk.py` reads every
entry in the built package and fails the build if a private address or a device
id appears in one.

## Shared source with the TV app

Both apps are built from `android/` by the same script with `APP=phone` or
`APP=tv`, sharing `ServerAddress` and the discovery code, and differing in their
manifest and their entry activity. Same four SDK tools, same absence of Gradle —
see [`TV_APP.md`](TV_APP.md) for why.
