"use client";
/**
 * Панель «Верификация» — методика §6.
 * Проверка временного режима измерения на эталонных C и RC до подключения DUT:
 * восстановление заряда C·ΔV в допуске, реальные границы окон, мёртвое время.
 */
import { useState } from "react";
import { toast } from "sonner";
import { CheckCircle2, FlaskConical, Info, Loader2, Quote, XCircle } from "lucide-react";
import { useQscvStore, defaultVerificationRequest } from "@/lib/store";
import { api } from "@/lib/qscv/api-client";
import type { VerificationRequest } from "@/lib/qscv/types";
import { formatAmps, formatCoulombs, formatEng, formatFarads, formatSeconds } from "@/lib/qscv/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Separator } from "@/components/ui/separator";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { NumField, OkBadge, PanelHeader, TimingDiagram, ScrollBox } from "./shared";

export default function VerificationPanel() {
  const busy = useQscvStore((s) => s.busy);
  const setBusy = useQscvStore((s) => s.setBusy);
  const verification = useQscvStore((s) => s.verification);
  const setVerification = useQscvStore((s) => s.setVerification);

  const [req, setReq] = useState<VerificationRequest>(defaultVerificationRequest());
  const [running, setRunning] = useState(false);

  const upd = (patch: Partial<VerificationRequest>) => setReq({ ...req, ...patch });
  const updTiming = (patch: Partial<VerificationRequest["timing"]>) =>
    setReq({ ...req, timing: { ...req.timing, ...patch } });

  const tau = req.rRef_ohm * req.cRef_F;

  const onVerify = async () => {
    setRunning(true);
    setBusy("verify");
    try {
      const res = await api.verify(req);
      setVerification(res);
      if (res.pass) toast.success("Проверка пройдена: заряд восстановлен в допуске");
      else toast.error("Проверка НЕ пройдена: заряд вне допуска");
    } catch (e) {
      toast.error(`Ошибка проверки: ${(e as Error).message}`);
    } finally {
      setRunning(false);
      setBusy(null);
    }
  };

  const busyAny = running || busy !== null;

  return (
    <div className="space-y-4">
      <PanelHeader
        title="Верификация"
        subtitle="Методика §6: проверка временно́го режима на эталонном C или RC до измерения DUT — восстановление заряда C·ΔV"
      />

      {/* Параметры проверки */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Эталон и параметры проверки</CardTitle>
          <CardDescription>
            Эталон подключается вместо DUT; шаг ΔV и тайминг должны совпадать с рабочими настройками измерения
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div className="space-y-1.5">
              <Label className="text-xs">Тип эталона</Label>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant={req.refType === "c" ? "default" : "outline"}
                  size="sm"
                  onClick={() => upd({ refType: "c" })}
                >
                  C (конденсатор)
                </Button>
                <Button
                  type="button"
                  variant={req.refType === "rc" ? "default" : "outline"}
                  size="sm"
                  onClick={() => upd({ refType: "rc" })}
                >
                  RC
                </Button>
              </div>
            </div>
            <NumField
              label="C эталона, Ф"
              value={req.cRef_F}
              onChange={(v) => upd({ cRef_F: v })}
              step="any"
            />
            {req.refType === "rc" && (
              <NumField
                label="R эталона, Ом"
                value={req.rRef_ohm}
                onChange={(v) => upd({ rRef_ohm: v })}
                step="any"
                hint={`τ = R·C = ${formatSeconds(tau)}`}
              />
            )}
            <NumField label="Шаг ΔV, В" value={req.dv_V} onChange={(v) => upd({ dv_V: v })} step="any" />
          </div>

          <Separator />

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
            <NumField
              label="Интервал, с"
              value={req.timing.interval_s}
              onChange={(v) => updTiming({ interval_s: v })}
              step="any"
            />
            <NumField
              label="Апертура, с"
              value={req.timing.aperture_s}
              onChange={(v) => updTiming({ aperture_s: v })}
              step="any"
            />
            <NumField
              label="Задержка источника, с"
              value={req.timing.tranDelay_s}
              onChange={(v) => updTiming({ tranDelay_s: v })}
              step="any"
            />
            <NumField
              label="Задержка измерения, с"
              value={req.timing.acqDelay_s}
              onChange={(v) => updTiming({ acqDelay_s: v })}
              step="any"
            />
            <NumField
              label="Длительность перехода, с"
              value={req.timing.tranTime_s}
              onChange={(v) => updTiming({ tranTime_s: v })}
              step="any"
            />
            <NumField
              label="Число апертур"
              value={req.timing.count}
              onChange={(v) => updTiming({ count: Math.max(1, Math.round(v)) })}
              step="1"
              min={1}
            />
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <NumField
              label="Допуск восстановления, %"
              value={req.tolerancePct}
              onChange={(v) => upd({ tolerancePct: v })}
              step="0.1"
            />
            <NumField
              label="Шум тока, А"
              value={req.noise_A}
              onChange={(v) => upd({ noise_A: v })}
              step="any"
              hint="модель шума прибора при проверке"
            />
          </div>

          <div className="flex flex-col gap-2 sm:flex-row">
            <Button onClick={onVerify} disabled={busyAny}>
              {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <FlaskConical className="h-4 w-4" />}
              Выполнить проверку
            </Button>
            <Button
              variant="outline"
              disabled={busyAny}
              onClick={() => {
                const newDelay = 5 * req.timing.tranTime_s + req.timing.interval_s;
                updTiming({ acqDelay_s: newDelay });
                toast.info(
                  `Установлена задержка измерения ${formatSeconds(newDelay)} — демонстрация потери начала импульса (§6)`,
                );
              }}
            >
              Начало пропущено (демо)
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Результат */}
      {verification ? (
        <>
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <CardTitle className="text-sm">Результат проверки</CardTitle>
                  <CardDescription>
                    Эталон: {verification.pass ? "заряд восстановлен" : "заряд НЕ восстановлен"} на известной цепи
                  </CardDescription>
                </div>
                {verification.pass ? (
                  <Badge className="border-emerald-500/60 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    Заряд восстановлен в допуске ±{verification.tolerancePct}%
                  </Badge>
                ) : (
                  <Badge className="border-red-500/60 bg-red-500/10 text-red-700 dark:text-red-400">
                    <XCircle className="h-3.5 w-3.5" />
                    ВНЕ ДОПУСКА (±{verification.tolerancePct}%)
                  </Badge>
                )}
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-5">
                <div className="rounded-md border p-3">
                  <div className="text-[11px] text-muted-foreground">Q ожидаемый = C·ΔV</div>
                  <div className="font-mono text-sm">{formatCoulombs(verification.qExpected_C)}</div>
                </div>
                <div className="rounded-md border p-3">
                  <div className="text-[11px] text-muted-foreground">Q восстановленный = ΔQ_obs</div>
                  <div className="font-mono text-sm">{formatCoulombs(verification.qRecovered_C)}</div>
                </div>
                <div className="rounded-md border p-3">
                  <div className="text-[11px] text-muted-foreground">Ошибка, %</div>
                  <div
                    className={`font-mono text-sm ${
                      verification.pass ? "text-emerald-700 dark:text-emerald-400" : "text-red-600 dark:text-red-400"
                    }`}
                  >
                    {verification.errorRelPct.toFixed(2)}
                  </div>
                </div>
                <div className="rounded-md border p-3">
                  <div className="text-[11px] text-muted-foreground">Истина (модель) в окнах</div>
                  <div className="font-mono text-sm">{formatCoulombs(verification.qRegisteredTrue_C)}</div>
                </div>
                <div className="rounded-md border p-3">
                  <div className="text-[11px] text-muted-foreground">Истина (модель) в пропусках</div>
                  <div className="font-mono text-sm">{formatCoulombs(verification.qInGaps_C)}</div>
                </div>
              </div>

              <div className="rounded-md border p-3">
                <TimingDiagram
                  mode="result"
                  interval={verification.timing.interval_s}
                  aperture={verification.timing.aperture_s}
                  tranDelay={verification.timing.tranDelay_s}
                  acqDelay={verification.timing.acqDelay_s}
                  tranTime={verification.timing.tranTime_s}
                  windows={Math.min(5, verification.apertures.length)}
                />
                <p className="mt-1 text-[11px] text-muted-foreground">
                  Первая апертура: [{formatSeconds(verification.timing.firstA_s)}, {formatSeconds(verification.timing.firstB_s)}] ·
                  мёртвое время: {formatSeconds(verification.timing.deadTime_s)} · задержка до первой апертуры:{" "}
                  {formatSeconds(verification.timing.initialGap_s)}
                </p>
              </div>

              {/* Таблица апертур */}
              <div>
                <div className="mb-2 text-xs font-semibold">Апертурные записи ({verification.apertures.length})</div>
                <ScrollBox className="max-h-96">
                  <Table>
                    <TableHeader className="sticky top-0 z-10 bg-card">
                      <TableRow>
                        <TableHead>j</TableHead>
                        <TableHead>a, с</TableHead>
                        <TableHead>b, с</TableHead>
                        <TableHead>длительность</TableHead>
                        <TableHead>Ī, А</TableHead>
                        <TableHead>u_i, А</TableHead>
                        <TableHead>статус</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {verification.apertures.map((ap) => (
                        <TableRow key={ap.j}>
                          <TableCell className="font-mono text-xs">{ap.j}</TableCell>
                          <TableCell className="font-mono text-xs">{formatSeconds(ap.a_s, 3)}</TableCell>
                          <TableCell className="font-mono text-xs">{formatSeconds(ap.b_s, 3)}</TableCell>
                          <TableCell className="font-mono text-xs">{formatSeconds(ap.b_s - ap.a_s, 3)}</TableCell>
                          <TableCell className="font-mono text-xs">{formatAmps(ap.i_avg_A)}</TableCell>
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
                </ScrollBox>
              </div>

              {/* Проверки */}
              <div>
                <div className="mb-2 text-xs font-semibold">Контрольные проверки</div>
                <div className="space-y-1.5">
                  {verification.checks.map((c, i) => (
                    <div
                      key={i}
                      className="flex flex-col gap-1 rounded-md border px-3 py-2 sm:flex-row sm:items-center sm:gap-3"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="text-xs font-medium">{c.label}</div>
                        {c.note && <div className="text-[11px] text-muted-foreground">{c.note}</div>}
                      </div>
                      <div className="font-mono text-xs text-muted-foreground">{c.value}</div>
                      {c.ok === null ? (
                        <Badge variant="outline" className="border-muted-foreground/40 text-muted-foreground">
                          инфо
                        </Badge>
                      ) : (
                        <OkBadge ok={c.ok} okText="ok" failText="fail" />
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Цитата §6 */}
          <Alert>
            <Quote />
            <AlertTitle>Методика §6</AlertTitle>
            <AlertDescription>
              Период таймера не равен апертуре. Частота выдачи отсчётов не характеризует полностью полосу и способность
              измерить короткий пик. Если начало импульса пропущено, поздний нулевой ток не доказывает правильность
              заряда.
            </AlertDescription>
          </Alert>
        </>
      ) : (
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-10 text-center">
            <FlaskConical className="h-8 w-8 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">
              Задайте параметры эталона и выполните проверку — результат появится здесь.
            </p>
          </CardContent>
        </Card>
      )}

      <Alert>
        <Info />
        <AlertDescription>
          Python/VISA-цикл не обеспечивает микросекундной синхронизации; фактические задержки задаёт триггерная модель
          прибора. Проверка выполняется на известном C и RC до подключения DUT.
        </AlertDescription>
      </Alert>

      {verification && req.refType === "rc" && (
        <p className="text-[11px] text-muted-foreground">
          Параметры текущей формы: C = {formatFarads(req.cRef_F)}, R = {formatEng(req.rRef_ohm, "Ом")}, τ ={" "}
          {formatSeconds(tau)} — сверьте с фактическим эталоном.
        </p>
      )}
    </div>
  );
}
