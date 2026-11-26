"use client";
/**
 * Панель «Результаты» — методика §8–12.
 * C–V секущих, таблица шагов, деталь шага (апертуры, флаги, фит, истина, энергия,
 * неопределённость) и карта сходимости заряда.
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Download, FolderOpen, Loader2, MousePointerClick } from "lucide-react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  ErrorBar,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useQscvStore } from "@/lib/store";
import { api } from "@/lib/qscv/api-client";
import { FLAG_DESCRIPTIONS, type StepResult } from "@/lib/qscv/types";
import { formatAmps, formatCoulombs, formatFarads, formatJoules, formatSci, formatSeconds, formatVolts } from "@/lib/qscv/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { FlagBadge, OkBadge, PanelHeader, directionLabel, meanExp, stopReasonText, ScrollBox } from "./shared";

const EMERALD = "#10b981";
const ORANGE = "#f97316";
const GRAY = "#71717a";
const AMBER = "#d97706";

function runLabel(r: { direction: string; cycle: number }): string {
  return `${r.direction === "forward" ? "Прямой ход" : "Обратный ход"}, цикл ${r.cycle}`;
}

function msTick(v: number): string {
  if (!Number.isFinite(v)) return "";
  return Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2);
}

export default function ResultsPanel() {
  const runs = useQscvStore((s) => s.runs);
  const setRuns = useQscvStore((s) => s.setRuns);
  const allRuns = useQscvStore((s) => s.allRuns);
  const busy = useQscvStore((s) => s.busy);
  const setBusy = useQscvStore((s) => s.setBusy);

  const [runIdx, setRunIdx] = useState(0);
  const [stepIdx, setStepIdx] = useState(0);
  const [showUnusable, setShowUnusable] = useState(true);
  const [loadingRunId, setLoadingRunId] = useState<string | null>(null);

  const run = runs.length > 0 ? runs[Math.min(runIdx, runs.length - 1)] : null;

  useEffect(() => {
    setStepIdx(0);
  }, [run?.id]);

  const loadRun = async (id: string) => {
    setLoadingRunId(id);
    setBusy("load-run-results");
    try {
      const r = await api.getRun(id);
      setRuns([r]);
      setRunIdx(0);
      setStepIdx(0);
      toast.success(`Прогон загружен: ${r.label}`);
    } catch (e) {
      toast.error(`Не удалось загрузить прогон: ${(e as Error).message}`);
    } finally {
      setLoadingRunId(null);
      setBusy(null);
    }
  };

  // ============================== Данные (guarded, hooks до любого return) ==============================

  const cvData = useMemo(() => {
    if (!run) return [];
    return [...run.cvCurve]
      .sort((a, b) => a.vMid_V - b.vMid_V)
      .map((p) => ({
        v: p.vMid_V,
        fwd: p.direction === "forward" && p.usable ? p.cSec_F : null,
        fwdU: p.direction === "forward" && p.usable ? p.uC_F : null,
        rev: p.direction === "reverse" && p.usable ? p.cSec_F : null,
        revU: p.direction === "reverse" && p.usable ? p.uC_F : null,
        cTrue: p.cSecTrue_F,
        fwdBad: showUnusable && !p.usable && p.direction === "forward" ? p.cSec_F : null,
        revBad: showUnusable && !p.usable && p.direction === "reverse" ? p.cSec_F : null,
      }));
  }, [run, showUnusable]);

  const step: StepResult | null = useMemo(() => {
    if (!run || run.steps.length === 0) return null;
    return run.steps[Math.min(stepIdx, run.steps.length - 1)];
  }, [run, stepIdx]);

  const tailWindows = useMemo(() => {
    if (!step) return [];
    const tp = Math.max(1, run?.config.tailPoints ?? 10);
    return step.apertures.slice(-Math.min(tp, step.apertures.length));
  }, [step, run]);

  const tMean = useMemo(
    () =>
      tailWindows.length
        ? tailWindows.reduce((s, ap) => s + (ap.a_s + ap.b_s) / 2, 0) / tailWindows.length
        : 0,
    [tailWindows],
  );

  const apertureData = useMemo(() => {
    if (!step) return [];
    return step.apertures.map((ap) => {
      const tMid = (ap.a_s + ap.b_s) / 2;
      const iCorr = ap.i_avg_A - ap.i_background_A;
      const tailFit = step.tailB_A + step.tailS_A_s * (tMid - tMean);
      let model: number | null = null;
      if (step.fit) {
        let m = step.fit.baseline_A;
        for (const c of step.fit.components) m += c.a_A * meanExp(ap.a_s, ap.b_s, c.tau_s);
        model = m;
      }
      return { tMs: tMid * 1000, iCorr, tailFit, model, j: ap.j };
    });
  }, [step, tMean]);

  const convData = useMemo(() => {
    if (!step) return [];
    let q = 0;
    const pts: { tMs: number; q: number }[] = [{ tMs: (step.apertures[0]?.a_s ?? 0) * 1000, q: 0 }];
    for (const ap of step.apertures) {
      q += (ap.i_avg_A - ap.i_background_A) * (ap.b_s - ap.a_s);
      pts.push({ tMs: ap.b_s * 1000, q });
    }
    return pts;
  }, [step]);

  // ============================== Пустое состояние ==============================
  if (!run) {
    return (
      <div className="space-y-4">
        <PanelHeader
          title="Результаты"
          subtitle="Методика §8–12: заряд перехода, секущая C–V, хвост, энергия, неопределённость"
        />
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-10 text-center">
            <FolderOpen className="h-8 w-8 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">
              Запустите измерение на вкладке «Измерение» или загрузите сохранённый прогон.
            </p>
          </CardContent>
        </Card>
        {allRuns.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Сохранённые прогоны</CardTitle>
              <CardDescription>Загрузка из базы данных журнала (§13)</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {allRuns.map((r) => (
                <div key={r.id} className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm">{r.label}</div>
                    <div className="text-[11px] text-muted-foreground">
                      {new Date(r.createdAt).toLocaleString("ru-RU")}
                    </div>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => loadRun(r.id)} disabled={busy !== null}>
                    {loadingRunId === r.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                    Загрузить
                  </Button>
                </div>
              ))}
            </CardContent>
          </Card>
        )}
      </div>
    );
  }

  // ============================== Основной вывод ==============================

  const usableCount = run.cvCurve.filter((p) => p.usable).length;

  return (
    <div className="space-y-4">
      <PanelHeader
        title="Результаты"
        subtitle="Методика §8–12: заряд перехода, секущая C–V, хвост, энергия, неопределённость"
      />

      {/* Выбор прогона */}
      <Card>
        <CardContent className="flex flex-col gap-3 pt-6 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap items-center gap-2">
            {runs.length > 1 && (
              <div className="flex flex-wrap gap-1">
                {runs.map((r, i) => (
                  <Button
                    key={r.id}
                    size="sm"
                    variant={i === Math.min(runIdx, runs.length - 1) ? "default" : "outline"}
                    onClick={() => {
                      setRunIdx(i);
                      setStepIdx(0);
                    }}
                  >
                    {runLabel(r)}
                  </Button>
                ))}
              </div>
            )}
            {runs.length === 1 && <Badge variant="secondary">{run.label}</Badge>}
            {run.stopped && (
              <Badge variant="outline" className="border-red-500/60 text-red-600 dark:text-red-400">
                остановлен: {stopReasonText(run.stopReason)}
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>
              {run.protocol === "aperture_charge" ? "протокол B" : "протокол A"} · шагов {run.steps.length} · пригодно{" "}
              {usableCount}/{run.cvCurve.length}
            </span>
          </div>
        </CardContent>
      </Card>

      {/* C–V график */}
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="text-sm">Секущая C–V</CardTitle>
              <CardDescription>
                Секущая C_sec=ΔQ/ΔV на середине интервала; результат зависит от протокола (§3)
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <Label htmlFor="show-unusable" className="text-xs text-muted-foreground">
                показать непригодные (usable=false) шаги
              </Label>
              <Switch
                id="show-unusable"
                checked={showUnusable}
                onCheckedChange={setShowUnusable}
                aria-label="Показать непригодные шаги"
              />
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={cvData} margin={{ top: 8, right: 16, bottom: 18, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                <XAxis
                  dataKey="v"
                  type="number"
                  domain={["dataMin", "dataMax"]}
                  tick={{ fontSize: 11 }}
                  tickFormatter={(v) => formatVolts(Number(v), 2)}
                  label={{ value: "V середина интервала, В", position: "insideBottom", offset: -10, fontSize: 11 }}
                />
                <YAxis
                  tick={{ fontSize: 11 }}
                  tickFormatter={(v) => formatFarads(Number(v), 2)}
                  width={76}
                  label={{ value: "C_sec, Ф", angle: -90, position: "insideLeft", fontSize: 11 }}
                />
                <Tooltip
                  formatter={(v, name) => [formatFarads(Number(v)), String(name)]}
                  labelFormatter={(l) => `V = ${formatVolts(Number(l), 3)}`}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line
                  name="прямой ход"
                  dataKey="fwd"
                  stroke={EMERALD}
                  strokeWidth={2}
                  dot={{ r: 3 }}
                  connectNulls={false}
                  isAnimationActive={false}
                >
                  <ErrorBar dataKey="fwdU" direction="y" width={3} strokeWidth={1} stroke={EMERALD} />
                </Line>
                <Line
                  name="обратный ход"
                  dataKey="rev"
                  stroke={ORANGE}
                  strokeWidth={2}
                  dot={{ r: 3 }}
                  connectNulls={false}
                  isAnimationActive={false}
                >
                  <ErrorBar dataKey="revU" direction="y" width={3} strokeWidth={1} stroke={ORANGE} />
                </Line>
                <Line
                  name="истина (модель)"
                  dataKey="cTrue"
                  stroke={GRAY}
                  strokeWidth={1.5}
                  strokeDasharray="6 4"
                  dot={false}
                  connectNulls={false}
                  isAnimationActive={false}
                />
                <Scatter name="прямой (не годен)" dataKey="fwdBad" fill={GRAY} fillOpacity={0.55} isAnimationActive={false} />
                <Scatter name="обратный (не годен)" dataKey="revBad" fill={GRAY} fillOpacity={0.55} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      {/* Таблица шагов */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Шаги прогона</CardTitle>
          <CardDescription>
            Щелчок по строке открывает деталь шага ниже (апертуры, флаги, подгонка, энергия)
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ScrollBox className="max-h-96">
            <Table>
              <TableHeader className="sticky top-0 z-10 bg-card">
                <TableRow>
                  <TableHead>шаг</TableHead>
                  <TableHead>V до</TableHead>
                  <TableHead>V после</TableHead>
                  <TableHead>V серед</TableHead>
                  <TableHead>ΔV</TableHead>
                  <TableHead>ΔQ_obs</TableHead>
                  <TableHead>ΔQ корр</TableHead>
                  <TableHead>C_sec</TableHead>
                  <TableHead>u(C)</TableHead>
                  <TableHead>B хвоста</TableHead>
                  <TableHead>S хвоста</TableHead>
                  <TableHead>хвост</TableHead>
                  <TableHead>pre_settled</TableHead>
                  <TableHead>флаги</TableHead>
                  <TableHead>годен</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {run.steps.map((st, i) => (
                  <TableRow
                    key={st.step}
                    className={`cursor-pointer ${i === Math.min(stepIdx, run.steps.length - 1) ? "bg-muted" : ""}`}
                    onClick={() => setStepIdx(i)}
                  >
                    <TableCell className="font-mono text-xs">{st.step}</TableCell>
                    <TableCell className="font-mono text-xs">{formatVolts(st.vBefore_V, 3)}</TableCell>
                    <TableCell className="font-mono text-xs">{formatVolts(st.vAfter_V, 3)}</TableCell>
                    <TableCell className="font-mono text-xs">{formatVolts(st.vMid_V, 3)}</TableCell>
                    <TableCell className="font-mono text-xs">{formatVolts(st.dv_V, 3)}</TableCell>
                    <TableCell className="font-mono text-xs">{formatCoulombs(st.qObs_C)}</TableCell>
                    <TableCell className="font-mono text-xs">{formatCoulombs(st.qCorrected_C)}</TableCell>
                    <TableCell className="font-mono text-xs">{formatFarads(st.cSec_F)}</TableCell>
                    <TableCell className="font-mono text-xs">{formatFarads(st.uC_F)}</TableCell>
                    <TableCell className="font-mono text-xs">{formatAmps(st.tailB_A, 2)}</TableCell>
                    <TableCell className="font-mono text-xs">{formatSci(st.tailS_A_s, 2)}</TableCell>
                    <TableCell>
                      <OkBadge ok={st.tailOk} okText="ok" failText="не сошёлся" />
                    </TableCell>
                    <TableCell>
                      <OkBadge ok={st.preSettled} okText="да" failText="нет" />
                    </TableCell>
                    <TableCell>
                      <div className="flex max-w-56 flex-wrap gap-1">
                        {st.flags.length === 0 && <span className="text-xs text-muted-foreground">—</span>}
                        {st.flags.map((f) => (
                          <FlagBadge key={f} flag={f} />
                        ))}
                      </div>
                    </TableCell>
                    <TableCell>
                      <OkBadge ok={st.usableForPair} okText="да" failText="нет" />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </ScrollBox>
        </CardContent>
      </Card>

      {/* ============================== Деталь шага ============================== */}
      {step && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">
              Деталь шага {step.step} ({directionLabel(step.direction)}) — {formatVolts(step.vBefore_V, 3)} →{" "}
              {formatVolts(step.vAfter_V, 3)}
            </CardTitle>
            <CardDescription>
              Измерено/рассчитано из данных; «истина» — только для симуляции (разделение §13)
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* Апертурный график */}
            <div>
              <div className="mb-2 text-xs font-semibold">Ток по апертурам: Ī_j − L_j</div>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={apertureData} margin={{ top: 8, right: 16, bottom: 18, left: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                    <XAxis
                      dataKey="tMs"
                      type="number"
                      domain={["dataMin", "dataMax"]}
                      tick={{ fontSize: 11 }}
                      tickFormatter={(v) => msTick(Number(v))}
                      label={{ value: "t (середина окна), мс", position: "insideBottom", offset: -10, fontSize: 11 }}
                    />
                    <YAxis
                      tick={{ fontSize: 11 }}
                      tickFormatter={(v) => formatAmps(Number(v), 2)}
                      width={76}
                      label={{ value: "I, А", angle: -90, position: "insideLeft", fontSize: 11 }}
                    />
                    <Tooltip
                      formatter={(v, name) => [formatAmps(Number(v)), String(name)]}
                      labelFormatter={(l) => `t = ${msTick(Number(l))} мс`}
                    />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <ReferenceLine y={0} stroke={GRAY} strokeDasharray="2 2" />
                    <Bar name="Ī_j − L_j" dataKey="iCorr" fill={EMERALD} fillOpacity={0.8} isAnimationActive={false} />
                    <Line
                      name="хвост: B + S(t−t̄)"
                      dataKey="tailFit"
                      stroke={GRAY}
                      strokeWidth={1.5}
                      strokeDasharray="6 4"
                      dot={false}
                      isAnimationActive={false}
                    />
                    {step.fit && (
                      <Line
                        name={`модель ${step.fit.model}`}
                        dataKey="model"
                        stroke={AMBER}
                        strokeWidth={1.5}
                        dot={{ r: 2 }}
                        connectNulls={false}
                        isAnimationActive={false}
                      />
                    )}
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                Линия хвоста продлена на весь домен для наглядности; окно подгонки — последние {tailWindows.length}{" "}
                окон (tailPoints = {run.config.tailPoints}).
              </p>
            </div>

            {/* Таблица апертур */}
            <div>
              <div className="mb-2 text-xs font-semibold">Апертурные записи шага ({step.apertures.length})</div>
              <div className="max-h-72 overflow-y-auto rounded-md border">
                <Table>
                  <TableHeader className="sticky top-0 z-10 bg-card">
                    <TableRow>
                      <TableHead>j</TableHead>
                      <TableHead>a, с</TableHead>
                      <TableHead>b, с</TableHead>
                      <TableHead>Ī, А</TableHead>
                      <TableHead>L (фон), А</TableHead>
                      <TableHead>Ī − L, А</TableHead>
                      <TableHead>u_i, А</TableHead>
                      <TableHead>статус</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {step.apertures.map((ap) => (
                      <TableRow key={ap.j}>
                        <TableCell className="font-mono text-xs">{ap.j}</TableCell>
                        <TableCell className="font-mono text-xs">{formatSci(ap.a_s, 3)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatSci(ap.b_s, 3)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatAmps(ap.i_avg_A)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatAmps(ap.i_background_A)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatAmps(ap.i_avg_A - ap.i_background_A)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatAmps(ap.u_i_A)}</TableCell>
                        <TableCell>
                          <Badge
                            variant="outline"
                            className={
                              ap.status === "OK"
                                ? "border-emerald-500/60 text-emerald-700 dark:text-emerald-400"
                                : "border-red-500/60 text-red-600 dark:text-red-400"
                            }
                          >
                            {ap.status}
                          </Badge>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>

            {/* Пропуски */}
            <div className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-3">
              <div className="rounded-md border p-3">
                <div className="text-muted-foreground">Суммарная длительность пропусков</div>
                <div className="font-mono">{formatSeconds(step.gapDuration_s)}</div>
              </div>
              <div className="rounded-md border p-3 sm:col-span-2">
                <div className="text-muted-foreground">Интервалы пропусков [l, r], с</div>
                <div className="font-mono break-all">
                  {step.gaps_s.length === 0
                    ? "—"
                    : step.gaps_s.map(([l, r]) => `[${l.toExponential(2)}, ${r.toExponential(2)}]`).join("  ")}
                </div>
              </div>
            </div>

            {/* Протокол А (Кун) */}
            {step.kuhn && (
              <div>
                <div className="mb-2 text-xs font-semibold">Протокол A — одиночное измерение на ступени</div>
                <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                  <div className="rounded-md border p-3">
                    <div className="text-muted-foreground">Ī, А</div>
                    <div className="font-mono">{formatAmps(step.kuhn.iMeas_A)}</div>
                  </div>
                  <div className="rounded-md border p-3">
                    <div className="text-muted-foreground">r = ΔV/T, В/с</div>
                    <div className="font-mono">{formatSci(step.kuhn.rampRate_Vps, 3)}</div>
                  </div>
                  <div className="rounded-md border p-3">
                    <div className="text-muted-foreground">C = |Ī/r|</div>
                    <div className="font-mono">{formatFarads(step.kuhn.cKuhn_F)}</div>
                  </div>
                  <div className="rounded-md border p-3">
                    <div className="text-muted-foreground">C с коррекцией утечки</div>
                    <div className="font-mono">{formatFarads(step.kuhn.cKuhnLeakCorrected_F)}</div>
                  </div>
                </div>
              </div>
            )}

            {/* Флаги */}
            <div>
              <div className="mb-2 text-xs font-semibold">Флаги шага</div>
              {step.flags.length === 0 ? (
                <p className="text-xs text-muted-foreground">Флагов нет — шаг прошёл проверки.</p>
              ) : (
                <div className="space-y-1.5">
                  {step.flags.map((f) => (
                    <div key={f} className="flex flex-col gap-1 rounded-md border px-3 py-2 sm:flex-row sm:items-center sm:gap-3">
                      <FlagBadge flag={f} />
                      <span className="text-xs text-muted-foreground">{FLAG_DESCRIPTIONS[f] ?? "описание отсутствует"}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Подгонка */}
            {step.fit && (
              <div>
                <div className="mb-2 text-xs font-semibold">Экспоненциальная подгонка (модель {step.fit.model})</div>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>компонента</TableHead>
                      <TableHead>τ, с</TableHead>
                      <TableHead>A, А</TableHead>
                      <TableHead>заряд A·τ, Кл</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {step.fit.components.map((c, i) => (
                      <TableRow key={i}>
                        <TableCell className="text-xs">{i + 1}</TableCell>
                        <TableCell className="font-mono text-xs">{formatSeconds(c.tau_s)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatAmps(c.a_A)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatCoulombs(c.q_C)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                <div className="mt-2 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                  <div className="rounded-md border p-3">
                    <div className="text-muted-foreground">reduced χ²</div>
                    <div className="font-mono">{step.fit.reducedChi2.toFixed(2)}</div>
                  </div>
                  <div className="rounded-md border p-3">
                    <div className="text-muted-foreground">обусловленность</div>
                    <div className="font-mono">{step.fit.condition.toExponential(2)}</div>
                  </div>
                  <div className="rounded-md border p-3">
                    <div className="text-muted-foreground">успех подгонки</div>
                    <div>
                      <OkBadge ok={step.fit.success} okText="да" failText="нет" />
                    </div>
                  </div>
                  <div className="rounded-md border p-3">
                    <div className="text-muted-foreground">оставшийся хвост</div>
                    <div className="font-mono">{formatCoulombs(step.fit.remainingTail_C)}</div>
                  </div>
                </div>
                <p className="mt-2 text-[11px] text-muted-foreground">
                  Компонентный заряд A·τ не доказывает механизм ловушек (§9). Оставшийся экспоненциальный хвост
                  записан, но к результату автоматически не прибавляется.
                </p>
              </div>
            )}

            {/* Истина (симуляция) */}
            {step.truth && (
              <div>
                <div className="mb-2 text-xs font-semibold">
                  Истина (модель симуляции) — разделение §13: измерено/рассчитано/восстановлено/истина
                </div>
                <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-3 lg:grid-cols-4">
                  <div className="rounded-md border p-3">
                    <div className="text-muted-foreground">Q быстрая ветвь</div>
                    <div className="font-mono">{formatCoulombs(step.truth.qFast_C)}</div>
                  </div>
                  {step.truth.qBranch_C.map((q, i) => (
                    <div key={i} className="rounded-md border p-3">
                      <div className="text-muted-foreground">Q ветвь {i + 1} (за окно)</div>
                      <div className="font-mono">{formatCoulombs(q)}</div>
                    </div>
                  ))}
                  <div className="rounded-md border p-3">
                    <div className="text-muted-foreground">Q утечка (за окно)</div>
                    <div className="font-mono">{formatCoulombs(step.truth.qLeak_C)}</div>
                  </div>
                  <div className="rounded-md border p-3">
                    <div className="text-muted-foreground">Q истина в окнах</div>
                    <div className="font-mono">{formatCoulombs(step.truth.qRegistered_C)}</div>
                  </div>
                  <div className="rounded-md border p-3">
                    <div className="text-muted-foreground">Q истина в пропусках</div>
                    <div className="font-mono">{formatCoulombs(step.truth.qInGaps_C)}</div>
                  </div>
                  <div className="rounded-md border p-3">
                    <div className="text-muted-foreground">Q полный перехода</div>
                    <div className="font-mono">{formatCoulombs(step.truth.qTotal_C)}</div>
                  </div>
                  <div className="rounded-md border p-3">
                    <div className="text-muted-foreground">C_sec истина</div>
                    <div className="font-mono">{formatFarads(step.truth.cSecTrue_F)}</div>
                  </div>
                  <div className="rounded-md border p-3">
                    <div className="text-muted-foreground">отклонение измеренной</div>
                    <div className="font-mono">
                      {step.truth.cSecTrue_F !== 0
                        ? `${(((step.cSec_F - step.truth.cSecTrue_F) / step.truth.cSecTrue_F) * 100).toFixed(2)} %`
                        : "—"}
                    </div>
                  </div>
                </div>
              </div>
            )}

            <Separator />

            {/* Энергия */}
            <div>
              <div className="mb-2 text-xs font-semibold">Энергия шага (§10)</div>
              <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                <div className="rounded-md border p-3">
                  <div className="text-muted-foreground">W_port = ∫V·I dt</div>
                  <div className="font-mono">{formatJoules(step.wPort_J)}</div>
                </div>
                <div className="rounded-md border p-3">
                  <div className="text-muted-foreground">ΔU запасённой</div>
                  <div className="font-mono">{formatJoules(step.dU_J)}</div>
                </div>
                <div className="rounded-md border p-3">
                  <div className="text-muted-foreground">диссипация модели</div>
                  <div className="font-mono">{formatJoules(step.dissipated_J)}</div>
                </div>
                <div className="rounded-md border p-3">
                  <div className="text-muted-foreground">невязка баланса (≈0)</div>
                  <div
                    className={`font-mono ${
                      Math.abs(step.energyResidual_J) < 1e-18
                        ? "text-emerald-700 dark:text-emerald-400"
                        : "text-amber-700 dark:text-amber-400"
                    }`}
                  >
                    {formatJoules(step.energyResidual_J)}
                  </div>
                </div>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                <div className="rounded-md border bg-muted/30 p-3">
                  <div className="text-muted-foreground">W_port за прогон</div>
                  <div className="font-mono">{formatJoules(run.wPortTotal_J)}</div>
                </div>
                <div className="rounded-md border bg-muted/30 p-3">
                  <div className="text-muted-foreground">ΔU за прогон</div>
                  <div className="font-mono">{formatJoules(run.dUTotal_J)}</div>
                </div>
                <div className="rounded-md border bg-muted/30 p-3">
                  <div className="text-muted-foreground">диссипация за прогон</div>
                  <div className="font-mono">{formatJoules(run.dissipatedTotal_J)}</div>
                </div>
                <div className="rounded-md border bg-muted/30 p-3">
                  <div className="text-muted-foreground">∮V dQ (гистерезис)</div>
                  <div className="font-mono">{formatJoules(run.loopArea_J)}</div>
                </div>
              </div>
              <p className="mt-2 text-[11px] text-muted-foreground">
                W_port=∫V·I dt; при утечке, trapping, гистерезисе работа порта ≠ автоматически изменению запасённой
                энергии (§10). ∮V dQ за цикл — гистерезисные потери.
              </p>
            </div>

            {/* Неопределённость */}
            <div>
              <div className="mb-2 text-xs font-semibold">Неопределённость шага (§12)</div>
              <div className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-3">
                <div className="rounded-md border p-3">
                  <div className="text-muted-foreground">u(ΔQ_obs)</div>
                  <div className="font-mono">{formatCoulombs(step.uQ_C)}</div>
                </div>
                <div className="rounded-md border p-3">
                  <div className="text-muted-foreground">u(C_sec)</div>
                  <div className="font-mono">{formatFarads(step.uC_F)}</div>
                </div>
                <div className="rounded-md border p-3">
                  <div className="text-muted-foreground">u(Qgap) только от fit</div>
                  <div className="font-mono">{formatCoulombs(step.fit?.uGapFitOnly_C ?? 0)}</div>
                </div>
              </div>
              <div className="mt-2 rounded-md bg-muted/50 px-3 py-2 font-mono text-[11px] leading-relaxed">
                u²(Q_obs) ≈ Σ [w_j²·u²(I_j−L_j) + (I_j−L_j)²·u²(w_j)]
              </div>
              <p className="mt-2 text-[11px] text-muted-foreground">
                Общие offset/gain и корреляции не уменьшаются как независимый шум; полный бюджет C=Q/ΔV учитывает
                covariance(Q,ΔV). Q_obs и модельный Q_gap коррелированы.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Сходимость */}
      {step && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Сходимость заряда по записи (§9)</CardTitle>
            <CardDescription>
              Q(T) = Σ (Ī_j−L_j)(b_j−a_j) по окнам с b_j ≤ T; пунктир — скорректированный заряд шага
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="h-60">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={convData} margin={{ top: 8, right: 16, bottom: 18, left: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                  <XAxis
                    dataKey="tMs"
                    type="number"
                    domain={["dataMin", "dataMax"]}
                    tick={{ fontSize: 11 }}
                    tickFormatter={(v) => msTick(Number(v))}
                    label={{ value: "T (конец окна), мс", position: "insideBottom", offset: -10, fontSize: 11 }}
                  />
                  <YAxis
                    tick={{ fontSize: 11 }}
                    tickFormatter={(v) => formatCoulombs(Number(v), 2)}
                    width={80}
                    label={{ value: "Q, Кл", angle: -90, position: "insideLeft", fontSize: 11 }}
                  />
                  <Tooltip
                    formatter={(v) => [formatCoulombs(Number(v)), "Q(T)"]}
                    labelFormatter={(l) => `T = ${msTick(Number(l))} мс`}
                  />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <ReferenceLine
                    y={step.qCorrected_C}
                    stroke={AMBER}
                    strokeDasharray="6 4"
                    label={{ value: "ΔQ корр", fontSize: 10, fill: AMBER, position: "insideTopRight" }}
                  />
                  <Line
                    name="накопленный заряд Q(T)"
                    dataKey="q"
                    stroke={EMERALD}
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <Alert className="mt-3">
              <MousePointerClick />
              <AlertDescription>
                Решающая проверка — сходимость заряда при увеличении записи. Если сходимости нет, сообщают
                C_sec(V; T_obs, подготовка), а не безусловную равновесную C_qs.
              </AlertDescription>
            </Alert>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
