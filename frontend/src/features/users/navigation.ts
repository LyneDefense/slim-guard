/** Canonical user entry points shared by the directory and scoped workspaces. */
export const userCallsPath = (userId: string) =>
  `/users/${encodeURIComponent(userId)}`;

export function userDirectoryReturn(value: string | null): string {
  if (!value) return "/users";
  try {
    const url = new URL(value, "https://admin.invalid");
    if (
      url.origin === "https://admin.invalid" &&
      ["/users", "/users/directory"].includes(url.pathname)
    ) {
      return url.pathname + url.search;
    }
  } catch {
    /* A malformed return target must not break navigation. */
  }
  return "/users";
}
