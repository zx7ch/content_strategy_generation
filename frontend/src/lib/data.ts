import type { NavigationItem } from "./types";

export const workspaceName = "Content Growth Console";

export const navigationItems: NavigationItem[] = [
  { label: "创作台", href: "/creator", icon: "C" },
  { label: "品牌", href: "/brands", icon: "B" },
  { label: "数据源", href: "/data-sources", icon: "S" },
  { label: "数据处理", href: "/data-processing", icon: "D" },
  { label: "选题库", href: "/topic-pool", icon: "T" },
  { label: "决策", href: "/decisions", icon: "R" },
  { label: "发布", href: "/publish", icon: "P" },
  { label: "绩效", href: "/performance", icon: "M" },
  { label: "评估", href: "/evaluation", icon: "E" },
];
