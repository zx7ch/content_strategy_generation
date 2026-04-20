"use client";

import { useState } from "react";

import { BRAND_SELECTION_STORAGE_KEY } from "@/components/providers/BrandProvider";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { createBrand, getRuntimeApiErrorMessage } from "@/lib/api";

const stageOptions = [
  { value: "seed", label: "种子期" },
  { value: "growth", label: "增长期" },
  { value: "mature", label: "成熟期" }
] as const;

export function CreateBrandPanel() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [stage, setStage] = useState<(typeof stageOptions)[number]["value"]>("seed");
  const [audienceSummary, setAudienceSummary] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError("请先填写品牌名称。");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const brand = await createBrand({
        name: trimmedName,
        category: category.trim() || undefined,
        stage,
        audienceSummary: audienceSummary.trim() || undefined
      });
      window.localStorage.setItem(BRAND_SELECTION_STORAGE_KEY, brand.id);
      window.location.assign(`/brands/${brand.id}`);
    } catch (submitError) {
      setError(getRuntimeApiErrorMessage(submitError));
      setSubmitting(false);
    }
  }

  if (!open) {
    return (
      <Button variant="primary" onClick={() => setOpen(true)}>
        + 新建品牌
      </Button>
    );
  }

  return (
    <Card className="w-full max-w-xl space-y-4 border-slate-200 bg-white">
      <div>
        <h2 className="text-base font-semibold text-ink">新建品牌</h2>
        <p className="mt-1 text-sm text-quiet">
          这里创建的是工作台里的品牌配置，不会自动创建小红书账号或发布内容。
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="space-y-2 text-sm text-ink">
          <span>品牌名称</span>
          <input
            className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="例如：山系轻户外"
          />
        </label>
        <label className="space-y-2 text-sm text-ink">
          <span>品类</span>
          <input
            className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
            value={category}
            onChange={(event) => setCategory(event.target.value)}
            placeholder="例如：鞋服 / 美妆 / 母婴"
          />
        </label>
        <label className="space-y-2 text-sm text-ink">
          <span>品牌阶段</span>
          <select
            className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
            value={stage}
            onChange={(event) => setStage(event.target.value as (typeof stageOptions)[number]["value"])}
          >
            {stageOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-2 text-sm text-ink">
          <span>目标人群说明</span>
          <input
            className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
            value={audienceSummary}
            onChange={(event) => setAudienceSummary(event.target.value)}
            placeholder="例如：25-34 岁城市女性"
          />
        </label>
      </div>

      {error ? <p className="text-sm text-rose-600">{error}</p> : null}

      <div className="flex flex-wrap gap-3">
        <Button variant="primary" onClick={() => void handleSubmit()} disabled={submitting}>
          {submitting ? "创建中..." : "创建并进入配置"}
        </Button>
        <Button
          variant="outline"
          onClick={() => {
            if (submitting) {
              return;
            }
            setOpen(false);
            setError(null);
          }}
          disabled={submitting}
        >
          取消
        </Button>
      </div>
    </Card>
  );
}
