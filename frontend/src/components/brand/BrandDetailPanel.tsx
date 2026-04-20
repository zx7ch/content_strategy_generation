"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import {
  createBrandChannel,
  updateBrand,
  updateBrandChannel,
  type BrandDetailPageData
} from "@/lib/api";
import type { BrandChannelOption } from "@/lib/types";

interface BrandDetailPanelProps extends BrandDetailPageData {}

const ageRangeOptions = ["18-24", "25-34", "35-44", "45+"] as const;
const stageOptions = [
  { value: "seed", label: "起步期" },
  { value: "growth", label: "成长期" },
  { value: "mature", label: "成熟期" }
] as const;
const genderOptions = [
  { value: "", label: "未指定" },
  { value: "female", label: "女性为主" },
  { value: "male", label: "男性为主" },
  { value: "balanced", label: "泛人群" }
] as const;
const toneOptions = [
  { value: "", label: "未指定" },
  { value: "practical", label: "实用导向" },
  { value: "professional", label: "专业理性" },
  { value: "friendly", label: "亲切自然" },
  { value: "playful", label: "轻松活泼" },
  { value: "premium", label: "高质感" }
] as const;
const primaryGoalOptions = [
  { value: "", label: "未指定" },
  { value: "topic_recommendation", label: "获取选题方向" },
  { value: "engagement_growth", label: "提升互动表现" },
  { value: "lead_conversion", label: "提升转化线索" },
  { value: "brand_awareness", label: "建立品牌认知" }
] as const;

