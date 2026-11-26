"use client";
/**
 * Панель «Измерение» — методика §7.
 * Сводка параметров, запуск (симуляция), воспроизведение зарегистрированных шагов,
 * стоп-условия и безопасность.
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  AlertOctagon,
  Gauge,
  Loader2,
  Pause,
  Play,
  ShieldAlert,
  SquareArrowOutUpRight,
  Zap,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useQscvStore } from "@/lib/store";
import { api } from "@/lib/qscv/api-client";
import { formatAmps, formatCoulombs, formatSeconds, formatVolts } from "@/lib/qscv/format";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";
import { PanelHeader, directionLabel, effectiveAperture, stopReasonText } from "./shared";

const SPEEDS = [1, 4, 16];

export default function MeasurementPanel() {
  const config = useQscvStore((s) => s.config);
  const setConfig = useQscvStore((s) => s.setConfig);
  const dutLabel = useQscvStore((s) => s.dutLabel);
  const runs = useQscvStore((s) => s.runs);
  const setRuns = useQscvStore((s) => s.setRuns);
  const setTab = useQscvStore((s) => s.setTab);
  const busy = useQscvStore((s) => s.busy);
  const setBusy = useQscvStore((s) => s.setBusy);

  const [running, setRunning] = useState(false);
  const [playIdx, setPlayIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);

  const aperture = useMemo(() => effectiveAperture(config), [config]);
  const transitionsPerSweep = useMemo(() => {
    if (config.vStep_V <= 0) return 0;
    return Math.ceil(Math.abs(config.vEnd_V - config.vStart_V) / config.vStep_V);
  }, [config.vStart_V, config.vEnd_V, config.vStep_V]);

  const allSteps = useMemo(
    () => runs.flatMap((r) => r.steps.map((st) => ({ run: r, step: st }))),
    [runs],
  );

  // Новый прогон — сброс воспроизведения
  useEffect(() => {
    setPlayIdx(0);
    setPlaying(runs.length > 0 && runs.some((r) => r.steps.length > 1));
  }, [runs]);

  // Такт воспроизведения
  useEffect(() => {
    if (!playing) return;
    if (playIdx >= allSteps.length - 1) {
      setPlaying(false);
      return;
    }
    const t = setTimeout(() => setPlayIdx((i) => Math.min(i + 1, allSteps.length - 1)), 500 / speed);
    return () => clearTimeout(t);
  }, [playing, playIdx, speed, allSteps.length]);

  const current = allSteps.length > 0 ? allSteps[Math.min(playIdx, allSteps.length - 1)] : null;

  const cumulativeQ = useMemo(() => {
    if (!current) return 0;
    let q = 0;
    for (const item of allSteps.slice(0, playIdx + 1)) {
      if (item.run.id === current.run.id) q += item.step.qObs_C;
    }
    return q;
  }, [allSteps, playIdx, current]);

  const barData = useMemo(() => {
    if (!current) return [];
    return current.step.apertures.map((ap, idx) => ({ j: idx + 1, i: ap.i_avg_A }));
  }, [current]);

  const finished = allSteps.length > 0 && playIdx >= allSteps.length - 1 && !playing;
  const stoppedRuns = runs.filter((r) => r.stopped);
  const busyAny = running || busy !== null;

  const onRun = async () => {
    setRunning(true);
    setBusy("run");
    setPlaying(false);
    try {
      const result = await api.runMeasurement({ config, dutLabel });
      setRuns(result);
      toast.success(`Измерение выполнено: прогонов ${result.length}, всего шагов ${result.reduce((n, r) => n + r.steps.length, 0)}`);
    } catch (e) {
      toast.error(`Ошибка измерения: ${(e as Error).message}`);
    } finally {
      setRunning(false);
      setBusy(null);
    }
  };

  const onHalveStep = () => {
    const newStep = config.vStep_V / 2;
    const newCount = Math.max(1, Math.round(config.trigCount * 2));
    setConfig({ ...config, vStep_V: newStep, trigCount: newCount });
    toast.info("Шаг уменьшен вдвое, регистрация удлинена — перезапустите измерение");
  };

  return (
    <div className="space-y-4">
      <PanelHeader
        title="Измерение"
        subtitle="Методика §7: пошаговый регламент, контроль условий и стоп-условия; симуляция модели DUT с последующим воспроизведением"
      />

      {/* Сводка параметров */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Сводка параметров программы</CardTitle>
          <CardDescription>
            {config.protocol === "aperture_charge"
              ? "Протокол B — зарядовый: C_sec = ΔQ/ΔV_DUT (апертурная регистрация)"
              : "Протокол A — линейная развёртка (Кун): C = |I/(ΔV/T)|"}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
            <div className="rounded-md border p-3">
              <div className="text-[11px] text-muted-foreground">Диапазон V</div>
              <div className="font-mono text-xs">
                {formatVolts(config.vStart_V)} … {formatVolts(config.vEnd_V)}
              </div>
            </div>
            <div className="rounded-md border p-3">
              <div className="text-[11px] text-muted-foreground">Шаг ΔV</div>
              <div className="font-mono text-xs">{formatVolts(config.vStep_V)}</div>
            </div>
            <div className="rounded-md border p-3">
              <div className="text-[11px] text-muted-foreground">Переходов за развёртку</div>
              <div className="font-mono text-xs">{transitionsPerSweep}</div>
            </div>
            <div className="rounded-md border p-3">
              <div className="text-[11px] text-muted-foreground">Апертур на шаг</div>
              <div className="font-mono text-xs">{config.trigCount}</div>
            </div>
            <div className="rounded-md border p-3">
              <div className="text-[11px] text-muted-foreground">Интервал таймера</div>
              <div className="font-mono text-xs">{formatSeconds(config.trigInterval_s)}</div>
            </div>
            <div className="rounded-md border p-3">
              <div className="text-[11px] text-muted-foreground">Апертура</div>
              <div className="font-mono text-xs">{formatSeconds(aperture.seconds)}</div>
            </div>
            <div className="rounded-md border p-3">
              <div className="text-[11px] text-muted-foreground">Compliance</div>
              <div className="font-mono text-xs">{formatAmps(config.iCompliance_A)}</div>
            </div>
          </div>
          <p className="mt-3 text-[11px] text-muted-foreground">
            Направления: {config.directions.map(directionLabel).join(", ") || "не выбраны"} · циклов: {config.cycles} ·
            подготовка: {formatVolts(config.preBias_V)} в течение {formatSeconds(config.preBiasDuration_s)} (§7.1)
          </p>
        </CardContent>
      </Card>

      {/* Запуск и воспроизведение */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Выполнение измерения</CardTitle>
          <CardDescription>
            Симуляция выполняется на выбранной модели DUT (паспорт программы); воспроизведение показывает
            зарегистрированные апертуры по шагам
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
            <Button size="lg" onClick={onRun} disabled={busyAny} className="min-w-56">
              {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4" />}
              {running ? "Выполняется…" : "Запустить измерение"}
            </Button>
            <Button variant="outline" onClick={onHalveStep} disabled={busyAny || runs.length === 0}>
              Повторить с меньшим шагом (§7.3 п.4)
            </Button>
          </div>

          {running && (
            <div className="space-y-2">
              <Progress value={100} className="h-2 animate-pulse" />
              <p className="text-xs text-muted-foreground">
                Симуляция шагов и апертур выполняется; элементы управления заблокированы…
              </p>
            </div>
          )}

          {allSteps.length === 0 && !running && (
            <div className="flex flex-col items-center gap-2 rounded-md border border-dashed py-10 text-center">
              <Gauge className="h-8 w-8 text-muted-foreground/50" />
              <p className="text-sm text-muted-foreground">
                Измерение ещё не выполнялось. Настройте паспорт программы и нажмите «Запустить измерение».
              </p>
            </div>
          )}

          {allSteps.length > 0 && (
            <div className="space-y-3">
              {/* Панель воспроизведения */}
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="outline"
                  size="icon"
                  aria-label={playing ? "Пауза" : "Воспроизвести"}
                  onClick={() => {
                    if (finished) {
                      setPlayIdx(0);
                      setPlaying(true);
                    } else {
                      setPlaying((p) => !p);
                    }
                  }}
                >
                  {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                </Button>
                <div className="flex items-center gap-1">
                  {SPEEDS.map((s) => (
                    <Button
                      key={s}
                      type="button"
                      size="sm"
                      variant={speed === s ? "default" : "outline"}
                      onClick={() => setSpeed(s)}
                      aria-label={`Скорость ×${s}`}
                    >
                      ×{s}
                    </Button>
                  ))}
                </div>
                <div className="ml-auto text-xs text-muted-foreground">
                  {finished ? (
                    <span className="font-medium text-emerald-700 dark:text-emerald-400">
                      Готово: {allSteps.length} шагов
                    </span>
                  ) : (
                    <>
                      Шаг {playIdx + 1} из {allSteps.length}
                    </>
                  )}
                </div>
              </div>

              <Slider
                value={[Math.min(playIdx, allSteps.length - 1)]}
                max={Math.max(0, allSteps.length - 1)}
                step={1}
                onValueChange={(v) => {
                  setPlaying(false);
                  setPlayIdx(v[0] ?? 0);
                }}
                aria-label="Положение воспроизведения"
                disabled={allSteps.length <= 1}
              />
              <Progress value={allSteps.length > 1 ? (playIdx / (allSteps.length - 1)) * 100 : 100} className="h-1.5" />

              {current && (
                <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                  {/* Живые показания шага */}
                  <div className="space-y-3 rounded-md border p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="secondary">{current.run.label}</Badge>
                      <Badge variant="outline">{directionLabel(current.step.direction)}</Badge>
                      <Badge variant="outline">шаг {current.step.step}</Badge>
                      {current.step.preSettled ? (
                        <Badge variant="outline" className="border-emerald-500/60 text-emerald-700 dark:text-emerald-400">
                          pre_settled
                        </Badge>
                      ) : (
                        <Badge variant="outline" className="border-amber-500/60 text-amber-700 dark:text-amber-400">
                          подготовка не подтверждена
                        </Badge>
                      )}
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                      <div>
                        <div className="text-muted-foreground">V до → после</div>
                        <div className="font-mono">
                          {formatVolts(current.step.vBefore_V, 3)} → {formatVolts(current.step.vAfter_V, 3)}
                        </div>
                      </div>
                      <div>
                        <div className="text-muted-foreground">ΔV_DUT</div>
                        <div className="font-mono">{formatVolts(current.step.dv_V, 3)}</div>
                      </div>
                      <div>
                        <div className="text-muted-foreground">ΔQ_obs шага</div>
                        <div className="font-mono">{formatCoulombs(current.step.qObs_C)}</div>
                      </div>
                      <div>
                        <div className="text-muted-foreground">Σ ΔQ по прогону</div>
                        <div className="font-mono">{formatCoulombs(cumulativeQ)}</div>
                      </div>
                    </div>
                    <p className="text-[11px] text-muted-foreground">
                      ΔQ_obs = Σ (Ī_j − L_j)(b_j − a_j) по {current.step.apertures.length} окнам; накопление по
                      воспроизведённым шагам прогона.
                    </p>
                  </div>

                  {/* Мини-график токов апертур */}
                  <div className="rounded-md border p-3">
                    <div className="mb-1 text-xs font-semibold">Токи апертур Ī_j текущего шага</div>
                    <div className="h-44">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={barData} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
                          <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                          <XAxis
                            dataKey="j"
                            tick={{ fontSize: 10 }}
                            label={{ value: "j", position: "insideBottomRight", offset: -2, fontSize: 10 }}
                          />
                          <YAxis
                            tick={{ fontSize: 10 }}
                            tickFormatter={(v: number) => formatAmps(v, 2)}
                            width={72}
                          />
                          <Tooltip
                            formatter={(v: number | string) => [formatAmps(Number(v)), "Ī_j"]}
                            labelFormatter={(l) => `апертура ${l}`}
                          />
                          <Bar dataKey="i" fill="#10b981" radius={[2, 2, 0, 0]} isAnimationActive={false} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                </div>
              )}

              {/* Завершение */}
              {finished && (
                <div className="space-y-2">
                  {stoppedRuns.length > 0 ? (
                    stoppedRuns.map((r) => (
                      <Alert key={r.id} variant="destructive">
                        <AlertOctagon />
                        <AlertTitle>
                          Прогон «{r.label}» остановлен: {stopReasonText(r.stopReason)}
                        </AlertTitle>
                        <AlertDescription>
                          Код: <span className="font-mono">{r.stopReason}</span> · шагов выполнено: {r.steps.length}.
                          Обстоятельства остановки фиксируют в журнале (§13) до перезапуска.
                        </AlertDescription>
                      </Alert>
                    ))
                  ) : (
                    <Alert>
                      <Gauge />
                      <AlertDescription>
                        Последовательность завершена штатно: {runs.length} прогон(ов), всего {allSteps.length} шагов.
                        Стоп-условия не сработали.
                      </AlertDescription>
                    </Alert>
                  )}
                  <Button onClick={() => setTab("results")}>
                    <SquareArrowOutUpRight className="h-4 w-4" />
                    Открыть результаты
                  </Button>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Безопасность §7 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Стоп-условия и завершение (§7)</CardTitle>
          <CardDescription>Наблюдение за последовательностью выполняется во время всего измерения</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <Alert variant="destructive">
            <ShieldAlert />
            <AlertDescription>
              Остановить последовательность при пробое/росте утечки, перегрузке, недопустимом compliance, неустойчивости
              или потере временной привязки.
            </AlertDescription>
          </Alert>
          <Alert>
            <ShieldAlert />
            <AlertDescription>
              После опыта безопасно вернуть bias и разрядить DUT по выбранному протоколу.
            </AlertDescription>
          </Alert>
          <p className={cn("text-[11px] text-muted-foreground")}>
            Коды остановки: BREAKDOWN_OR_LEAKAGE_GROWTH · OVERLOAD · COMPLIANCE · INSTABILITY · TIMING_LOSS ·
            TAIL_NOT_SETTLED.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
