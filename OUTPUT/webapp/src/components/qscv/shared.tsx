"use client";
/**
 * Общие вспомогательные элементы QSCV-панелей (только для компонентов этой папки):
 * числовые поля, SVG-диаграмма тайминга B2900, бейджи статусов/флагов, загрузка файлов.
 */
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { formatEng, formatSeconds } from "@/lib/qscv/format";

// ============================== Числовое поле ==============================

export interface NumFieldProps {
  label: string;
  value: number;
  onChange: (v: number) => void;
  step?: string;
  hint?: string;
  className?: string;
  min?: number;
  unit?: string;
}

function numToText(v: number): string {
  if (!Number.isFinite(v)) return "";
  return String(v);
}

/**
 * Поле числового ввода с поддержкой научной записи (1e-3).
 * Вне фокуса отображается значение из store; во время ввода — локальный текст,
 * поэтому промежуточные состояния ("1e-", "-") не «слипаются».
 */
export function NumField({ label, value, onChange, step = "any", hint, className, min }: NumFieldProps) {
  const [text, setText] = useState<string>(() => numToText(value));
  const [focused, setFocused] = useState(false);

  return (
    <div className={cn("space-y-1.5", className)}>
      <Label className="text-xs">{label}</Label>
      <Input
        type="number"
        inputMode="decimal"
        step={step}
        min={min}
        aria-label={label}
        value={focused ? text : numToText(value)}
        onFocus={() => {
          setText(numToText(value));
          setFocused(true);
        }}
        onBlur={() => setFocused(false)}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === "") return; // промежуточное состояние number-input ("1e-", "-")
          const v = parseFloat(raw);
          if (Number.isFinite(v)) {
            setText(raw);
            onChange(v);
          }
        }}
      />
      {hint && <p className="text-[11px] leading-snug text-muted-foreground">{hint}</p>}
    </div>
  );
}

// ============================== SVG-диаграмма тайминга ==============================

export interface TimingDiagramProps {
  interval: number;
  aperture: number;
  tranDelay: number;
  acqDelay: number;
  tranTime: number;
  /** сколько окон рисовать (по умолчанию 5) */
  windows?: number;
  /** config — подписи «период таймера ≠ апертура»; result — аннотации задержки и мёртвого времени */
  mode?: "config" | "result";
  className?: string;
}

function safe(v: number): number {
  return Number.isFinite(v) && v > 0 ? v : 0;
}

/**
 * Упрощённая временная диаграмма триггерной модели B2900 (§5–6):
 * переход источника [tranDelay, tranDelay+tranTime], окна апертур
 * a_j=(j−1)·interval+acqDelay, b_j=a_j+aperture, мёртвое время между окнами.
 */
