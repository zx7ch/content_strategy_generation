"use client";

import { useBrandContext } from "@/components/providers/BrandProvider";
import { workspaceName } from "@/lib/data";

export function TopNav() {
  const { brands, selectedBrandId, setSelectedBrandId, isLoading } = useBrandContext();

  return (
    <header className="sticky top-0 z-20 border-b border-white/70 bg-white/80 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-[1600px] items-center justify-between px-4 sm:px-6">
        <div className="flex items-center gap-6">
          <div className="text-lg font-semibold tracking-tight text-ink">{workspaceName}</div>
          <div className="hidden items-center gap-3 sm:flex">
            <span className="text-sm text-slate-600">工作台</span>
            <select
              className="rounded-full border border-line bg-white px-3 py-1 text-sm text-ink"
              value={selectedBrandId ?? ""}
              onChange={(event) => setSelectedBrandId(event.target.value)}
              disabled={isLoading || brands.length === 0}
            >
              {brands.length === 0 ? <option value="">暂无品牌</option> : null}
              {brands.map((brand) => (
                <option key={brand.id} value={brand.id}>
                  {brand.name}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="hidden rounded-full border border-line bg-white px-3 py-1 text-xs text-quiet sm:block">
            Workspace scoped
          </div>
          <div className="flex h-9 w-9 items-center justify-center rounded-full bg-slate-100 text-sm font-medium text-slate-500">
            U
          </div>
        </div>
      </div>
    </header>
  );
}
