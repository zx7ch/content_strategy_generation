"use client";

import { usePathname } from "next/navigation";

import type { NavigationItem } from "@/lib/types";

export function useSidebar(items: NavigationItem[]) {
  const pathname = usePathname();
  return items.map((item) => ({
    ...item,
    active: pathname === item.href
  }));
}
