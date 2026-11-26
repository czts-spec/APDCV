"use client";
/**
 * Панель «Анализ данных» — порт офлайн-анализатора qscv_final.py.
 * Загрузка апертурных строк (из прогона / CSV / импорта), параметры анализа,
 * таблицы шагов и пар fwd/rev, ошибки и постоянное предупреждение.
 */
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, Cpu, FileUp, Loader2, Upload } from "lucide-react";
import { useQscvStore, DEFAULT_ANALYZE_OPTIONS } from "@/lib/store";
import { api } from "@/lib/qscv/api-client";
import type { AcquisitionRow, AnalyzeOptions, RunResult } from "@/lib/qscv/types";
import { formatAmps, formatCoulombs, formatFarads, formatSci, formatSeconds, formatVolts } from "@/lib/qscv/format";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { FlagBadge, NumField, OkBadge, PanelHeader, directionLabel, ScrollBox } from "./shared";

const CSV_HEADER =
  "run,direction,step,v_before_V,v_after_V,a_s,b_s,i_A,u_i_A,leak_A,u_leak_ind_A,u_dt_s,pre_settled";

function runToRows(run: RunResult): AcquisitionRow[] {
  const rows: AcquisitionRow[] = [];
  for (const st of run.steps) {
    for (const ap of st.apertures) {
      rows.push({
        run: run.label,
        direction: st.direction,
        step: st.step,
        v_before_V: st.vBefore_V,
        v_after_V: st.vAfter_V,
        a_s: ap.a_s,
        b_s: ap.b_s,
        i_A: ap.i_avg_A,
        u_i_A: ap.u_i_A,
        leak_A: ap.i_background_A,
        u_leak_ind_A: 0,
        u_dt_s: 0,
        pre_settled: st.preSettled,
      });
    }
  }
  return rows;
}

