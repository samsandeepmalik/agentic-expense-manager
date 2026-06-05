import { CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart,
         ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const COLORS = ["#3a8f63", "#c2742c", "#7a9e7e", "#d9a85c", "#a08c6a",
                "#5e8ca7", "#b56a5d", "#8a7ba8"];

export function TrendChart({ data }:
    { data: { month: string; income: number; expenses: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="#efe9de" />
        <XAxis dataKey="month" stroke="#a08c6a" /><YAxis stroke="#a08c6a" />
        <Tooltip /><Legend />
        <Line type="monotone" dataKey="income" stroke="#3a8f63" strokeWidth={2} dot={false} />
        <Line type="monotone" dataKey="expenses" stroke="#c2742c" strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function CategoryPie({ data }: { data: Record<string, number> }) {
  const rows = Object.entries(data).map(([name, value]) => ({ name, value }));
  if (!rows.length) return <p className="muted">No expenses yet this period.</p>;
  return (
    <ResponsiveContainer width="100%" height={240}>
      <PieChart>
        <Pie data={rows} dataKey="value" nameKey="name" outerRadius={85} label>
          {rows.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
        </Pie>
        <Tooltip /><Legend />
      </PieChart>
    </ResponsiveContainer>
  );
}