export function TimingDiagram({
  interval,
  aperture,
  tranDelay,
  acqDelay,
  tranTime,
  windows = 5,
  mode = "config",
  className,
}: TimingDiagramProps) {
  const iv = safe(interval);
  const ap = safe(aperture);
  const td = safe(tranDelay);
  const ad = safe(acqDelay);
  const tt = safe(tranTime);
  const n = Math.max(1, Math.min(12, windows));

  if (iv <= 0) {
    return (
      <div className={cn("rounded-md border bg-muted/40 px-3 py-6 text-center text-xs text-muted-foreground", className)}>
        Период таймера должен быть положительным — диаграмма недоступна
      </div>
    );
  }

  const tEnd = Math.max(n * iv + ad + ap, td + tt + iv * 0.2) * 1.04;
  const W = 800;
  const H = 168;
  const x0 = 46;
  const x1 = W - 12;
  const x = (t: number) => x0 + (t / tEnd) * (x1 - x0);

  // каналы
  const vTop = 26;
  const vBase = 56; // уровень V_prev
  const vHigh = 14; // уровень V_next
  const aTop = 84;
  const aBot = 116;
  const axisY = 132;

  const aps: { a: number; b: number; j: number }[] = [];
  for (let j = 1; j <= n; j++) {
    const a = (j - 1) * iv + ad;
    aps.push({ a, b: a + ap, j });
  }

  const firstA = aps[0].a;
  const lastB = aps[aps.length - 1].b;
  const dead = Math.max(0, iv - ap);

  return (
    <svg
      role="img"
      aria-label="Временная диаграмма: переход источника и окна апертур"
      viewBox={`0 0 ${W} ${H}`}
      className={cn("w-full", className)}
      style={{ height: 168 }}
    >
      {/* названия каналов */}
      <text x={8} y={vBase - 12} fontSize={11} fill="currentColor" className="text-muted-foreground">
        V(t)
      </text>
      <text x={8} y={aTop + 18} fontSize={11} fill="currentColor" className="text-muted-foreground">
        I(t)
      </text>

      {/* полоса перехода источника */}
      {tt > 0 && (
        <rect x={x(td)} y={vHigh - 2} width={Math.max(1, x(td + tt) - x(td))} height={aBot - vHigh + 4} fill="#a1a1aa" opacity={0.15} />
      )}

      {/* напряжение: плато V_prev → фронт → плато V_next */}
      <polyline
        points={`${x(0)},${vBase} ${x(td)},${vBase} ${x(td + tt)},${vHigh} ${x(tEnd)},${vHigh}`}
        fill="none"
        stroke="#71717a"
        strokeWidth={2}
      />
      <text x={x(0) + 4} y={vBase + 11} fontSize={10} fill="#71717a">
        V_до
      </text>
      <text x={x(td + tt) + 4} y={vHigh + 10} fontSize={10} fill="#71717a">
        V_после
      </text>

      {/* задержка до первой апертуры (начальный пропуск) */}
      {firstA > 0 && (
        <rect x={x(0)} y={aTop} width={Math.max(0, x(firstA) - x(0))} height={aBot - aTop} fill="#f59e0b" opacity={0.22} />
      )}
      {/* мёртвое время между окнами */}
      {aps.slice(0, -1).map((w, idx) => (
        <rect
          key={`gap-${idx}`}
          x={x(w.b)}
          y={aTop}
          width={Math.max(0, x(aps[idx + 1].a) - x(w.b))}
          height={aBot - aTop}
          fill="#f59e0b"
          opacity={0.3}
        />
      ))}

      {/* окна апертур */}
      {aps.map((w) => (
        <rect
          key={`ap-${w.j}`}
          x={x(w.a)}
          y={aTop}
          width={Math.max(1.2, x(w.b) - x(w.a))}
          height={aBot - aTop}
          fill="#10b981"
          opacity={0.75}
          rx={1.5}
        />
      ))}

      {/* ось времени */}
      <line x1={x(0)} y1={axisY} x2={x(tEnd)} y2={axisY} stroke="#a1a1aa" strokeWidth={1} />
      <line x1={x(0)} y1={axisY - 4} x2={x(0)} y2={axisY + 4} stroke="#a1a1aa" strokeWidth={1} />
      <line x1={x(lastB)} y1={axisY - 4} x2={x(lastB)} y2={axisY + 4} stroke="#a1a1aa" strokeWidth={1} />
      <text x={x(0)} y={axisY + 15} fontSize={10} fill="currentColor" className="text-muted-foreground">
        0
      </text>
      <text x={x(lastB)} y={axisY + 15} fontSize={10} textAnchor="end" fill="currentColor" className="text-muted-foreground">
        {formatSeconds(lastB, 2)}
      </text>
      <text x={x1} y={axisY + 15} fontSize={10} textAnchor="end" fill="currentColor" className="text-muted-foreground">
        t, с
      </text>

      {mode === "config" ? (
        <>
          {/* скобка «период таймера» */}
          <line x1={x(aps[0].a)} y1={aBot + 7} x2={x(aps[1]?.a ?? aps[0].a + iv)} y2={aBot + 7} stroke="#a1a1aa" strokeWidth={1} />
          <line x1={x(aps[0].a)} y1={aBot + 4} x2={x(aps[0].a)} y2={aBot + 10} stroke="#a1a1aa" strokeWidth={1} />
          <line x1={x(aps[1]?.a ?? aps[0].a + iv)} y1={aBot + 4} x2={x(aps[1]?.a ?? aps[0].a + iv)} y2={aBot + 10} stroke="#a1a1aa" strokeWidth={1} />
          <text
            x={(x(aps[0].a) + x(aps[1]?.a ?? aps[0].a + iv)) / 2}
            y={aBot + 19}
            fontSize={11}
            textAnchor="middle"
            fill="currentColor"
            className="text-muted-foreground"
          >
            период таймера ≠ апертура (§6)
          </text>
          {/* скобка «апертура» */}
          <line x1={x(aps[0].a)} y1={aTop - 6} x2={x(aps[0].b)} y2={aTop - 6} stroke="#059669" strokeWidth={1} />
          <line x1={x(aps[0].a)} y1={aTop - 9} x2={x(aps[0].a)} y2={aTop - 3} stroke="#059669" strokeWidth={1} />
          <line x1={x(aps[0].b)} y1={aTop - 9} x2={x(aps[0].b)} y2={aTop - 3} stroke="#059669" strokeWidth={1} />
          <text x={(x(aps[0].a) + x(aps[0].b)) / 2} y={aTop - 11} fontSize={11} textAnchor="middle" fill="#059669">
            апертура
          </text>
          {dead > 0 && (
            <text x={(x(aps[0].b) + x(aps[1]?.a ?? aps[0].a + iv)) / 2} y={(aTop + aBot) / 2 + 3} fontSize={10} textAnchor="middle" fill="#b45309">
              мёртвое
            </text>
          )}
        </>
      ) : (
        <>
          {firstA > 0 && (
            <>
              <line x1={x(0)} y1={aTop - 6} x2={x(firstA)} y2={aTop - 6} stroke="#b45309" strokeWidth={1} />
              <line x1={x(0)} y1={aTop - 9} x2={x(0)} y2={aTop - 3} stroke="#b45309" strokeWidth={1} />
              <line x1={x(firstA)} y1={aTop - 9} x2={x(firstA)} y2={aTop - 3} stroke="#b45309" strokeWidth={1} />
              <text x={(x(0) + x(firstA)) / 2} y={aTop - 11} fontSize={11} textAnchor="middle" fill="#b45309">
                задержка до первой апертуры
              </text>
            </>
          )}
          {dead > 0 && (
            <text x={(x(aps[0].b) + x(aps[1]?.a ?? aps[0].b + dead)) / 2} y={aBot + 19} fontSize={11} textAnchor="middle" fill="#b45309">
              мёртвое время = интервал − апертура
            </text>
          )}
        </>
      )}
    </svg>
  );
}

