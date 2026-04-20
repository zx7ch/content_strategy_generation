import { BrandDetailPanel } from "@/components/brand/BrandDetailPanel";
import { LiveApiErrorState } from "@/components/ui/LiveApiErrorState";
import { getLiveApiErrorMessage, getServerBrandDetailPageData } from "@/lib/server-api";

export default async function BrandDetailPage({
  params
}: {
  params: { id: string };
}) {
  try {
    const data = await getServerBrandDetailPageData(params.id);
    return (
      <BrandDetailPanel
        brand={data.brand}
        channels={data.channels}
        latestExtensionCaptureSession={data.latestExtensionCaptureSession}
        latestDataImportPreview={data.latestDataImportPreview}
        source={data.source}
      />
    );
  } catch (error) {
    return (
      <LiveApiErrorState
        title="品牌详情 Live API 读取失败"
        message={getLiveApiErrorMessage(error)}
      />
    );
  }
}
