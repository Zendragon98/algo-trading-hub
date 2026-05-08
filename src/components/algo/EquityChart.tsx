import { useMemo } from "react";

export function EquityChart({ data }: { data: number[] }) {
  const { path, area, min, max, last, first } = useMemo(() => {
    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;
    const w = 100;
    const h = 100;
    const step = w / (data.length - 1);
    const pts = data.map((v, i) => {
      const x = i * step;
      const y = h - ((v - min) / range) * h;
      return [x, y] as const;
    });
    const path = pts.map(([x, y], i) => (i === 0 ? `M${x},${y}` : `L${x},${y}`)).join(" ");
    const area = `${path} L${w},${h} L0,${h} Z`;
    return { path, area, min, max, last: data[data.length - 1], first: data[0] };
  }, [data]);

  const up = last >= first;
  const stroke = up ? "var(--bull)" : "var(--bear)";

  return (
    <div className="relative h-full w-full">
      <svg
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        className="h-full w-full"
      >
        <defs>
          <linearGradient id="equityFill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.35" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>
        {[20, 40, 60, 80].map((y) => (
          <line
            key={y}
            x1="0"
            x2="100"
            y1={y}
            y2={y}
            stroke="var(--border)"
            strokeWidth="0.15"
            strokeDasharray="0.6 0.6"
            vectorEffect="non-scaling-stroke"
          />
        ))}
        <path d={area} fill="url(#equityFill)" />
        <path
          d={path}
          fill="none"
          stroke={stroke}
          strokeWidth="1.2"
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      </svg>
      <div className="pointer-events-none absolute right-2 top-2 text-[10px] tabular-nums text-muted-foreground">
        HIGH {max.toFixed(2)}
      </div>
      <div className="pointer-events-none absolute bottom-2 right-2 text-[10px] tabular-nums text-muted-foreground">
        LOW {min.toFixed(2)}
      </div>
    </div>
  );
}