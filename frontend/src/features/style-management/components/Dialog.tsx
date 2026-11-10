import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
export function Dialog({
  title,
  children,
  onClose,
  drawer = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  drawer?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const background = Array.from(document.body.children).filter(
      (item) => item !== ref.current?.parentElement,
    ) as HTMLElement[];
    const previousInert = background.map((item) => item.inert);
    background.forEach((item) => {
      item.inert = true;
    });
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    ref.current?.focus();
    const handle = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current();
      }
      if (event.key !== "Tab") return;
      const elements = Array.from(
        ref.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], summary, [tabindex="0"]',
        ) ?? [],
      ).filter((item) => !item.closest("[hidden]"));
      const first = elements[0];
      const last = elements.at(-1);
      if (!first) {
        event.preventDefault();
        return;
      }
      if (
        event.shiftKey &&
        (document.activeElement === first ||
          document.activeElement === ref.current)
      ) {
        event.preventDefault();
        last?.focus();
      } else if (
        !event.shiftKey &&
        (document.activeElement === last ||
          document.activeElement === ref.current)
      ) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handle);
    return () => {
      document.removeEventListener("keydown", handle);
      background.forEach((item, i) => {
        item.inert = previousInert[i];
      });
      document.body.style.overflow = previousOverflow;
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  if (typeof document === "undefined") return null;
  return createPortal(
    <div
      className={`studio-overlay ${drawer ? "studio-drawer-overlay" : ""}`}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className={`studio-dialog ${drawer ? "studio-drawer" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={ref}
        tabIndex={-1}
      >
        <header>
          <h2 id={titleId}>{title}</h2>
          <button type="button" aria-label={`关闭${title}`} onClick={onClose}>
            关闭
          </button>
        </header>
        {children}
      </div>
    </div>,
    document.body,
  );
}
