"use client";

import useSWR from "swr";

export function usePageData<T>(key: string | null, loader: () => T | Promise<T>) {
  return useSWR(key, async () => Promise.resolve(loader()), {
    revalidateOnFocus: false,
    keepPreviousData: true
  });
}
