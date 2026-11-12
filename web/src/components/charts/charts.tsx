import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContent } from "@/lib/chart-tooltip";
import { rechartsTooltip } from "./ChartTooltip";

const AXIS = { fontSize: 12, fill: "#5d6679" };
const ACCENT = "#2f5bd3";

type Base<T> = {
  data: T[];
  x: keyof T & string;
  y: keyof T & string;
  xLabel: (v: string) => string;
  yFormat?: (v: number) => string;
  tooltip: (d: T) => TooltipContent;
  label: string;
  height?: number;
};

/** One-series vertical bars with a custom hover card. */
export function Bars<T extends object>({
  data,
  x,
  y,
  xLabel,
  yFormat,
  tooltip,
  label,
  height = 220,
  color = ACCENT,
  colorOf,
}: Base<T> & { color?: string; colorOf?: (d: T) => string }) {
  return (
    <div role="img" aria-label={label} style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 4, left: -16, bottom: 0 }} barCategoryGap="22%">
          <CartesianGrid vertical={false} stroke="#eceef2" />
          <XAxis
            dataKey={x as string}
            tickFormatter={(v) => xLabel(String(v))}
            tick={AXIS}
            tickLine={false}
            axisLine={{ stroke: "#d0d5dd" }}
            interval="preserveStartEnd"
            minTickGap={16}
          />
          <YAxis
            tick={AXIS}
            tickLine={false}
            axisLine={false}
            allowDecimals={false}
            tickFormatter={(v) => (yFormat ? yFormat(Number(v)) : String(v))}
            width={48}
          />
          <Tooltip
            cursor={{ fill: "rgb(47 91 211 / 0.07)" }}
            content={rechartsTooltip(tooltip)}
            isAnimationActive={false}
            wrapperStyle={{ outline: "none", zIndex: 20 }}
          />
          <Bar
            dataKey={y as string}
            radius={[4, 4, 0, 0]}
            fill={color}
            maxBarSize={36}
            isAnimationActive={false}
          >
            {colorOf ? data.map((d, i) => <Cell key={i} fill={colorOf(d)} />) : null}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Area line for a daily series. */
export function AreaLine<T extends object>({
  data,
  x,
  y,
  xLabel,
  yFormat,
  tooltip,
  label,
  height = 220,
}: Base<T>) {
  return (
    <div role="img" aria-label={label} style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
          <defs>
            <linearGradient id="area-fill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={ACCENT} stopOpacity={0.22} />
              <stop offset="100%" stopColor={ACCENT} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} stroke="#eceef2" />
          <XAxis
            dataKey={x as string}
            tickFormatter={(v) => xLabel(String(v))}
            tick={AXIS}
            tickLine={false}
            axisLine={{ stroke: "#d0d5dd" }}
            interval="preserveStartEnd"
            minTickGap={16}
          />
          <YAxis
            tick={AXIS}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => (yFormat ? yFormat(Number(v)) : String(v))}
            width={60}
          />
          <Tooltip
            cursor={{ stroke: ACCENT, strokeOpacity: 0.35, strokeDasharray: "3 3" }}
            content={rechartsTooltip(tooltip)}
            isAnimationActive={false}
            wrapperStyle={{ outline: "none", zIndex: 20 }}
          />
          <Area
            type="monotone"
            dataKey={y as string}
            stroke={ACCENT}
            strokeWidth={2}
            fill="url(#area-fill)"
            activeDot={{ r: 4, strokeWidth: 2, stroke: "#fff", fill: ACCENT }}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