// ============================== Бейджи ==============================

const FLAG_SEVERITY: Record<string, "red" | "amber"> = {
  COMPLIANCE_EXCEEDED: "red",
  RANGE_OVERLOAD: "red",
  TAIL_NOT_SETTLED: "amber",
  GAPS_UNACCOUNTED: "amber",
  GAPS_BOUNDED_NOT_CORRECTED: "amber",
  MODEL_DEPENDENT_GAP_CORRECTION: "amber",
  FIT_DIAGNOSTIC_WARNING: "amber",
  MISSED_START: "amber",
  INITIAL_GAP_PRESENT: "amber",
  PRECONDITION_NOT_CONFIRMED: "amber",
  FIRST_STEP_DIAGNOSTIC: "amber",
};

export function FlagBadge({ flag }: { flag: string }) {
  const sev = FLAG_SEVERITY[flag] ?? "amber";
  return (
    <Badge
      variant="outline"
      className={cn(
        "font-mono text-[10px]",
        sev === "red" ? "border-red-500/60 text-red-600 dark:text-red-400" : "border-amber-500/60 text-amber-700 dark:text-amber-400",
      )}
    >
      {flag}
    </Badge>
  );
}

export function OkBadge({ ok, okText = "да", failText = "нет" }: { ok: boolean; okText?: string; failText?: string }) {
  return (
    <Badge
      variant="outline"
      className={cn(
        ok ? "border-emerald-500/60 text-emerald-700 dark:text-emerald-400" : "border-red-500/60 text-red-600 dark:text-red-400",
      )}
    >
      {ok ? okText : failText}
    </Badge>
  );
}

