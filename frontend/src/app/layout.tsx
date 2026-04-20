import type { ReactNode } from "react";
import type { Metadata } from "next";

import { BrandProvider } from "@/components/providers/BrandProvider";
import { WorkspaceProvider } from "@/components/providers/WorkspaceProvider";
import { ConsoleLayout } from "@/components/layout/ConsoleLayout";

import "./globals.css";

export const metadata: Metadata = {
  title: "XHS Growth Agent V2",
  description: "Workspace-scoped growth console for Xiaohongshu brands"
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="text-ink antialiased">
        <WorkspaceProvider>
          <BrandProvider>
            <ConsoleLayout>{children}</ConsoleLayout>
          </BrandProvider>
        </WorkspaceProvider>
      </body>
    </html>
  );
}
