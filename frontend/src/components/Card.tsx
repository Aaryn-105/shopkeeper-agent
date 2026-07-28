import type { ReactNode } from "react";

export function Card(props: {
  title?: string;
  subtitle?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={
        "rounded-xl border border-slate-200 bg-white p-5 shadow-sm " +
        (props.className ?? "")
      }
    >
      {(props.title || props.right) && (
        <header className="mb-3 flex items-center justify-between">
          <div>
            {props.title && (
              <h2 className="text-base font-semibold text-slate-900">
                {props.title}
              </h2>
            )}
            {props.subtitle && (
              <p className="mt-0.5 text-sm text-slate-500">{props.subtitle}</p>
            )}
          </div>
          {props.right}
        </header>
      )}
      {props.children}
    </section>
  );
}