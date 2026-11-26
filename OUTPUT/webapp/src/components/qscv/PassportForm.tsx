"use client";
/**
 * Панель «Паспорт программы» — методика §5 (плюс §7.1, §9, §12, qscv_final).
 * Редактор MeasurementConfig + модель DUT (симуляция) + пресеты + проверка согласованности.
 */
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, Loader2, Save, Settings2 } from "lucide-react";
import { useQscvStore, currentDut } from "@/lib/store";
import { api } from "@/lib/qscv/api-client";
import {
  DEFAULT_CONFIG,
  DUT_PRESETS,
  type BackgroundMode,
  type FitModel,
  type GapMode,
  type MeasurementConfig,
  type SpeedMode,
} from "@/lib/qscv/types";
import { formatAmps, formatEng, formatFarads, formatSeconds } from "@/lib/qscv/format";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { NumField, PanelHeader, TimingDiagram, effectiveAperture } from "./shared";

const CURRENT_RANGES: { value: number; label: string }[] = [
  { value: 10e-9, label: "10 нА" },
  { value: 100e-9, label: "100 нА" },
  { value: 1e-6, label: "1 мкА" },
  { value: 10e-6, label: "10 мкА" },
  { value: 100e-6, label: "100 мкА" },
  { value: 1e-3, label: "1 мА" },
  { value: 10e-3, label: "10 мА" },
  { value: 100e-3, label: "100 мА" },
];

const BACKGROUND_MODES: { value: BackgroundMode; label: string; desc: string }[] = [
  { value: "none", label: "none", desc: "фон не вычитается" },
  { value: "mean_tail", label: "mean_tail", desc: "постоянный фон из среднего хвоста" },
  { value: "linear_tail", label: "linear_tail", desc: "линейный фон B+S(t−t̄) из хвоста" },
  { value: "blank", label: "blank", desc: "фон из blank-измерения (оснастка без DUT)" },
];

const SPEED_MODES: { value: SpeedMode; label: string }[] = [
  { value: "auto", label: "AUTO" },
  { value: "short", label: "SHORT (0.01 PLC)" },
  { value: "medium", label: "MEDIUM (0.1 PLC)" },
  { value: "normal", label: "NORMAL (1 PLC)" },
  { value: "long", label: "LONG (10 PLC)" },
  { value: "manual", label: "MANUAL (NPLC или апертура, с)" },
];

const SECTION_IDS = [
  "voltage",
  "current",
  "integration",
  "timing",
  "preparation",
  "background",
  "tail",
  "gaps",
  "uncertainty",
  "housekeeping",
  "noise",
];