function parseTagInput(value: string): string[] {
  return value
    .split(/[,\n，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function formatTagInput(value: unknown): string {
  return Array.isArray(value)
    ? value
        .filter((item): item is string => typeof item === "string")
        .join("，")
    : "";
}

function formatTimestamp(value?: string) {
  if (!value) {
    return "暂无";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString("zh-CN", { hour12: false });
}

function buildAudienceSummary(targetAudience: Record<string, unknown>) {
  const parts = [
    typeof targetAudience.summary === "string" ? targetAudience.summary.trim() : "",
    Array.isArray(targetAudience.age_ranges) ? targetAudience.age_ranges.join("/") : "",
    targetAudience.gender_skew === "female"
      ? "女性为主"
      : targetAudience.gender_skew === "male"
        ? "男性为主"
        : targetAudience.gender_skew === "balanced"
          ? "泛人群"
          : ""
  ];
  return parts.filter(Boolean).join(" · ") || "待补充";
}

function isValidProfileUrl(value: string) {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

export function BrandDetailPanel({
  brand,
  channels
}: BrandDetailPanelProps) {
  const existingHomepage = channels[0];
  const [brandName, setBrandName] = useState(brand.name);
  const [category, setCategory] = useState(brand.category ?? "");
  const [stage, setStage] = useState(brand.stage);
  const [audienceSummary, setAudienceSummary] = useState(String(brand.targetAudience.summary ?? ""));
  const [ageRanges, setAgeRanges] = useState<string[]>(
    Array.isArray(brand.targetAudience.age_ranges)
      ? brand.targetAudience.age_ranges.filter((item): item is string => typeof item === "string")
      : []
  );
  const [genderSkew, setGenderSkew] = useState(
    typeof brand.targetAudience.gender_skew === "string" ? brand.targetAudience.gender_skew : ""
  );
  const [interestsInput, setInterestsInput] = useState(formatTagInput(brand.targetAudience.interests));
  const [lifestyleInput, setLifestyleInput] = useState(
    formatTagInput(brand.targetAudience.lifestyle_descriptors)
  );
  const [geoInput, setGeoInput] = useState(formatTagInput(brand.targetAudience.geographic_focus));
  const [tone, setTone] = useState(typeof brand.brandExpression.tone === "string" ? brand.brandExpression.tone : "");
  const [keywordsInput, setKeywordsInput] = useState(formatTagInput(brand.brandExpression.keywords));
  const [avoidInput, setAvoidInput] = useState(formatTagInput(brand.brandExpression.avoided_phrases));
  const [expressionNotes, setExpressionNotes] = useState(String(brand.brandExpression.notes ?? ""));
  const [primaryGoal, setPrimaryGoal] = useState(
    typeof brand.businessGoals.primary === "string" ? brand.businessGoals.primary : ""
  );
  const [secondaryGoalsInput, setSecondaryGoalsInput] = useState(formatTagInput(brand.businessGoals.secondary));
  const [metricsInput, setMetricsInput] = useState(formatTagInput(brand.businessGoals.metrics));
  const [goalNotes, setGoalNotes] = useState(String(brand.businessGoals.notes ?? ""));
  const [accountName, setAccountName] = useState(existingHomepage?.accountName ?? "");
  const [profileUrl, setProfileUrl] = useState(existingHomepage?.profileUrl ?? "");
  const [channelState, setChannelState] = useState<BrandChannelOption[]>(channels);
  const [saving, setSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const audienceSummaryText = useMemo(
    () =>
      buildAudienceSummary({
        summary: audienceSummary,
        age_ranges: ageRanges,
        gender_skew: genderSkew
      }),
    [ageRanges, audienceSummary, genderSkew]
  );

  function toggleAgeRange(value: string) {
    setAgeRanges((current) =>
      current.includes(value) ? current.filter((item) => item !== value) : [...current, value]
    );
  }

  async function handleSave() {
    const trimmedBrandName = brandName.trim();
    const trimmedProfileUrl = profileUrl.trim();
    if (!trimmedBrandName) {
      setSaveError("请先填写品牌名称。");
      return;
    }
    if (trimmedProfileUrl && !isValidProfileUrl(trimmedProfileUrl)) {
      setSaveError("品牌主页链接格式不正确，请填写完整的网址。");
      return;
    }

    setSaving(true);
    setSaveError(null);
    setSaveMessage(null);
    try {
      await updateBrand(brand.id, {
        name: trimmedBrandName,
        category: category.trim() || undefined,
        stage,
        targetAudience: {
          summary: audienceSummary.trim(),
          age_ranges: ageRanges,
          gender_skew: genderSkew || null,
          interests: parseTagInput(interestsInput),
          lifestyle_descriptors: parseTagInput(lifestyleInput),
          geographic_focus: parseTagInput(geoInput)
        },
        brandExpression: {
          tone: tone || null,
          keywords: parseTagInput(keywordsInput),
          avoided_phrases: parseTagInput(avoidInput),
          notes: expressionNotes.trim()
        },
        businessGoals: {
          primary: primaryGoal || null,
          secondary: parseTagInput(secondaryGoalsInput),
          metrics: parseTagInput(metricsInput),
          notes: goalNotes.trim()
        }
      });

      let nextChannels = channelState;
      if (accountName.trim() || trimmedProfileUrl) {
        const payload = {
          platform: "xiaohongshu",
          accountName: accountName.trim() || undefined,
          profileUrl: trimmedProfileUrl || undefined
        };
        const savedChannel = existingHomepage
          ? await updateBrandChannel(brand.id, existingHomepage.id, payload)
          : await createBrandChannel(brand.id, payload);
        nextChannels = existingHomepage
          ? channelState.map((channel) => (channel.id === savedChannel.id ? savedChannel : channel))
          : [savedChannel, ...channelState];
        setChannelState(nextChannels);
      }

      setSaveMessage("品牌配置已更新。");
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "保存失败，请稍后重试。");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-4 rounded-panel border border-white/70 bg-white/85 p-6 shadow-panel backdrop-blur sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm text-quiet">品牌配置</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-ink">{brandName || brand.name}</h1>
          <p className="mt-2 text-sm text-quiet">维护品牌基础信息、目标受众、表达风格和业务目标。</p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Link
            href="/data-sources"
            className="inline-flex items-center justify-center rounded-full border border-line bg-white px-4 py-2 text-sm font-medium text-ink transition hover:bg-slate-50"
          >
            前往数据源
          </Link>
          <Button variant="primary" onClick={handleSave} disabled={saving}>
            {saving ? "保存中..." : "保存修改"}
          </Button>
        </div>
      </section>

      {saveError ? (
        <Card>
          <p className="text-sm text-rose-600">{saveError}</p>
        </Card>
      ) : null}
      {saveMessage ? (
        <Card>
          <p className="text-sm text-emerald-700">{saveMessage}</p>
        </Card>
      ) : null}

      <section className="grid gap-4 lg:grid-cols-2">
        <Card className="space-y-4">
          <div>
            <h2 className="text-lg font-semibold text-ink">品牌概览</h2>
            <p className="mt-1 text-sm text-quiet">这里维护品牌名称、赛道和当前阶段。</p>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="space-y-2 text-sm text-ink">
              <span>品牌名称</span>
              <input
                className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
                value={brandName}
                onChange={(event) => setBrandName(event.target.value)}
              />
            </label>
            <label className="space-y-2 text-sm text-ink">
              <span>所属赛道</span>
              <input
                className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
                value={category}
                onChange={(event) => setCategory(event.target.value)}
                placeholder="例如：户外 / 美妆 / 母婴"
              />
            </label>
            <label className="space-y-2 text-sm text-ink">
              <span>当前阶段</span>
              <select
                className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
                value={stage}
                onChange={(event) => setStage(event.target.value as typeof stage)}
              >
                {stageOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <div className="space-y-2 text-sm text-ink">
              <span className="block text-quiet">最近更新时间</span>
              <div className="rounded-2xl border border-line bg-slate-50 px-3 py-2 text-sm text-slate-700">
                {formatTimestamp(brand.updatedAt)}
              </div>
            </div>
          </div>
        </Card>

        <Card className="space-y-4">
          <div>
            <h2 className="text-lg font-semibold text-ink">品牌主页</h2>
            <p className="mt-1 text-sm text-quiet">配置品牌的小红书主页信息，后续数据采集会直接使用这里的信息。</p>
          </div>
          <div className="grid gap-4">
            <label className="space-y-2 text-sm text-ink">
              <span>账号名称</span>
              <input
                className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
                value={accountName}
                onChange={(event) => setAccountName(event.target.value)}
                placeholder="例如：轻量户外官方号"
              />
            </label>
            <label className="space-y-2 text-sm text-ink">
              <span>品牌主页链接</span>
              <input
                className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
                value={profileUrl}
                onChange={(event) => setProfileUrl(event.target.value)}
                placeholder="https://www.xiaohongshu.com/user/profile/..."
              />
            </label>
            <div className="rounded-2xl border border-line bg-slate-50 px-4 py-3 text-sm text-slate-700">
              当前主页状态：
              {" "}
              {channelState[0]?.profileUrl ? "已配置" : "未配置"}
            </div>
            {profileUrl.trim() && isValidProfileUrl(profileUrl.trim()) ? (
              <div>
                <a
                  href={profileUrl.trim()}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center justify-center rounded-full border border-line bg-white px-4 py-2 text-sm font-medium text-ink transition hover:bg-slate-50"
                >
                  打开主页
                </a>
              </div>
            ) : null}
          </div>
        </Card>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <Card className="space-y-4">
          <div>
            <h2 className="text-lg font-semibold text-ink">目标受众</h2>
            <p className="mt-1 text-sm text-quiet">明确品牌主要面向的人群，后续选题生成会参考这里的信息。</p>
          </div>
          <div className="space-y-4">
            <label className="space-y-2 text-sm text-ink">
              <span>人群摘要</span>
              <textarea
                className="min-h-24 w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
                value={audienceSummary}
                onChange={(event) => setAudienceSummary(event.target.value)}
                placeholder="例如：面向一二线城市通勤人群，偏轻户外和周末短途场景。"
              />
            </label>
            <div className="space-y-2 text-sm text-ink">
              <span className="block">年龄段</span>
              <div className="flex flex-wrap gap-2">
                {ageRangeOptions.map((option) => (
                  <button
                    key={option}
                    type="button"
                    className={[
                      "rounded-full border px-3 py-2 text-sm transition",
                      ageRanges.includes(option)
                        ? "border-slate-400 bg-slate-200 text-ink"
                        : "border-line bg-white text-slate-700 hover:bg-slate-50"
                    ].join(" ")}
                    onClick={() => toggleAgeRange(option)}
                  >
                    {option}
                  </button>
                ))}
              </div>
            </div>
            <label className="space-y-2 text-sm text-ink">
              <span>性别倾向</span>
              <select
                className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
                value={genderSkew}
                onChange={(event) => setGenderSkew(event.target.value)}
              >
                {genderOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-2 text-sm text-ink">
              <span>兴趣标签</span>
              <input
                className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
                value={interestsInput}
                onChange={(event) => setInterestsInput(event.target.value)}
                placeholder="例如：通勤，徒步，露营"
              />
            </label>
            <label className="space-y-2 text-sm text-ink">
              <span>生活方式 / 使用场景</span>
              <input
                className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
                value={lifestyleInput}
                onChange={(event) => setLifestyleInput(event.target.value)}
                placeholder="例如：城市通勤，周末短途，轻量出行"
              />
            </label>
            <label className="space-y-2 text-sm text-ink">
              <span>地域重点</span>
              <input
                className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
                value={geoInput}
                onChange={(event) => setGeoInput(event.target.value)}
                placeholder="例如：上海，杭州，深圳"
              />
            </label>
          </div>
        </Card>

        <Card className="space-y-4">
          <div>
            <h2 className="text-lg font-semibold text-ink">当前受众摘要</h2>
            <p className="mt-1 text-sm text-quiet">这是一段面向运营同学的只读总结，方便快速确认配置是否完整。</p>
          </div>
          <div className="rounded-3xl border border-line bg-slate-50 px-5 py-5 text-sm leading-7 text-slate-700">
            {audienceSummaryText}
          </div>
        </Card>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <Card className="space-y-4">
          <div>
            <h2 className="text-lg font-semibold text-ink">品牌表达</h2>
            <p className="mt-1 text-sm text-quiet">定义品牌说话方式与表达边界，帮助后续内容输出更统一。</p>
          </div>
          <div className="space-y-4">
            <label className="space-y-2 text-sm text-ink">
              <span>品牌语气</span>
              <select
                className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
                value={tone}
                onChange={(event) => setTone(event.target.value)}
              >
                {toneOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-2 text-sm text-ink">
              <span>核心关键词</span>
              <input
                className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
                value={keywordsInput}
                onChange={(event) => setKeywordsInput(event.target.value)}
                placeholder="例如：轻量，通勤，耐用"
              />
            </label>
            <label className="space-y-2 text-sm text-ink">
              <span>避免表达</span>
              <input
                className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
                value={avoidInput}
                onChange={(event) => setAvoidInput(event.target.value)}
                placeholder="例如：夸张承诺，廉价感描述"
              />
            </label>
            <label className="space-y-2 text-sm text-ink">
              <span>补充说明</span>
              <textarea
                className="min-h-24 w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
                value={expressionNotes}
                onChange={(event) => setExpressionNotes(event.target.value)}
                placeholder="补充品牌表达风格、禁忌和统一写法。"
              />
            </label>
          </div>
        </Card>

        <Card className="space-y-4">
          <div>
            <h2 className="text-lg font-semibold text-ink">业务目标</h2>
            <p className="mt-1 text-sm text-quiet">明确当下最重要的业务目标和关注指标，后续决策会更贴近实际诉求。</p>
          </div>
          <div className="space-y-4">
            <label className="space-y-2 text-sm text-ink">
              <span>当前核心目标</span>
              <select
                className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
                value={primaryGoal}
                onChange={(event) => setPrimaryGoal(event.target.value)}
              >
                {primaryGoalOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-2 text-sm text-ink">
              <span>次级目标</span>
              <input
                className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
                value={secondaryGoalsInput}
                onChange={(event) => setSecondaryGoalsInput(event.target.value)}
                placeholder="例如：扩大覆盖，提升收藏，沉淀品牌认知"
              />
            </label>
            <label className="space-y-2 text-sm text-ink">
              <span>关注指标</span>
              <input
                className="w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
                value={metricsInput}
                onChange={(event) => setMetricsInput(event.target.value)}
                placeholder="例如：互动率，收藏率，进店点击"
              />
            </label>
            <label className="space-y-2 text-sm text-ink">
              <span>补充说明</span>
              <textarea
                className="min-h-24 w-full rounded-2xl border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition focus:border-slate-400"
                value={goalNotes}
                onChange={(event) => setGoalNotes(event.target.value)}
                placeholder="补充当前阶段最关注的业务方向与优先级。"
              />
            </label>
          </div>
        </Card>
      </section>
    </div>
  );
}
