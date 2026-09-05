/** Copying text where this app actually runs.
 *
 * `navigator.clipboard` is another secure-context-only API: over plain http at a
 * LAN address it is undefined. Guarding it with `?.` stops the exception but
 * leaves a Copy button that quietly does nothing, which is worse than one that
 * says it failed — the invite link is the whole point of that screen.
 *
 * The textarea-and-execCommand route is deprecated and still works everywhere,
 * including in an insecure context. It is the fallback, not the first choice.
 */
export async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Permission refused or not actually available; try the old way.
    }
  }

  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  // Off-screen but focusable. `display: none` cannot be selected, and iOS
  // scrolls to a visible field it is asked to select.
  field.style.position = "fixed";
  field.style.top = "-1000px";
  field.style.opacity = "0";

  document.body.appendChild(field);
  field.select();
  field.setSelectionRange(0, text.length);

  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(field);
  return ok;
}
