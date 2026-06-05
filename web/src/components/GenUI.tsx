// Generative UI renderer (A2UI-style): the backend agent emits a declarative
// component spec; this maps it to charts/tables/metrics. No business logic.

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { UiComponentSpec, UiSpec } from "../api";

const PALETTE = [
  "#6c8cff", "#5fd4a2", "#ffb35c", "#ff7a8a", "#b48cff",
  "#5cd0e0", "#ffd75c", "#8aa0b8",
];

function MetricCard({ spec }: { spec: UiComponentSpec }) {
  return (
    <div className="metric-card">
      <div className="metric-label">{spec.label ?? spec.title ?? ""}</div>
      <div className="metric-value">
        {spec.unit === "$" ? "$" : ""}
        {typeof spec.value === "number"
          ? spec.value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
          : spec.value}
        {spec.unit && spec.unit !== "$" ? ` ${spec.unit}` : ""}
      </div>
    </div>
  );
}

function ChartBlock({ spec }: { spec: UiComponentSpec }) {
  const data = spec.data ?? [];
  const xKey = spec.xKey ?? "name";
  const series = spec.series ?? [];

  if (spec.type === "pieChart") {
    const valueKey = series[0] ?? "value";
    return (
      <ResponsiveContainer width="100%" height={260}>
        <PieChart>
          <Pie data={data} dataKey={valueKey} nameKey={xKey} outerRadius={95} label>
            {data.map((_, index) => (
              <Cell key={index} fill={PALETTE[index % PALETTE.length]} />
            ))}
          </Pie>
          <Tooltip />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    );
  }

  if (spec.type === "lineChart") {
    return (
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#2c3344" />
          <XAxis dataKey={xKey} stroke="#8aa0b8" />
          <YAxis stroke="#8aa0b8" />
          <Tooltip />
          <Legend />
          {series.map((key, index) => (
            <Line
              key={key}
              type="monotone"
              dataKey={key}
              stroke={PALETTE[index % PALETTE.length]}
              strokeWidth={2}
              dot={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="#2c3344" />
        <XAxis dataKey={xKey} stroke="#8aa0b8" />
        <YAxis stroke="#8aa0b8" />
        <Tooltip />
        <Legend />
        {series.map((key, index) => (
          <Bar key={key} dataKey={key} fill={PALETTE[index % PALETTE.length]} radius={[4, 4, 0, 0]} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

function TableBlock({ spec }: { spec: UiComponentSpec }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {(spec.columns ?? []).map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {(spec.rows ?? []).map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, cellIndex) => (
                <td key={cellIndex}>{String(cell ?? "")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function GenUI({ spec }: { spec: UiSpec }) {
  const metrics = spec.components.filter((component) => component.type === "metric");
  const rest = spec.components.filter((component) => component.type !== "metric");

  return (
    <div className="genui">
      {spec.title && <div className="genui-title">{spec.title}</div>}
      {metrics.length > 0 && (
        <div className="metric-row">
          {metrics.map((component, index) => (
            <MetricCard key={index} spec={component} />
          ))}
        </div>
      )}
      {rest.map((component, index) => (
        <div key={index} className="genui-block">
          {component.title && <div className="block-title">{component.title}</div>}
          {component.type === "table" ? (
            <TableBlock spec={component} />
          ) : (
            <ChartBlock spec={component} />
          )}
        </div>
      ))}
    </div>
  );
}
