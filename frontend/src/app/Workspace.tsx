import type { ReactNode } from "react";

/** The call monitor's header is the shared workspace chrome, not a feature theme. */
export function Workspace({
  title,
  note,
  className = "",
  children,
}: {
  title: ReactNode;
  note?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={`workspace ${className}`}>
      <header className="workspace-topbar">
        <span>{title}</span>
        {note && <small>{note}</small>}
      </header>
      {children}
    </div>
  );
}
