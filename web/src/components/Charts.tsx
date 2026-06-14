import { CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart,
         ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useChartColors } from "../useTheme";

export const MONO_TICK = { fontFamily: '"IBM Plex Mono", monospace', fontSize: 11 };
export const TOOLTIP_STYLE = {
  background: "var(--bg)", border: "1.5px solid var(--rule)", borderRadius: 0,
  fontFamily: '"IBM Plex Mono", monospace', fontSize: 12,
} as const;
export const LEGEND_STYLE = {
  fontSize: 11, textTransform: "uppercase", letterSpacing: ".1em",
} as const;

export function TrendChart({ data }:
    { data: { month: string; income: number; expenses: number }[] }) {
  const colors = useChartColors();
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke={colors.hairline} />
        <XAxis dataKey="month" stroke={colors.ink} tick={{ ...MONO_TICK, fill: colors.ink }} />
        <YAxis stroke={colors.ink} tick={{ ...MONO_TICK, fill: colors.ink }}
               tickFormatter={(v: number) => `$${v}`} />
        <Tooltip contentStyle={TOOLTIP_STYLE} />
        <Legend wrapperStyle={LEGEND_STYLE} />
        <Line type="monotone" dataKey="income" stroke={colors.green} strokeWidth={2}
              dot={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="expenses" stroke={colors.accent} strokeWidth={2}
              dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function CategoryPie({ data }: { data: Record<string, number> }) {
  const colors = useChartColors();
  const rows = Object.entries(data).map(([name, value]) => ({ name, value }));
  if (!rows.length) return <p className="muted">No expenses yet this period.</p>;
  return (
    <ResponsiveContainer width="100%" height={240}>
      <PieChart>
        <Pie data={rows} dataKey="value" nameKey="name" outerRadius={85} label
             isAnimationActive={false}>
          {rows.map((_, i) => <Cell key={i} fill={colors.palette[i % colors.palette.length]} />)}
        </Pie>
        <Tooltip contentStyle={TOOLTIP_STYLE} />
        <Legend wrapperStyle={LEGEND_STYLE} />
      </PieChart>
    </ResponsiveContainer>
  );
}
