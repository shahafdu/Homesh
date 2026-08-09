# Security Policy

## Reporting a vulnerability

Please report security issues privately via GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository, rather than opening a public issue.

Include what you did, what happened, and what you expected. A proof of concept helps.
Expect an acknowledgement within a week.

## Scope

Hearth is designed to hold personal media and to be reachable from the public internet.
The properties below are treated as security guarantees; a break in any of them is a
vulnerability worth reporting.

- **No inbound exposure of the home network.** The home agent connects outbound only.
  Any design or default that requires port forwarding is a bug.
- **No unauthenticated media access.** Media URLs are HMAC-signed, scoped and
  short-lived. A URL that outlives its TTL, or works for a different user, is a bug.
- **Credentials at rest are useless alone.** OAuth tokens are envelope-encrypted with a
  key held outside the database; session tokens are stored only as hashes. A database
  dump that yields a usable token or session is a bug.
- **No public registration.** The first account requires a bootstrap code issued on the
  server console; later accounts require an admin. Any path around this is a bug.
- **Path confinement.** Source connectors must refuse any path outside their configured
  roots, checked after symlink resolution.

## Out of scope

- Attacks requiring physical access to an already-unlocked machine
- Denial of service through sheer request volume against a self-hosted instance
- Vulnerabilities in third-party dependencies without a demonstrated exploit path here
