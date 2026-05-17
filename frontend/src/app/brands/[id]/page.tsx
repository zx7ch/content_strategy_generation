"use client";

import { useEffect, useState } from "react";

import { BrandDetailPanel } from "@/components/brand/BrandDetailPanel";
import { LiveApiErrorState } from "@/components/ui/LiveApiErrorState";
import { getBrandDetailPageData, getRuntimeApiErrorMessage } from "@/lib/api";
import type { BrandDetailPageData } from "@/lib/api";

export default function BrandDetailPage({
  params
}: {
  params: { id: string };
}) {
  const [data, setData] = useState<BrandDetailPageData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getBrandDetailPageData(params.id)
      .then(setData)
      .catch((err) => setError(getRuntimeApiErrorMessage(err)));
  }, [params.id]);

  if (error) {
    return (
      <LiveApiErrorState
        title="品牌详情 Live API 读取失败"
        message={error}
        retryLabel="重试"
        onRetry={() => {
          setError(null);
          setData(null);
          getBrandDetailPageData(params.id)
            .then(setData)
            .catch((err) => setError(getRuntimeApiErrorMessage(err)));
        }}
      />
    );
  }

  if (!data) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-gray-400">
        正在加载品牌详情...
      </div>
    );
  }

  return (
    <BrandDetailPanel
      brand={data.brand}
      channels={data.channels}
      latestExtensionCaptureSession={data.latestExtensionCaptureSession}
      latestDataImportPreview={data.latestDataImportPreview}
      source={data.source}
    />
  );
}
