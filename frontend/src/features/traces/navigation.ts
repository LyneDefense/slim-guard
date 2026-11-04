import { userCallsPath } from "../users/navigation";

/** A detail may return only to this user's call list, never a different owner. */
export function callListReturn(value: string | null, userId: string): string {
  const fallback = userCallsPath(userId);
  if (!value) return fallback;
  try {
    const url = new URL(value, "https://admin.invalid");
    if (url.origin === "https://admin.invalid" && url.pathname === fallback) {
      return url.pathname + url.search;
    }
  } catch {
    /* Fall back to the known owner, including for broken deep links. */
  }
  return fallback;
}
