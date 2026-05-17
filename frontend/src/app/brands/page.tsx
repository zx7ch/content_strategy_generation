"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { CreateBrandPanel } from "@/components/brand/CreateBrandPanel";
import { DataTable } from "@/components/dashboard/DataTable";
import { StatCard } from "@/components/dashboard/StatCard";
import { LiveApiErrorState } from "@/components/ui/LiveApiErrorState";
import { getBrandsPageData, getRuntimeApiErrorMessage } from "@/lib/api";
import type { BrandsPageData } from "@/lib/api";
import type { Brand } from "@/lib/types";

export default function BrandsPage() {
  const [data, setData] = useState<BrandsPageData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getBrandsPageData()
      .then(setData)
      .catch((err) => setError(getRuntimeApiErrorMessage(err)));
  }, []);

  if (error) {
    return (
      <LiveApiErrorState
        title="品牌列表 Live API 读取失败"
        message={error}
        retryLabel="重试"
        onRetry={() => {
          setError(null);
          setData(null);
          getBrandsPageData()
            .then(setData)
            .catch((err) => setError(getRuntimeApiErrorMessage(err)));
        }}
      />
    );
  }

  if (!data) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-gray-400">
        正在加载品牌数据...
      </div>
    );
  }

  const { brands, stats } = data;

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-4 rounded-panel border border-white/70 bg-white/85 p-6 shadow-panel backdrop-blur sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm text-quiet">Brands</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-ink">品牌与配置</h1>
          <p className="mt-2 text-sm text-quiet">当前数据源: Live API</p>
        </div>
        <CreateBrandPanel />
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <StatCard value={String(stats.activeBrands)} label="活跃品牌" />
        <StatCard value={String(stats.connectedAccounts)} label="接入账号" />
      </section>

      <DataTable<Brand>
        columns={[
          { key: "name", header: "品牌名称", render: (brand) => brand.name },
          { key: "stage", header: "阶段", render: (brand) => `${brand.stage === "Growth" ? "增长期" : brand.stage === "Seed" ? "种子期" : "成熟期"} (${brand.stage})` },
          { key: "targetAudience", header: "目标受众", render: (brand) => brand.targetAudience },
          {
            key: "status",
            header: "状态",
            render: (brand) => (
              <span className="rounded-full bg-successBg px-2.5 py-1 text-xs font-medium text-success">
                {brand.status === "Active" ? "运行中" : "未启用"}
              </span>
            )
          },
          {
            key: "action",
            header: "操作",
            render: (brand) => (
              <Link
                href={`/brands/${brand.id}`}
                className="inline-flex items-center justify-center rounded-full border border-line bg-white px-4 py-2 text-sm font-medium text-ink transition hover:bg-slate-50"
              >
                配置
              </Link>
            )
          }
        ]}
        rows={brands}
      />
    </div>
  );
}
