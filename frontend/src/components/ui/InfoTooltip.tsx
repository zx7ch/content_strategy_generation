"use client";

import type { PropsWithChildren, ReactNode } from "react";

interface InfoTooltipProps {
  label: string;
  content: ReactNode;
}

export function InfoTooltip({ label, content }: PropsWithChildren<InfoTooltipProps>) {
  return (
    <span className="group relative inline-flex items-center">
      <button
        type="button"
        aria-label={label}
        className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-slate-300 bg-white text-[10px] font-semibold text-slate-500 transition hover:border-slate-400 hover:text-slate-700 focus:outline-none focus:ring-2 focus:ring-slate-300"
      >
        i
      </button>
      <span className="pointer-events-none absolute left-1/2 top-full z-20 mt-2 hidden w-64 -translate-x-1/2 rounded-2xl border border-slate-200 bg-white px-3 py-2 text-left text-xs leading-5 text-slate-600 shadow-lg group-hover:block group-focus-within:block">
        {content}
      </span>
    </span>
  );
}
