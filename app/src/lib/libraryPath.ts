/** Agent tools may report Windows separators even though library rows and
 * markdown links always use forward slashes. Only recognize paths inside the
 * library, optionally reached from the adjacent agents directory. Absolute
 * paths and traversal inside courses are left as ordinary text. */
export function libraryPath(raw: string | null | undefined): string | null {
  if (!raw || /[\r\n\0]/.test(raw)) return null;
  const path = raw.trim().replace(/\\/g, "/").replace(/^\.\.?\//, "");
  if (!path.startsWith("courses/")) return null;
  const parts = path.split("/");
  if (parts.some((part) => !part || part === "." || part === "..")) return null;
  return path;
}
