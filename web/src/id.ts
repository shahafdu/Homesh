/** Random identifiers that work where this app actually runs.
 *
 * `crypto.randomUUID()` is restricted to secure contexts — https, or localhost.
 * Every screen in the house reaches the server over plain http at an address on
 * the LAN, which is not one, so there it is simply `undefined`.
 *
 * That made it the worst kind of dependency: it works perfectly on the machine
 * the code is written on and throws on every device it was written for. The TV
 * app called it while deriving its device identity, so the failure landed before
 * pairing had even started and left the screen showing "Ready" forever.
 *
 * `crypto.getRandomValues()` carries no such restriction and is present in every
 * WebView this targets.
 */
export function randomId(): string {
  const bytes = new Uint8Array(16);

  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    // Nothing this old is expected. It is here so that a missing crypto object
    // degrades to a duplicate-prone id rather than to an exception on boot —
    // this identifier names a television, it does not protect anything.
    for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  }

  // Version 4, variant 1, so the value is a well-formed UUID wherever it lands.
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;

  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