export default function PassportForm() {
  const config = useQscvStore((s) => s.config);
  const setConfig = useQscvStore((s) => s.setConfig);
  const dutLabel = useQscvStore((s) => s.dutLabel);
  const setDutLabel = useQscvStore((s) => s.setDutLabel);
  const busy = useQscvStore((s) => s.busy);
  const [saving, setSaving] = useState(false);

  const upd = (patch: Partial<MeasurementConfig>) => setConfig({ ...config, ...patch });

  const aperture = useMemo(() => effectiveAperture(config), [config]);

  const warnings = useMemo(() => {
    const w: string[] = [];
    if (config.vStep_V <= 0) w.push("Шаг напряжения должен быть положительным числом.");
    if (config.vStart_V === config.vEnd_V) w.push("Начальное и конечное напряжение совпадают: развёртка не выполнится.");
    if (config.trigInterval_s > 0 && aperture.seconds > config.trigInterval_s)
      w.push(
        `Апертура (${formatSeconds(aperture.seconds)}) превышает период таймера (${formatSeconds(
          config.trigInterval_s,
        )}): измерения будут накладываться либо триггеры будут пропущены.`,
      );
    else if (config.trigInterval_s > 0 && aperture.seconds > 0.9 * config.trigInterval_s)
      w.push(
        `Апертура (${formatSeconds(aperture.seconds)}) занимает более 90% периода таймера (${formatSeconds(
          config.trigInterval_s,
        )}): вместе со служебным временем измерения возможна потеря триггеров (сверить по Programming Guide).`,
      );
    if (config.trigCount < config.tailPoints + 2)
      w.push(
        `Число апертур на шаг (${config.trigCount}) меньше tailPoints+2 (${config.tailPoints + 2}): анализ хвоста §9 невозможен.`,
      );
    if (config.protocol === "kuhn_staircase" && config.trigCount !== 1)
      w.push("Для протокола A (Кун) на ступень выполняется одиночное измерение: trigCount обычно равен 1.");
    if (config.directions.length === 0) w.push("Не выбрано ни одного направления развёртки.");
    return w;
  }, [config, aperture]);

  const dut = currentDut(dutLabel);

  const onSave = async () => {
    setSaving(true);
    try {
      await api.saveConfig({ config, dutLabel });
      toast.success("Конфигурация сохранена");
    } catch (e) {
      toast.error(`Не удалось сохранить конфигурацию: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      <PanelHeader
        title="Паспорт программы"
        subtitle="Методика §5: все параметры измерения задаются до запуска; численные значения согласуются с триггерной моделью B2900"
      />

      {/* Соглашения по значению полей см. в методике §5; предупреждения ниже — не блокирующие */}
      {warnings.length > 0 && (
        <Alert>
          <AlertTriangle />
          <AlertTitle>Проверка согласованности параметров ({warnings.length})</AlertTitle>
          <AlertDescription>
            <ul className="list-disc space-y-1 pl-4">
              {warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      <Accordion type="multiple" defaultValue={SECTION_IDS} className="rounded-xl border bg-card px-6">
        {/* ============================== Напряжение ============================== */}
        <AccordionItem value="voltage">
          <AccordionTrigger className="text-sm font-semibold">Напряжение</AccordionTrigger>
          <AccordionContent>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <NumField label="V начальное, В" value={config.vStart_V} onChange={(v) => upd({ vStart_V: v })} step="any" />
              <NumField label="V конечное, В" value={config.vEnd_V} onChange={(v) => upd({ vEnd_V: v })} step="any" />
              <NumField
                label="Шаг ΔV, В"
                value={config.vStep_V}
                onChange={(v) => upd({ vStep_V: v })}
                step="any"
                min={0}
                hint="положительное число; направление задаётся Vнач → Vкон"
              />
              <NumField
                label="Повторы цикла"
                value={config.cycles}
                onChange={(v) => upd({ cycles: Math.max(1, Math.round(v)) })}
                step="1"
                min={1}
                hint="§7.3 п.3: повтор цикла прямого/обратного хода"
              />
            </div>
            <Separator className="my-4" />
            <div className="space-y-2">
              <Label className="text-xs">Направления развёртки</Label>
              <div className="flex flex-wrap gap-4">
                <label className="flex cursor-pointer items-center gap-2 text-sm">
                  <Checkbox
                    checked={config.directions.includes("forward")}
                    onCheckedChange={(v) =>
                      upd({
                        directions: v
                          ? [...config.directions, "forward"]
                          : config.directions.filter((d) => d !== "forward"),
                      })
                    }
                  />
                  прямой ход
                </label>
                <label className="flex cursor-pointer items-center gap-2 text-sm">
                  <Checkbox
                    checked={config.directions.includes("reverse")}
                    onCheckedChange={(v) =>
                      upd({
                        directions: v
                          ? [...config.directions, "reverse"]
                          : config.directions.filter((d) => d !== "reverse"),
                      })
                    }
                  />
                  обратный ход
                </label>
              </div>
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* ============================== Ток ============================== */}
        <AccordionItem value="current">
          <AccordionTrigger className="text-sm font-semibold">Ток</AccordionTrigger>
          <AccordionContent>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <NumField
                label="Compliance, А"
                value={config.iCompliance_A}
                onChange={(v) => upd({ iCompliance_A: v })}
                step="any"
                hint="ограничение тока защиты DUT (§4), не по максимуму SMU"
              />
              <div className="space-y-1.5">
                <Label className="text-xs">Режим диапазона</Label>
                <Select
                  value={config.iRangeMode}
                  onValueChange={(v) => upd({ iRangeMode: v as "auto" | "fixed" })}
                >
                  <SelectTrigger className="w-full" aria-label="Режим диапазона тока">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">auto (автопереключение)</SelectItem>
                    <SelectItem value="fixed">fixed (фиксированный)</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-[11px] text-muted-foreground">autorange полезен, когда ток меняется на порядки</p>
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Диапазон тока</Label>
                <Select
                  value={String(config.iRange_A)}
                  onValueChange={(v) => upd({ iRange_A: parseFloat(v) })}
                  disabled={config.iRangeMode === "auto"}
                >
                  <SelectTrigger className="w-full" aria-label="Диапазон тока">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CURRENT_RANGES.map((r) => (
                      <SelectItem key={r.value} value={String(r.value)}>
                        {r.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-[11px] text-muted-foreground">измерительные диапазоны B2900B/BL</p>
              </div>
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* ============================== Интегрирование ============================== */}
        <AccordionItem value="integration">
          <AccordionTrigger className="text-sm font-semibold">Интегрирование (§5)</AccordionTrigger>
          <AccordionContent>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <div className="space-y-1.5">
                <Label className="text-xs">Режим скорости (User&apos;s Guide)</Label>
                <Select value={config.speedMode} onValueChange={(v) => upd({ speedMode: v as SpeedMode })}>
                  <SelectTrigger className="w-full" aria-label="Режим скорости">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SPEED_MODES.map((m) => (
                      <SelectItem key={m.value} value={m.value}>
                        {m.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-[11px] text-muted-foreground">{aperture.note}</p>
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Задание апертуры (MANUAL)</Label>
                <Select
                  value={config.apertureMode}
                  onValueChange={(v) => upd({ apertureMode: v as "nplc" | "seconds" })}
                  disabled={config.speedMode !== "manual"}
                >
                  <SelectTrigger className="w-full" aria-label="Задание апертуры">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="nplc">NPLC (число периодов сети)</SelectItem>
                    <SelectItem value="seconds">секунды</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {config.speedMode === "manual" && config.apertureMode === "nplc" && (
                <NumField
                  label="NPLC"
                  value={config.nplc}
                  onChange={(v) => upd({ nplc: v })}
                  step="0.01"
                  min={0.01}
                  hint="число периодов сети на одно измерение"
                />
              )}
              {config.speedMode === "manual" && config.apertureMode === "seconds" && (
                <NumField
                  label="Апертура, с"
                  value={config.aperture_s}
                  onChange={(v) => upd({ aperture_s: v })}
                  step="any"
                  min={0}
                  hint="длительность интегрирования одного отсчёта"
                />
              )}
              <div className="space-y-1.5">
                <Label className="text-xs">Частота сети, Гц</Label>
                <Select value={String(config.lineFreq_Hz)} onValueChange={(v) => upd({ lineFreq_Hz: parseFloat(v) })}>
                  <SelectTrigger className="w-full" aria-label="Частота сети">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="50">50 Гц</SelectItem>
                    <SelectItem value="60">60 Гц</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-[11px] text-muted-foreground">фактическая частота сети на объекте</p>
              </div>
            </div>
            <div className="mt-3 rounded-md bg-muted/50 px-3 py-2 font-mono text-xs">
              Апертура: {formatSeconds(aperture.seconds)} ({aperture.nplc.toFixed(aperture.nplc < 0.1 ? 3 : 2)} PLC)
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* ============================== Тайминг ============================== */}
        <AccordionItem value="timing">
          <AccordionTrigger className="text-sm font-semibold">Тайминг — триггерная модель B2900</AccordionTrigger>
          <AccordionContent>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
              <NumField
                label="Период таймера, с"
                value={config.trigInterval_s}
                onChange={(v) => upd({ trigInterval_s: v })}
                step="any"
                hint=":TRIG:TIM — интервал между точками триггера ≠ апертура"
              />
              <NumField
                label="Апертур на шаг"
                value={config.trigCount}
                onChange={(v) => upd({ trigCount: Math.max(1, Math.round(v)) })}
                step="1"
                min={1}
                hint=":TRIG:COUN (протокол B)"
              />
              <NumField
                label="Задержка источника, с"
                value={config.tranDelay_s}
                onChange={(v) => upd({ tranDelay_s: v })}
                step="any"
                hint=":TRIG:TRAN:DEL — задержка изменения источника"
              />
              <NumField
                label="Задержка измерения, с"
                value={config.acqDelay_s}
                onChange={(v) => upd({ acqDelay_s: v })}
                step="any"
                hint=":TRIG:ACQ:DEL — задержка первой апертуры от триггера"
              />
              <NumField
                label="Длительность перехода, с"
                value={config.tranTime_s}
                onChange={(v) => upd({ tranTime_s: v })}
                step="any"
                hint="модель линейного фронта источника"
              />
            </div>
            <div className="mt-4 rounded-md border p-3">
              <TimingDiagram
                interval={config.trigInterval_s}
                aperture={aperture.seconds}
                tranDelay={config.tranDelay_s}
                acqDelay={config.acqDelay_s}
                tranTime={config.tranTime_s}
                windows={5}
                mode="config"
              />
              <p className="mt-1 text-[11px] text-muted-foreground">
                Схема: переход источника и первые окна апертур a_j=(j−1)·интервал+задержка, b_j=a_j+апертура. Ось —
                относительное время от триггера перехода.
              </p>
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* ============================== Подготовка ============================== */}
        <AccordionItem value="preparation">
          <AccordionTrigger className="text-sm font-semibold">Подготовка (§7.1)</AccordionTrigger>
          <AccordionContent>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <NumField
                label="Предварительный bias, В"
                value={config.preBias_V}
                onChange={(v) => upd({ preBias_V: v })}
                step="any"
                hint="напряжение подготовки до первого шага"
              />
              <NumField
                label="Длительность подготовки, с"
                value={config.preBiasDuration_s}
                onChange={(v) => upd({ preBiasDuration_s: v })}
                step="any"
              />
              <NumField
                label="Критерий |dI/dt|, А/с"
                value={config.settleDIdt_A_s}
                onChange={(v) => upd({ settleDIdt_A_s: v })}
                step="any"
                hint="0 = критерий установления только по времени"
              />
            </div>
            <p className="mt-3 text-[11px] text-muted-foreground">
              pre_settled=true допустим только после документированной подготовки; первый шаг ветви без неё — только
              диагностический (§7.1).
            </p>
          </AccordionContent>
        </AccordionItem>

        {/* ============================== Фон ============================== */}
        <AccordionItem value="background">
          <AccordionTrigger className="text-sm font-semibold">Фон (§5)</AccordionTrigger>
          <AccordionContent>
            <div className="space-y-1.5">
              <Label className="text-xs">Способ оценки фона (утечки)</Label>
              <Select value={config.backgroundMode} onValueChange={(v) => upd({ backgroundMode: v as BackgroundMode })}>
                <SelectTrigger className="w-full sm:w-80" aria-label="Способ оценки фона">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {BACKGROUND_MODES.map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.label} — {m.desc}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">
                {BACKGROUND_MODES.find((m) => m.value === config.backgroundMode)?.desc}. Весь хвост не обязательно
                является утечкой; blank измеряется в согласованной конфигурации.
              </p>
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* ============================== Допуск ============================== */}
        <AccordionItem value="tail">
          <AccordionTrigger className="text-sm font-semibold">Допуск хвоста и сходимости (§9)</AccordionTrigger>
          <AccordionContent>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
              <NumField
                label="Окон в хвосте N"
                value={config.tailPoints}
                onChange={(v) => upd({ tailPoints: Math.max(1, Math.round(v)) })}
                step="1"
                min={1}
              />
              <NumField
                label="Предел тока I_lim, А"
                value={config.tailLimit_A}
                onChange={(v) => upd({ tailLimit_A: v })}
                step="any"
                hint="критерий |B|+k·u(B) ≤ I_lim"
              />
              <NumField
                label="Предел наклона S_lim, А/с"
                value={config.slopeLimit_A_s}
                onChange={(v) => upd({ slopeLimit_A_s: v })}
                step="any"
                hint="критерий |S|+k·u(S) ≤ S_lim"
              />
              <NumField
                label="Коэффициент k"
                value={config.kFactor}
                onChange={(v) => upd({ kFactor: v })}
                step="0.01"
                hint="при k=1.96 нормальная интерпретация лишь приближённая"
              />
              <NumField
                label="Допуск сходимости, %"
                value={config.convergencePct}
                onChange={(v) => upd({ convergencePct: v })}
                step="0.1"
                hint="сходимость заряда при удлинении записи"
              />
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* ============================== Пропуски и модель ============================== */}
        <AccordionItem value="gaps">
          <AccordionTrigger className="text-sm font-semibold">Пропуски и модель (qscv_final)</AccordionTrigger>
          <AccordionContent>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <div className="space-y-1.5">
                <Label className="text-xs">Обработка пропусков</Label>
                <Select value={config.gapMode} onValueChange={(v) => upd({ gapMode: v as GapMode })}>
                  <SelectTrigger className="w-full" aria-label="Обработка пропусков">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="reject">reject — пропуск помечается</SelectItem>
                    <SelectItem value="bound">bound — граница по току</SelectItem>
                    <SelectItem value="model">model — интегрирование модели</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-[11px] leading-snug text-muted-foreground">
                  {config.gapMode === "reject" && "пропуски помечаются GAPS_UNACCOUNTED; Q остаётся только зарегистрированным"}
                  {config.gapMode === "bound" && "|Qgap| ≤ Ibound·Tgap — граница, не исправление"}
                  {config.gapMode === "model" && "интегрирование экспоненциальной модели по пропускам — модельно зависимо"}
                </p>
              </div>
              <NumField
                label="Граница тока пропуска, А"
                value={config.gapCurrentBound_A}
                onChange={(v) => upd({ gapCurrentBound_A: v })}
                step="any"
                hint="для bound: |Qgap| ≤ Ibound·Tgap"
              />
              <div className="space-y-1.5">
                <Label className="text-xs">Модель подгонки</Label>
                <Select value={config.fitModel} onValueChange={(v) => upd({ fitModel: v as FitModel })}>
                  <SelectTrigger className="w-full" aria-label="Модель подгонки">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">none — без подгонки</SelectItem>
                    <SelectItem value="exp1">exp1 — одна экспонента</SelectItem>
                    <SelectItem value="exp2">exp2 — две экспоненты</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-[11px] text-muted-foreground">
                  i(t)=B+Σ A·m e^(−t/τ_m); подгонка локальная, без гарантии глобального решения
                </p>
              </div>
              <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2.5">
                <div>
                  <div className="text-sm font-medium">Стоп при непрошедшем хвосте</div>
                  <div className="text-[11px] text-muted-foreground">остановить последовательность (TAIL_NOT_SETTLED)</div>
                </div>
                <Switch
                  checked={config.stopOnTailFail}
                  onCheckedChange={(v) => upd({ stopOnTailFail: v })}
                  aria-label="Стоп при непрошедшем хвосте"
                />
              </div>
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* ============================== Неопределённость ============================== */}
        <AccordionItem value="uncertainty">
          <AccordionTrigger className="text-sm font-semibold">Неопределённость (§12)</AccordionTrigger>
          <AccordionContent>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <NumField
                label="u ток общий, А"
                value={config.uCurrentCommon_A}
                onChange={(v) => upd({ uCurrentCommon_A: v })}
                step="any"
                hint="общий offset-член тока (не усредняется)"
              />
              <NumField
                label="u фон общий, А"
                value={config.uLeakCommon_A}
                onChange={(v) => upd({ uLeakCommon_A: v })}
                step="any"
                hint="общий член неопределённости фона"
              />
              <NumField
                label="u тактовая, отн."
                value={config.uClockRel}
                onChange={(v) => upd({ uClockRel: v })}
                step="any"
                hint="относительная неопределённость длительностей окон"
              />
              <NumField
                label="u(ΔV_DUT), В"
                value={config.uDv_V}
                onChange={(v) => upd({ uDv_V: v })}
                step="any"
                hint="неопределённость действительного приращения напряжения DUT"
              />
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* ============================== Служебные циклы ============================== */}
        <AccordionItem value="housekeeping">
          <AccordionTrigger className="text-sm font-semibold">Служебные циклы (§5)</AccordionTrigger>
          <AccordionContent>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2.5">
                <div>
                  <div className="text-sm font-medium">Autozero</div>
                  <div className="text-[11px] text-muted-foreground">сверять по SCPI Command Reference</div>
                </div>
                <Switch checked={config.autozero} onCheckedChange={(v) => upd({ autozero: v })} aria-label="Autozero" />
              </div>
              <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2.5">
                <div>
                  <div className="text-sm font-medium">Выходной фильтр</div>
                  <div className="text-[11px] text-muted-foreground">:OUTP:FILT</div>
                </div>
                <Switch
                  checked={config.outputFilter}
                  onCheckedChange={(v) => upd({ outputFilter: v })}
                  aria-label="Выходной фильтр"
                />
              </div>
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* ============================== Шумовая модель SMU ============================== */}
        <AccordionItem value="noise">
          <AccordionTrigger className="text-sm font-semibold">Шумовая модель SMU (симуляция)</AccordionTrigger>
          <AccordionContent>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <NumField
                label="u смещения шума, А"
                value={config.uIOffset_A}
                onChange={(v) => upd({ uIOffset_A: v })}
                step="any"
                hint="белый шум на апертуру: u_i = uIOffset + uIGainRel·|I|"
              />
              <NumField
                label="u усиления, отн."
                value={config.uIGainRel}
                onChange={(v) => upd({ uIGainRel: v })}
                step="any"
              />
              <NumField
                label="Систематический offset, А"
                value={config.iOffset_A}
                onChange={(v) => upd({ iOffset_A: v })}
                step="any"
                hint="постоянное смещение прибора — ловится blank-измерением"
              />
            </div>
          </AccordionContent>
        </AccordionItem>
      </Accordion>

      {/* ============================== Модель DUT ============================== */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Модель DUT (симуляция)</CardTitle>
          <CardDescription>
            Пассивная линейная модель PHYSICAL_OBSERVER: C0 ∥ G ∥ (R,C)-ветви. Демонстрация, не аттестованная модель
            DUT.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label className="text-xs">Пресет модели</Label>
            <Select value={dutLabel} onValueChange={setDutLabel}>
              <SelectTrigger className="w-full" aria-label="Модель DUT">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(DUT_PRESETS).map(([key, m]) => (
                  <SelectItem key={key} value={key}>
                    {m.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-md border p-3">
              <div className="text-muted-foreground">C0 (быстрая ёмкость)</div>
              <div className="font-mono">{formatFarads(dut.c0_F)}</div>
            </div>
            <div className="rounded-md border p-3">
              <div className="text-muted-foreground">G (проводимость утечки)</div>
              <div className="font-mono">{formatEng(dut.g_S, "См")}</div>
            </div>
            {dut.kind === "mos_demo" && (
              <>
                <div className="rounded-md border p-3">
                  <div className="text-muted-foreground">C_ox / C_min</div>
                  <div className="font-mono">
                    {formatFarads(dut.cOx_F)} / {formatFarads(dut.cMin_F)}
                  </div>
                </div>
                <div className="rounded-md border p-3">
                  <div className="text-muted-foreground">V_fb / ширина перехода</div>
                  <div className="font-mono">
                    {formatEng(dut.vfb_V, "В")} / {formatEng(dut.width_V, "В")}
                  </div>
                </div>
              </>
            )}
            {dut.branches.length === 0 && (
              <div className="rounded-md border p-3">
                <div className="text-muted-foreground">Релаксационные ветви</div>
                <div className="font-mono">нет</div>
              </div>
            )}
          </div>
          {dut.branches.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Ветвь</TableHead>
                  <TableHead>C, Ф</TableHead>
                  <TableHead>τ, с</TableHead>
                  <TableHead>R = τ/C, Ом</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {dut.branches.map((b, i) => (
                  <TableRow key={i}>
                    <TableCell className="text-xs">{i + 1}</TableCell>
                    <TableCell className="font-mono text-xs">{formatFarads(b.c_F)}</TableCell>
                    <TableCell className="font-mono text-xs">{formatSeconds(b.tau_s)}</TableCell>
                    <TableCell className="font-mono text-xs">{formatEng(b.tau_s / b.c_F, "Ом")}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          <p className="text-[11px] text-muted-foreground">
            {dut.kind === "mos_demo"
              ? "Нелинейная Q_fast(V) (сигмоид C(V)) — только помеченная демонстрационная модель; методика §3: C_qs = dQ_eq/dV."
              : "Линейная пассивная схема; истинное разложение заряда доступно в результатах (разделение §13)."}
          </p>
        </CardContent>
      </Card>

      {/* ============================== Пресеты ============================== */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Пресеты конфигурации</CardTitle>
          <CardDescription>Быстрая загрузка типовых протоколов (текущие значения будут заменены)</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          <Button
            variant="outline"
            onClick={() => {
              setConfig(DEFAULT_CONFIG);
              setDutLabel("demo_qscv");
              toast.success("Загружен пресет: протокол B — демо qscv_final");
            }}
          >
            <Settings2 className="h-4 w-4" />
            Протокол B — демо qscv_final
          </Button>
          <Button
            variant="outline"
            onClick={() => {
              setConfig({
                ...DEFAULT_CONFIG,
                protocol: "kuhn_staircase",
                vStart_V: -2,
                vEnd_V: 2,
                vStep_V: 0.01,
                directions: ["forward", "reverse"],
                cycles: 1,
                speedMode: "manual",
                apertureMode: "nplc",
                // Апертура должна накрывать период ступени: NPLC 5 @ 50 Гц = 100 мс = T,
                // иначе C = |I/(ΔV/T)| завышается в T/T_ап раз (методика §3 — одиночное
                // измерение внутри ступени не делится на ΔV/dwell без проверки).
                nplc: 5,
                trigInterval_s: 0.1,
                trigCount: 1,
              });
              setDutLabel("demo_qscv");
              toast.success("Загружен пресет: протокол A — линейная развёртка (r = 0.1 В/с)");
            }}
          >
            Протокол A — линейная развёртка (SCPI-пример)
          </Button>
          <Button
            variant="outline"
            onClick={() => {
              setConfig({
                ...DEFAULT_CONFIG,
                vStart_V: -0.2,
                vEnd_V: 0.2,
                vStep_V: 0.01,
                trigCount: 60,
                trigInterval_s: 0.001,
                aperture_s: 0.0008,
                backgroundMode: "none",
              });
              setDutLabel("mos_demo");
              toast.success("Загружен пресет: MOS-демо (протокол B)");
            }}
          >
            MOS-демо (протокол B)
          </Button>
        </CardContent>
      </Card>

      {/* ============================== Сохранение ============================== */}
      <Card>
        <CardContent className="flex flex-col gap-3 pt-6 sm:flex-row sm:items-center sm:justify-between">
          <div className="text-xs text-muted-foreground">
            Протокол: {config.protocol === "aperture_charge" ? "B — зарядовый (апертуры)" : "A — линейная развёртка (Кун)"} ·
            апертура {formatSeconds(aperture.seconds)} · интервал {formatSeconds(config.trigInterval_s)} · compliance{" "}
            {formatAmps(config.iCompliance_A)}
          </div>
          <Button onClick={onSave} disabled={saving || busy !== null}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Сохранить конфигурацию
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
