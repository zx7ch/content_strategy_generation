import type { ReactNode } from "react";

import { Card } from "@/components/ui/Card";

export interface TableColumn<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  className?: string;
}

interface DataTableProps<T> {
  columns: TableColumn<T>[];
  rows: T[];
  emptyLabel?: string;
}

export function DataTable<T>({ columns, rows, emptyLabel = "暂无数据" }: DataTableProps<T>) {
  return (
    <Card className="overflow-hidden p-0">
      <div className="overflow-x-auto">
        <table className="min-w-full border-collapse text-sm">
          <thead className="bg-slate-50">
            <tr>
              {columns.map((column) => (
                <th
                  key={column.key}
                  className={[
                    "border-b border-line px-4 py-3 text-left text-xs font-medium uppercase tracking-[0.12em] text-quiet",
                    column.className ?? ""
                  ].join(" ")}
                >
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td
                  className="px-4 py-8 text-center text-sm text-quiet"
                  colSpan={columns.length}
                >
                  {emptyLabel}
                </td>
              </tr>
            ) : (
              rows.map((row, index) => (
                <tr key={index} className="transition hover:bg-slate-50/80">
                  {columns.map((column) => (
                    <td
                      key={column.key}
                      className={[
                        "border-b border-line px-4 py-4 align-top text-slate-700 last:pr-6",
                        column.className ?? ""
                      ].join(" ")}
                    >
                      {column.render(row)}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
