import { Card } from "@/components/ui/Card";

interface StatCardProps {
  value: string;
  label: string;
}

export function StatCard({ value, label }: StatCardProps) {
  return (
    <Card className="min-h-[108px]">
      <div className="text-3xl font-semibold tracking-tight text-ink">{value}</div>
      <div className="mt-2 text-sm text-quiet">{label}</div>
    </Card>
  );
}
