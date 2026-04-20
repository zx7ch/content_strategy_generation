"use client";

import Link from "next/link";

import { useSidebar } from "@/hooks/useSidebar";
import { navigationItems } from "@/lib/data";

export function Sidebar() {
  const items = useSidebar(navigationItems);

  return (
    <aside className="w-full shrink-0 lg:w-[240px]">
      <div className="rounded-panel border border-white/70 bg-white/80 p-4 shadow-panel backdrop-blur">
        <div className="px-3 pb-3 text-xs uppercase tracking-[0.16em] text-quiet">核心业务</div>
        <nav className="flex flex-col gap-1">
          {items.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={[
                "flex items-center gap-3 rounded-2xl px-3 py-3 text-sm transition",
                item.active ? "bg-slate-100 text-ink" : "text-slate-600 hover:bg-slate-50"
              ].join(" ")}
            >
              <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-slate-200 text-xs font-semibold text-slate-600">
                {item.icon}
              </span>
              <span>{item.label}</span>
            </Link>
          ))}
        </nav>
      </div>
    </aside>
  );
}
