# Putting Homesh behind HTTPS

Three things are waiting on this, and none of them are cosmetic:

- **Passkeys on your phone.** WebAuthn only exists in a secure context. Over
  plain http at a LAN address the API is simply absent — there is no prompt to
  answer, and no amount of interface work changes that.
- **Sharing a file from your phone.** `navigator.share` is secure-context-only
  too, which is why the Share button explains itself and points at Download.
- **Reaching the house from outside it.** The wedding video from the office.

Tailscale is the shortest route to all three: a stable hostname, a real
certificate, and **no inbound ports opened at home** — which keeps the
architecture's rule that home components dial out and nothing dials in.

---

## Before you start: the one real risk

**A passkey is bound to the address it was created against.** Yours was created
against `localhost`. The moment the server answers to a different hostname, that
passkey stops working — not a fault, that binding is the point of the mechanism.

So the order below matters. It is written to be recoverable at every step, and
the last line of defence is that reverting the address brings the old passkey
back exactly.

---

## 1. Install Tailscale on the server  (about 5 minutes)

Download from <https://tailscale.com/download/windows>, install, sign in.
A personal account is free and covers far more devices than a house has.

Check it came up:

```powershell
tailscale status
```

Note the machine's name in the output — something like
`homesh.tailnet-name.ts.net`. That is the hostname everything will use.

## 2. Install it on your phone

The app is in both stores. Sign in with the same account. That is all — the phone
can now reach the server from anywhere, including from your office.

## 3. Enable certificates for the tailnet  (once, in the browser)

HTTPS certificates are **off by default** on a new tailnet, and nothing on the
machine can turn them on. Without this step the next command fails with:

> 500 Internal Server Error: your Tailscale account does not support getting TLS certs

Which reads like an account limitation and is not one — it is a switch nobody has
flipped yet. In the admin console at <https://login.tailscale.com/admin/dns>:

1. **MagicDNS** — enable it if it is not already. The certificate is issued for
   the MagicDNS name, so there is no name to put on one until this is on.
2. **HTTPS Certificates** — enable.

That page also shows your tailnet name, which is the part after the machine name
in `homesh.tailnet-name.ts.net`.

## 4. Turn on HTTPS  (about 2 minutes)

Tailscale issues and renews a real certificate for that hostname:

```powershell
tailscale cert homesh.tailnet-name.ts.net      # once, to prove it works
tailscale serve --bg --https=443 http://localhost:8080
```

The second line puts Homesh behind HTTPS on port 443 of that hostname. Check it:

```powershell
tailscale serve status
```

Note the target is a **local port**, not the port to listen on. `tailscale serve
443` means "proxy to 443 on this machine", which is not what anybody means by it
and produces a page that will not load.

**Funnel is almost certainly not what you want.** Reaching the server from
somewhere else — the office, a hotel — only needs Tailscale on the device doing
the reaching. Your phone is already on the tailnet, so it can reach the server
from anywhere in the world without the server being on the public internet at
all.

Funnel is for the other case: handing a link to somebody who is *not* on your
tailnet. It puts your login page on the public internet, and the hostname is
published in certificate transparency logs, so it will be found by scanners
within days. If you never need to send a stranger a link, leave it off.

If you turned it on and want it off:

```powershell
tailscale serve reset
tailscale serve --bg http://127.0.0.1:8080
```

## 5. Take a link code, *before* switching

On the PC, still at `http://localhost:8080` and still signed in:

**Settings → Use on another device.** Write the eight-character code down.

This is your way back in after the address changes, and it can only be issued
while signed in — which you will not be, a minute from now.

## 6. Switch the address

```powershell
.\tools\set-origin.ps1 -Origin https://homesh.tailnet-name.ts.net
docker compose up -d
```

The script prints what it is about to invalidate before it does it.

## 7. Sign in at the new address, and enrol a passkey

Open `https://homesh.tailnet-name.ts.net` on the PC.

1. Choose **Use a code** and enter the code from step 5
2. Go to **Settings → Add a passkey to this device**

Now do the same on your phone: open the same address, **Use on another device**
from the PC for a fresh code, then add a passkey there too. From then on the
phone signs in with a fingerprint, and Share works.

## If anything goes wrong

```powershell
.\tools\set-origin.ps1 -Revert
docker compose up -d
```

Back to `http://localhost:8080`, with the original passkey working exactly as
before. Nothing is lost by trying.

---

## What changes once this is done

- Passkeys work on every device, not just the machine the server runs on
- Sharing a file sends the file, from your phone, to WhatsApp or mail
- The library is reachable from outside the house if you enabled Funnel
- Session cookies become `Secure` automatically — the server derives that from
  the origin, so there is nothing else to set

## What does not change

- The TV app keeps using the LAN address it discovers, which is faster in the
  house and needs no certificate
- The Denon is spoken to directly over the LAN, as before
- Nothing is opened on your router
