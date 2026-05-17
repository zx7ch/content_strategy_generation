# Development Guide: ALIGN-2 - Server Component API 读取迁移

> Generated: 2026-05-16
> Source: docs/changes/2026-05-16-frontend-scope-v1-v2-alignment.md

## 1. Task Context

### In Scope
- `brands/page.tsx`: 从 async Server Component → Client Component（useEffect + useState）
- `brands/[id]/page.tsx`: 同上，用 `params.id` 传给浏览器侧数据函数
- 保留现有 UI 结构和 live API 错误态

### Out Of Scope
- 修改 server-api.ts 内部逻辑
- 重做 Console 信息架构
- 其他页面的迁移

### Acceptance Criteria
- [ ] AC1: `/brands` 页面是 Client Component，数据通过 `getBrandsPageData()` 在浏览器侧获取
- [ ] AC2: `/brands/[id]` 页面是 Client Component，数据通过 `getBrandDetailPageData(id)` 在浏览器侧获取
- [ ] AC3: runtime 断开时两页面显示统一错误态（`LiveApiErrorState`）
- [ ] AC4: `npm run build` 通过（无 client/server 边界错误）

## 3. Technical Design

### brands/page.tsx
```
"use client"
useState: data (BrandsPageData | null), error (string | null)
useEffect: getBrandsPageData() → setData | setError
render: loading → 正在加载 | error → LiveApiErrorState | data → existing JSX
```

### brands/[id]/page.tsx
```
"use client"
params: { id: string }  (Next.js 14 passes params to Client Components as regular prop)
useState: data (BrandDetailPageData | null), error (string | null)
useEffect: getBrandDetailPageData(params.id) → setData | setError
render: loading → 正在加载 | error → LiveApiErrorState | data → BrandDetailPanel
```

### Error handling
使用 `getRuntimeApiErrorMessage` from api.ts（不使用 getLiveApiErrorMessage from server-api.ts）。