export function InfoBadge({ children }: { children: React.ReactNode }) {
  return <Badge variant="outline" className="border-muted-foreground/40 text-muted-foreground">{children}</Badge>;
}

export const STOP_REASON_RU: Record<string, string> = {
  BREAKDOWN_OR_LEAKAGE_GROWTH: "пробой / рост утечки",
  OVERLOAD: "перегрузка диапазона измерения",
  COMPLIANCE: "недопустимый compliance",
  INSTABILITY: "неустойчивость",
  TIMING_LOSS: "потеря временной привязки",
  TAIL_NOT_SETTLED: "хвост не прошёл допуск (stopOnTailFail)",
};

export function stopReasonText(reason: string | null): string {
  if (!reason) return "";
  return STOP_REASON_RU[reason] ?? reason;
}

export function directionLabel(d: string): string {
  return d === "forward" ? "прямой ход" : d === "reverse" ? "обратный ход" : d;
}

export function protocolLabel(p: string): string {
  return p === "aperture_charge"
    ? "Протокол B — зарядовый (апертуры)"
    : "Протокол A — линейная развёртка (Кун)";
}

// ============================== Файлы и буфер ==============================

export function downloadText(content: string, filename: string, mime: string, bom = false): void {
  const blob = new Blob([bom ? "\uFEFF" + content : content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

// ============================== Числа ==============================

/** Апертура по speedMode / apertureMode (§5, User's Guide B2900B/BL). */
export function effectiveAperture(cfg: {
  speedMode: string;
  apertureMode: string;
  nplc: number;
  aperture_s: number;
  lineFreq_Hz: number;
}): { seconds: number; nplc: number; note: string } {
  const f = cfg.lineFreq_Hz > 0 ? cfg.lineFreq_Hz : 50;
  const mode = cfg.speedMode;
  if (mode === "auto")
    return { seconds: 1 / f, nplc: 1, note: "AUTO: 1 PLC на диапазонах 10/100 нА, иначе 0.01 PLC (зависит от диапазона)" };
  if (mode === "short") return { seconds: 0.01 / f, nplc: 0.01, note: "SHORT: 0.01 PLC" };
  if (mode === "medium") return { seconds: 0.1 / f, nplc: 0.1, note: "MEDIUM: 0.1 PLC" };
  if (mode === "normal") return { seconds: 1 / f, nplc: 1, note: "NORMAL: 1 PLC" };
  if (mode === "long") return { seconds: 10 / f, nplc: 10, note: "LONG: 10 PLC" };
  // manual
  if (cfg.apertureMode === "nplc") {
    const n = cfg.nplc > 0 ? cfg.nplc : 0;
    return { seconds: n / f, nplc: n, note: `MANUAL/NPLC: ${n} PLC при ${f} Гц` };
  }
  const s = cfg.aperture_s > 0 ? cfg.aperture_s : 0;
  return { seconds: s, nplc: s * f, note: `MANUAL/секунды: ${formatEng(s, "с")} ≈ ${(s * f).toFixed(2)} PLC` };
}

/** Среднее значение экспоненты по окну [a,b] (qscv_final): (τ/(b−a))·(e^(−a/τ)−e^(−b/τ)). */
export function meanExp(a: number, b: number, tau: number): number {
  if (!(tau > 0) || !(b > a)) return Math.exp(-a / tau);
  return (tau / (b - a)) * (Math.exp(-a / tau) - Math.exp(-b / tau));
}

/** Заголовок панели: название + §-ссылка на методику. */
export function PanelHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="mb-4">
      <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
      <p className="mt-0.5 text-sm text-muted-foreground">{subtitle}</p>
    </div>
  );
}

/**
 * Надёжный скроллящийся контейнер с ограничением высоты.
 * (Radix ScrollArea с max-h не ограничивает viewport — контент «вытекает»
 * за пределы контейнера; здесь обычный overflow-y-auto + стилизованный скроллбар.)
 */
export function ScrollBox({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return <div className={`qscv-scroll overflow-y-auto rounded-md border ${className ?? ""}`}>{children}</div>;
}