export default function AnalysisPanel() {
  const runs = useQscvStore((s) => s.runs);
  const importedRows = useQscvStore((s) => s.importedRows);
  const setAnalysis = useQscvStore((s) => s.setAnalysis);
  const analysis = useQscvStore((s) => s.analysis);

  const [rows, setRows] = useState<AcquisitionRow[] | null>(null);
  const [rowsSource, setRowsSource] = useState<string>("");
  const [runIdx, setRunIdx] = useState("0");
  const [csvText, setCsvText] = useState("");
  const [importing, setImporting] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [options, setOptions] = useState<AnalyzeOptions>(DEFAULT_ANALYZE_OPTIONS);

  const updOpt = (patch: Partial<AnalyzeOptions>) => setOptions({ ...options, ...patch });

  const preview = useMemo(() => (rows ?? []).slice(0, 8), [rows]);

  const onImportCsv = async (text: string) => {
    const t = text.trim();
    if (!t) {
      toast.error("Пустой CSV: вставьте текст или выберите файл");
      return;
    }
    setImporting(true);
    try {
      const parsed = await api.importJournal(t);
      setRows(parsed);
      setRowsSource("CSV");
      toast.success(`Импортировано строк: ${parsed.length}`);
    } catch (e) {
      toast.error(`Ошибка импорта CSV: ${(e as Error).message}`);
    } finally {
      setImporting(false);
    }
  };

  const onAnalyze = async () => {
    if (!rows || rows.length === 0) {
      toast.error("Нет строк для анализа — загрузите источник данных");
      return;
    }
    setAnalyzing(true);
    try {
      const res = await api.analyze({ rows, options });
      setAnalysis(res);
      const usable = res.steps.filter((s) => s.usableForPair).length;
      toast.success(`Анализ выполнен: шагов ${res.steps.length}, пригодно ${usable}, пар ${res.pairs.length}`);
    } catch (e) {
      toast.error(`Ошибка анализа: ${(e as Error).message}`);
    } finally {
      setAnalyzing(false);
    }
  };

  return (
    <div className="space-y-4">
      <PanelHeader
        title="Анализ данных"
        subtitle="Порт qscv_final.py: офлайн-анализ апертурных записей — фиты, пропуски, пары forward/reverse"
      />

      {/* Источник данных */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Источник апертурных строк</CardTitle>
          <CardDescription>
            Одна строка = одно окно [a_s, b_s] со средним током; обязательный заголовок qscv_final
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-md bg-muted/50 px-3 py-2 font-mono text-[11px] break-all">{CSV_HEADER}</div>

          {/* a) из последнего прогона */}
          {runs.length > 0 && (
            <div className="rounded-md border p-3">
              <div className="mb-2 text-xs font-semibold">Из последнего прогона (симуляция)</div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <Select value={runIdx} onValueChange={setRunIdx}>
                  <SelectTrigger className="w-full sm:w-72" aria-label="Прогон">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {runs.map((r, i) => (
                      <SelectItem key={r.id} value={String(i)}>
                        {r.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  variant="outline"
                  onClick={() => {
                    const r = runs[parseInt(runIdx, 10)] ?? runs[0];
                    const rr = runToRows(r);
                    setRows(rr);
                    setRowsSource(`прогон «${r.label}»`);
                    toast.success(`Загружено строк из прогона: ${rr.length}`);
                  }}
                >
                  <Upload className="h-3.5 w-3.5" />
                  Загрузить в анализатор
                </Button>
              </div>
              <p className="mt-2 text-[11px] text-muted-foreground">
                Преобразование выполняется на клиенте: каждая апертура шага становится строкой (leak_A = фон,
                u_leak_ind_A = 0).
              </p>
            </div>
          )}

          {/* b) импорт CSV */}
          <div className="rounded-md border p-3">
            <div className="mb-2 text-xs font-semibold">Импорт CSV (acquisition.csv)</div>
            <Textarea
              rows={4}
              placeholder={`Вставьте CSV с заголовком:\n${CSV_HEADER}`}
              value={csvText}
              onChange={(e) => setCsvText(e.target.value)}
              className="font-mono text-[11px]"
            />
            <div className="mt-2 flex flex-col gap-2 sm:flex-row">
              <Button variant="outline" onClick={() => onImportCsv(csvText)} disabled={importing}>
                {importing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileUp className="h-3.5 w-3.5" />}
                Импортировать текст
              </Button>
              <div className="flex items-center gap-2">
                <Input
                  type="file"
                  accept=".csv,.txt"
                  aria-label="Файл CSV"
                  className="max-w-64 text-xs"
                  onChange={async (e) => {
                    const f = e.target.files?.[0];
                    if (!f) return;
                    const text = await f.text();
                    setCsvText(text.slice(0, 100000));
                    await onImportCsv(text);
                  }}
                />
              </div>
            </div>
          </div>

          {/* c) из журнала */}
          {importedRows && (
            <div className="rounded-md border p-3">
              <div className="mb-2 text-xs font-semibold">Строки, импортированные на вкладке «Журнал»</div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">Доступно строк: {importedRows.length}</span>
                <Button
                  variant="outline"
                  onClick={() => {
                    setRows(importedRows);
                    setRowsSource("журнал (импорт)");
                    toast.success(`Загружено строк из журнала: ${importedRows.length}`);
                  }}
                >
                  Использовать
                </Button>
              </div>
            </div>
          )}

          {/* статус загрузки */}
          {rows && rows.length > 0 && (
            <div>
              <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                <span className="font-semibold">Загружено: {rows.length} строк</span>
                <span className="text-muted-foreground">источник: {rowsSource}</span>
              </div>
              <div className="max-h-48 overflow-y-auto rounded-md border">
                <Table>
                  <TableHeader className="sticky top-0 z-10 bg-card">
                    <TableRow>
                      <TableHead>run</TableHead>
                      <TableHead>dir</TableHead>
                      <TableHead>step</TableHead>
                      <TableHead>v до, В</TableHead>
                      <TableHead>v после, В</TableHead>
                      <TableHead>a, с</TableHead>
                      <TableHead>b, с</TableHead>
                      <TableHead>i, А</TableHead>
                      <TableHead>pre_settled</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {preview.map((r, i) => (
                      <TableRow key={i}>
                        <TableCell className="max-w-32 truncate font-mono text-[11px]">{r.run}</TableCell>
                        <TableCell className="text-[11px]">{r.direction}</TableCell>
                        <TableCell className="font-mono text-[11px]">{r.step}</TableCell>
                        <TableCell className="font-mono text-[11px]">{r.v_before_V.toFixed(3)}</TableCell>
                        <TableCell className="font-mono text-[11px]">{r.v_after_V.toFixed(3)}</TableCell>
                        <TableCell className="font-mono text-[11px]">{formatSci(r.a_s, 2)}</TableCell>
                        <TableCell className="font-mono text-[11px]">{formatSci(r.b_s, 2)}</TableCell>
                        <TableCell className="font-mono text-[11px]">{formatAmps(r.i_A, 2)}</TableCell>
                        <TableCell className="text-[11px]">{r.pre_settled ? "true" : "false"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              {rows.length > preview.length && (
                <p className="mt-1 text-[11px] text-muted-foreground">Показаны первые {preview.length} строк из {rows.length}.</p>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Параметры анализа */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Параметры анализа (qscv_final)</CardTitle>
          <CardDescription>
            Модель, обработка пропусков, критерии хвоста и члены неопределённости — как флаги qscv_final.py
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div className="space-y-1.5">
              <Label className="text-xs">Модель</Label>
              <Select value={options.model} onValueChange={(v) => updOpt({ model: v as AnalyzeOptions["model"] })}>
                <SelectTrigger className="w-full" aria-label="Модель">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">none — без подгонки</SelectItem>
                  <SelectItem value="exp1">exp1</SelectItem>
                  <SelectItem value="exp2">exp2</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Пропуски</Label>
              <Select value={options.gapMode} onValueChange={(v) => updOpt({ gapMode: v as AnalyzeOptions["gapMode"] })}>
                <SelectTrigger className="w-full" aria-label="Обработка пропусков">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="reject">reject</SelectItem>
                  <SelectItem value="bound">bound</SelectItem>
                  <SelectItem value="model">model</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <NumField
              label="Граница тока пропуска, А"
              value={options.gapCurrentBound_A}
              onChange={(v) => updOpt({ gapCurrentBound_A: v })}
              step="any"
              hint="bound: |Qgap| ≤ Ibound·Tgap"
            />
            <NumField
              label="Окон в хвосте N"
              value={options.tailPoints}
              onChange={(v) => updOpt({ tailPoints: Math.max(1, Math.round(v)) })}
              step="1"
              min={1}
            />
            <NumField
              label="Предел тока I_lim, А"
              value={options.tailLimit_A}
              onChange={(v) => updOpt({ tailLimit_A: v })}
              step="any"
            />
            <NumField
              label="Предел наклона S_lim, А/с"
              value={options.slopeLimit_A_s}
              onChange={(v) => updOpt({ slopeLimit_A_s: v })}
              step="any"
            />
            <NumField label="Коэффициент k" value={options.kFactor} onChange={(v) => updOpt({ kFactor: v })} step="0.01" />
            <NumField
              label="u ток общий, А"
              value={options.uCurrentCommon_A}
              onChange={(v) => updOpt({ uCurrentCommon_A: v })}
              step="any"
            />
            <NumField
              label="u фон общий, А"
              value={options.uLeakCommon_A}
              onChange={(v) => updOpt({ uLeakCommon_A: v })}
              step="any"
            />
            <NumField
              label="u тактовая, отн."
              value={options.uClockRel}
              onChange={(v) => updOpt({ uClockRel: v })}
              step="any"
            />
            <NumField
              label="u(ΔV_DUT), В"
              value={options.uDv_V}
              onChange={(v) => updOpt({ uDv_V: v })}
              step="any"
            />
          </div>
          <div className="mt-4">
            <Button onClick={onAnalyze} disabled={analyzing || !rows || rows.length === 0}>
              {analyzing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Cpu className="h-4 w-4" />}
              Анализировать
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Результаты */}
      {analysis && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Шаги (steps.csv)</CardTitle>
              <CardDescription>
                Заряды по шагам: наблюдённый, модельный по пропускам, граница, скорректированный и секущая
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ScrollBox className="max-h-96">
                <Table>
                  <TableHeader className="sticky top-0 z-10 bg-card">
                    <TableRow>
                      <TableHead>run</TableHead>
                      <TableHead>напр.</TableHead>
                      <TableHead>шаг</TableHead>
                      <TableHead>V серед</TableHead>
                      <TableHead>Q_obs</TableHead>
                      <TableHead>u(Q)</TableHead>
                      <TableHead>Q_gap модель</TableHead>
                      <TableHead>Q_gap граница</TableHead>
                      <TableHead>Q корр</TableHead>
                      <TableHead>C_sec</TableHead>
                      <TableHead>u(C)</TableHead>
                      <TableHead>B хвоста</TableHead>
                      <TableHead>S хвоста</TableHead>
                      <TableHead>хвост</TableHead>
                      <TableHead>pre</TableHead>
                      <TableHead>пропуски, с</TableHead>
                      <TableHead>годен</TableHead>
                      <TableHead>флаги</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {analysis.steps.map((s, i) => (
                      <TableRow key={`${s.run}-${s.direction}-${s.step}-${i}`}>
                        <TableCell className="max-w-28 truncate font-mono text-[11px]">{s.run}</TableCell>
                        <TableCell className="text-[11px]">{directionLabel(s.direction)}</TableCell>
                        <TableCell className="font-mono text-xs">{s.step}</TableCell>
                        <TableCell className="font-mono text-xs">{formatVolts(s.vMid_V, 3)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatCoulombs(s.qObserved_C)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatCoulombs(s.uQ_C)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatCoulombs(s.qGapModel_C)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatCoulombs(s.qGapBound_C)}</TableCell>
                        <TableCell className="bg-emerald-500/10 font-mono text-xs">{formatCoulombs(s.qCorrected_C)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatFarads(s.cSec_F)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatFarads(s.uC_F)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatAmps(s.tailCurrent_A, 2)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatSci(s.tailSlope_A_s, 2)}</TableCell>
                        <TableCell>
                          <OkBadge ok={s.tailSettled} okText="ok" failText="нет" />
                        </TableCell>
                        <TableCell>
                          <OkBadge ok={s.preSettled} okText="да" failText="нет" />
                        </TableCell>
                        <TableCell className="font-mono text-xs">{formatSeconds(s.gapDuration_s, 2)}</TableCell>
                        <TableCell>
                          <OkBadge ok={s.usableForPair} okText="да" failText="нет" />
                        </TableCell>
                        <TableCell>
                          <div className="flex max-w-48 flex-wrap gap-1">
                            {s.flags.length === 0 && <span className="text-xs text-muted-foreground">—</span>}
                            {s.flags.map((f) => (
                              <FlagBadge key={f} flag={f} />
                            ))}
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ScrollBox>
            </CardContent>
          </Card>

          {/* Детали подгонки экспоненциальной модели (fits.json) */}
          {analysis.steps.some((s) => s.fit) && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Подгонка экспоненциальной модели (fits.json)</CardTitle>
                <CardDescription>
                  i(t) = B + Σ A_m·e^(−t/τ_m); компонентный заряд A·τ не доказывает механизм ловушек (§9).
                  Постоянный B не интегрируется до бесконечности; оставшийся хвост записан, но не прибавляется.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <ScrollBox className="max-h-72">
                  <Table>
                    <TableHeader className="sticky top-0 z-10 bg-card">
                      <TableRow>
                        <TableHead>шаг</TableHead>
                        <TableHead>модель</TableHead>
                        <TableHead>B, А</TableHead>
                        <TableHead>τ_m, с</TableHead>
                        <TableHead>A_m, А</TableHead>
                        <TableHead>заряд A·τ, Кл</TableHead>
                        <TableHead>red. χ²</TableHead>
                        <TableHead>обусловл.</TableHead>
                        <TableHead>сход.</TableHead>
                        <TableHead>ост. хвост, Кл</TableHead>
                        <TableHead>u(Qgap), Кл</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {analysis.steps
                        .filter((s) => s.fit)
                        .map((s, i) => (
                          <TableRow key={`fit-${s.run}-${s.direction}-${s.step}-${i}`}>
                            <TableCell className="font-mono text-xs">
                              {s.run}/{s.direction}/{s.step}
                            </TableCell>
                            <TableCell className="text-xs">{s.fit!.model}</TableCell>
                            <TableCell className="font-mono text-xs">{formatAmps(s.fit!.baseline_A, 2)}</TableCell>
                            <TableCell className="font-mono text-xs">
                              {s.fit!.components.map((c) => formatSeconds(c.tau_s, 2)).join(" · ")}
                            </TableCell>
                            <TableCell className="font-mono text-xs">
                              {s.fit!.components.map((c) => formatAmps(c.a_A, 2)).join(" · ")}
                            </TableCell>
                            <TableCell className="font-mono text-xs">
                              {s.fit!.components.map((c) => formatCoulombs(c.q_C)).join(" · ")}
                            </TableCell>
                            <TableCell className="font-mono text-xs">{s.fit!.reducedChi2.toFixed(2)}</TableCell>
                            <TableCell className="font-mono text-xs">
                              {isFinite(s.fit!.condition) ? s.fit!.condition.toExponential(1) : "∞"}
                            </TableCell>
                            <TableCell>
                              <OkBadge ok={s.fit!.success} okText="да" failText="нет" />
                            </TableCell>
                            <TableCell className="font-mono text-xs">{formatCoulombs(s.fit!.remainingTail_C)}</TableCell>
                            <TableCell className="font-mono text-xs">{formatCoulombs(s.fit!.uGapFitOnly_C)}</TableCell>
                          </TableRow>
                        ))}
                    </TableBody>
                  </Table>
                </ScrollBox>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Строгие пары forward/reverse (forward_reverse.csv)</CardTitle>
              <CardDescription>
                Только допущенные QS-шаги одного прогона; reverse интерполируется в области перекрытия без экстраполяции
              </CardDescription>
            </CardHeader>
            <CardContent>
              {analysis.pairs.length === 0 ? (
                <Alert>
                  <AlertTriangle />
                  <AlertDescription>
                    Строгие пары пусты из-за пропусков/отсутствия reverse — это ожидаемо, не ошибка и не нулевой
                    гистерезис.
                  </AlertDescription>
                </Alert>
              ) : (
                <ScrollBox className="max-h-72">
                  <Table>
                    <TableHeader className="sticky top-0 z-10 bg-card">
                      <TableRow>
                        <TableHead>run</TableHead>
                        <TableHead>V серед, В</TableHead>
                        <TableHead>C прямой, Ф</TableHead>
                        <TableHead>C обратный (интерп.), Ф</TableHead>
                        <TableHead>ΔC, Ф</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {analysis.pairs.map((p, i) => (
                        <TableRow key={i}>
                          <TableCell className="max-w-32 truncate font-mono text-[11px]">{p.run}</TableCell>
                          <TableCell className="font-mono text-xs">{formatVolts(p.vMid_V, 3)}</TableCell>
                          <TableCell className="font-mono text-xs">{formatFarads(p.cForward_F)}</TableCell>
                          <TableCell className="font-mono text-xs">{formatFarads(p.cReverseInterpolated_F)}</TableCell>
                          <TableCell className="font-mono text-xs">{formatFarads(p.deltaC_F)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </ScrollBox>
              )}
            </CardContent>
          </Card>

          {analysis.errors.length > 0 && (
            <Alert variant="destructive">
              <AlertTriangle />
              <AlertTitle>Ошибки анализа ({analysis.errors.length})</AlertTitle>
              <AlertDescription>
                <ul className="list-disc space-y-1 pl-4">
                  {analysis.errors.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              </AlertDescription>
            </Alert>
          )}
        </>
      )}

      {!analysis && (
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-10 text-center">
            <Cpu className="h-8 w-8 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">
              Загрузите строки и нажмите «Анализировать» — результаты появятся здесь.
            </p>
          </CardContent>
        </Card>
      )}

      <Alert>
        <AlertTriangle />
        <AlertTitle>Границы применения</AlertTitle>
        <AlertDescription>
          Офлайн-анализ, не драйвер прибора. Ни один флаг не является сертификатом метрологической аттестации. Q_obs и
          модельный Q_gap коррелированы; их u нельзя просто складывать квадратически.
        </AlertDescription>
      </Alert>
    </div>
  );
}
