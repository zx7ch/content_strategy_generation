"use client";

import type { PropsWithChildren } from "react";
import { usePathname } from "next/navigation";

import { Sidebar } from "@/components/layout/Sidebar";
import { TopNav } from "@/components/layout/TopNav";

export function ConsoleLayout({ children }: PropsWithChildren) {
  const pathname = usePathname();
  const isCreator = pathname.startsWith("/creator");

  return (
    <div className="flex min-h-screen flex-col">
      <TopNav />
      <div
        className={[
          "mx-auto flex w-full max-w-[1600px] flex-1 flex-col gap-5 px-4 py-5 sm:px-6 lg:flex-row",
          isCreator ? "min-h-0 py-0 lg:py-0" : ""
        ].join(" ")}
      >
        {isCreator ? null : <Sidebar />}
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}
