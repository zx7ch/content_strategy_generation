import type {
  Brand,
  DecisionItem,
  EvaluationSlice,
  NavigationItem,
  PerformanceMetric,
  PublishRecord,
  Topic
} from "@/lib/types";

export const workspaceName = "品牌增长实验室";

export const navigationItems: NavigationItem[] = [
  { label: "品牌管理", href: "/brands", icon: "B" },
  { label: "数据源", href: "/data-sources", icon: "S" },
  { label: "数据处理", href: "/data-processing", icon: "H" },
  { label: "选题库", href: "/topic-pool", icon: "T" },
  { label: "决策运行", href: "/decisions", icon: "D" },
  { label: "发布记录", href: "/publish", icon: "P" },
  { label: "绩效反馈", href: "/performance", icon: "R" },
  { label: "离线评估", href: "/evaluation", icon: "V" }
];

const brands: Brand[] = [
  {
    id: "brand-outdoor",
    name: "轻量户外",
    stage: "Growth",
    targetAudience: "25-34岁 女性",
    status: "Active",
    accounts: 4
  },
  {
    id: "brand-skincare",
    name: "温和修护",
    stage: "Seed",
    targetAudience: "22-30岁 敏感肌用户",
    status: "Active",
    accounts: 5
  },
  {
    id: "brand-office",
    name: "办公室微运动",
    stage: "Mature",
    targetAudience: "24-35岁 白领",
    status: "Active",
    accounts: 3
  }
];

const topics: Topic[] = [
  {
    id: "topic-1",
    title: "通勤场景下的轻量装备选择",
    type: "Scenario",
    hypothesis: "痛点：通勤与户外切换不便",
    score: 0.88,
    source: "Engagement",
    angle: "从通勤和户外双场景切入，强调切换成本与轻量体验。",
    evidenceCount: 3
  },
  {
    id: "topic-2",
    title: "敏感肌换季护肤攻略",
    type: "Problem",
    hypothesis: "缺口：缺乏温和配方推荐",
    score: 0.76,
    source: "Gap",
    angle: "从换季痛点和成分选择误区切入，强调低负担方案。",
    evidenceCount: 2
  },
  {
    id: "topic-3",
    title: "办公室瑜伽 10分钟",
    type: "Trend",
    hypothesis: "机会：午休碎片时间的轻运动内容更易完播",
    score: 0.69,
    source: "Trend",
    angle: "结合碎片化办公场景和近期上升趋势切入。",
    evidenceCount: 2
  }
];

const decisionItems: DecisionItem[] = [
  {
    slotIndex: 0,
    topicId: "topic-4",
    title: "轻量徒步装备实测",
    angle: "从实测场景切入，强调不同路况下的切换体验。",
    hypothesis: "真实场景验证更容易带动收藏与评论。",
    topicType: "Scenario",
    strategyScore: 0.91,
    mode: "Exploitation",
    expectedReward: 0.45,
    reviewStatus: "pending",
    actionLabel: "发布"
  },
  {
    slotIndex: 1,
    topicId: "topic-3",
    title: "办公室瑜伽 10分钟",
    angle: "主打碎片时间与低门槛动作组合。",
    hypothesis: "午休场景内容更容易获得完播。",
    topicType: "Trend",
    strategyScore: 0.65,
    mode: "Exploration",
    expectedReward: 0.27,
    reviewStatus: "pending",
    actionLabel: "预览"
  },
  {
    slotIndex: 2,
    topicId: "topic-5",
    title: "学生党入门徒步装备清单",
    angle: "以预算友好和避坑建议为主线。",
    hypothesis: "新手导向选题有机会扩大覆盖。",
    topicType: "Problem",
    strategyScore: 0.59,
    mode: "Exploration",
    expectedReward: 0.19,
    reviewStatus: "edit_and_accept",
    actionLabel: "编辑接受"
  }
];

const publishRecords: PublishRecord[] = [
  {
    id: "publish-1",
    title: "周末露营好物分享",
    channel: "小红书-官方号",
    publishedAt: "2026-04-10 09:30",
    decisionSource: "Batch #20260410A",
    status: "Published"
  }
];

const performanceMetrics: PerformanceMetric[] = [
  {
    topicTitle: "轻量徒步装备",
    impressions: 12000,
    clicks: 850,
    conversionProxyLabel: "8.0% (加购)",
    engagementRate: 0.045,
    rewardScore: 0.365,
    publishTime: "2026-04-17 09:30"
  }
];

const evaluationSlices: EvaluationSlice[] = [
  {
    slice: "受众: 学生群体",
    issue: "探索率过低导致样本不足",
    action: "增加 min_prob 配额"
  }
];

export function getBrands(): Brand[] {
  return brands.map((item) => ({ ...item }));
}

export function getTopics(): Topic[] {
  return topics.map((item) => ({ ...item }));
}

export function getDecisionItems(): DecisionItem[] {
  return decisionItems.map((item) => ({ ...item }));
}

export function getPublishRecords(): PublishRecord[] {
  return publishRecords.map((item) => ({ ...item }));
}

export function getPerformanceMetrics(): PerformanceMetric[] {
  return performanceMetrics.map((item) => ({ ...item }));
}

export function getEvaluationSlices(): EvaluationSlice[] {
  return evaluationSlices.map((item) => ({ ...item }));
}

export function getWorkspaceStats() {
  const activeBrands = brands.filter((brand) => brand.status === "Active").length;
  const connectedAccounts = brands.reduce((sum, brand) => sum + brand.accounts, 0);
  return { activeBrands, connectedAccounts };
}

export function getTopicPoolStats() {
  const bestScore = Math.max(...topics.map((topic) => topic.score));
  return { totalCandidates: 142, bestScore, lastRefreshAt: "2026-04-12T10:30:00+08:00" };
}

export function getDecisionStats() {
  return {
    expectedReward: 0.45,
    selectedCount: 3,
    explorationProbability: 0.08
  };
}

export function getPerformanceSummary() {
  return {
    averageEngagementRate: 0.045,
    compositeReward168h: 0.365
  };
}

export function getEvaluationSummary() {
  return {
    comparisonLabel: "Baseline vs Thompson Sampling",
    sampleSize: 1200,
    coverage: 0.98,
    essRatio: 0.85,
    uplift: 0.124,
    note: "主要收益来自 Problem 类型的选题优化"
  };
}
