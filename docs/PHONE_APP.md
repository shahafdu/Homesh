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

## Other apps beside Homesh

`PHONE_APPS` in the PC's `.env` lists other web apps to offer as buttons under
**Open Homesh** -- `Name|address on the PC`, separated by semicolons. The phone
learns the list from the PC, never from the standby, and replaces it whole each
time, so an app removed on the PC disappears from the phone too.

An optional third field is the same app's address on the standby:
`Name|address on the PC|address on the standby`. Without it, the button opens
the PC's address straight away in a tab, the way Homesh opens.

With it, the app opens in a screen of the phone app's own (from 1.6.0), which
keeps it on whichever machine is answering:

- **Opening** asks both machines at once. The PC is used if it answers within
  2.5 seconds, the standby otherwise, so a PC that is off or overloaded costs a
  couple of seconds rather than the sum of two timeouts.
- **A page that fails**, returns a server error, or (on the PC) is still
  loading after 8 seconds moves the app to the other machine, if it answers.
  Nothing is on screen to lose then, so it happens by itself, but at most once
  every 30 seconds: when both machines are struggling it stays on one and says
  so, with a tap to try again.
- **Once a minute while it is on screen**, a keepalive asks the PC. When two
  checks in a row agree that the other machine is the better one, a PC that
  keeps not answering or one that is back, the strip offers it. Nothing is
  reloaded: a tap moves, and otherwise the move waits for the next page you
  open, since the view is changing then anyway.

A page you are reading is never reloaded unasked (from 1.6.1). In 1.6.0 every
check that disagreed moved the app at once, and on a weak signal, where every
answer is late, the PC looked down to one check and up to the next. The result
was a reload every few seconds, each one back at the top of the page.

Wherever a move does happen, the page keeps its place: the same path on the
other machine, scrolled to where it was, the inner panes too. The phone keeps
the position itself, since the two machines are different sites to the page
and share none of its storage. A strip at the top says when the app is on the
standby, and tapping it checks the PC there and then.

Why not a tab, like Homesh: once a Custom Tab is showing, the phone app is
behind it, and Android 15 onwards does not let an app behind another one start
anything. So a tab can be pointed at the right machine when it opens, and never
corrected afterwards. The cost of a WebView is a cookie jar of its own and no
passkeys or Google sign-in. Each app is signed into once there, with its PIN.
Homesh needs its passkey, so it stays in a tab.

### What the phone keeps, and how

The addresses come from the PC at run time: the PC's on the house network and
its tailnet name, the standby's, and this list of apps. None of them is in the
app or in this repository. The phone has to remember them, because the time it
needs the standby's address is when the PC is off and cannot be asked.

They are stored encrypted (from 1.6.0), with AES-GCM under a key made in the
Android Keystore. The key cannot be read out by the app or by anybody else,
and on most phones it is held in hardware, so a copy of the app's files holds
only ciphertext. Values stored in the clear by an older version are encrypted
the first time they are read. Below Android 6, which has no Keystore AES,
nothing is kept, and the phone asks the PC each time.

One exception: an app opened in its own screen is a WebView, and a WebView keeps
its own history and cookies, which name the app's address. That data is private
to the phone app, like everything else it stores, but it is not encrypted by it.

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
