import { useEffect } from "react";
import { contentKey, type Draft, type StudioContent } from "./model";
const bufferKey = (id: string) => `slimguard-style-studio:${id}`;
export function readDraftBuffer(
  id: string,
): { revision: number; content: StudioContent } | null {
  try {
    if (typeof window === "undefined") return null;
    const value = JSON.parse(sessionStorage.getItem(bufferKey(id)) ?? "null");
    if (
      value &&
      typeof value.revision === "number" &&
      typeof value.content?.expression === "string" &&
      typeof value.content?.avoid === "string" &&
      Array.isArray(value.content?.examples)
    )
      return value;
  } catch {
    /* A browser may disable session storage. */
  }
  return null;
}
export function useDraftBuffer(
  id: string,
  content: StudioContent,
  draft: Draft,
  dirty: boolean,
) {
  const key = contentKey(content);
  useEffect(() => {
    try {
      if (dirty)
        sessionStorage.setItem(
          bufferKey(id),
          JSON.stringify({ revision: draft.revision, content }),
        );
      else sessionStorage.removeItem(bufferKey(id));
    } catch {
      /* Navigation warnings still protect edits if storage is unavailable. */
    }
  }, [id, key, draft.revision, dirty]);
  useEffect(() => {
    if (!dirty) return;
    const unload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    const navigate = (event: MouseEvent) => {
      const link = (event.target as Element)?.closest<HTMLAnchorElement>(
        "a[href]",
      );
      if (
        !link ||
        link.target === "_blank" ||
        link.hasAttribute("download") ||
        link.href === window.location.href ||
        event.defaultPrevented
      )
        return;
      if (
        !window.confirm(
          "草稿还有未保存修改。离开前建议保存；本浏览器会临时保留这些修改。确定离开？",
        )
      ) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", unload);
    document.addEventListener("click", navigate, true);
    return () => {
      window.removeEventListener("beforeunload", unload);
      document.removeEventListener("click", navigate, true);
    };
  }, [dirty]);
}
