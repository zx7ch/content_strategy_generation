import type { PropsWithChildren } from "react";

interface CardProps {
  className?: string;
}

export function Card({ children, className = "" }: PropsWithChildren<CardProps>) {
  return (
    <section
      className={[
        "rounded-panel border border-white/70 bg-white/85 p-5 shadow-panel backdrop-blur",
        className
      ].join(" ")}
    >
      {children}
    </section>
  );
}
