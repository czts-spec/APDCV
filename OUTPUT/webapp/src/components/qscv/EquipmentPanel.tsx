"use client";
/**
 * Панель «Оборудование» — методика §4.
 * Паспорт прибора (InstrumentPassport), таблица подключения B2910BL ↔ DUT,
 * чек-лист подготовки, предупреждения безопасности, команды проверки.
 */
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, Check, Copy, Loader2, Save, ShieldAlert } from "lucide-react";
import { useQscvStore } from "@/lib/store";
import { api } from "@/lib/qscv/api-client";
import type { InstrumentPassport } from "@/lib/qscv/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Separator } from "@/components/ui/separator";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { copyToClipboard, PanelHeader } from "./shared";

const WIRING_ROWS: { b2910: string; dut: string }[] = [
  { b2910: "Force HI", dut: "Затвор" },
  { b2910: "Sense HI", dut: "Затвор (при 4-проводной схеме)" },
  { b2910: "Force LO", dut: "Подложка / общий контакт" },
  { b2910: "Sense LO", dut: "Подложка (при 4-проводной схеме)" },
  { b2910: "Guard", dut: "Экран кабеля; НЕ соединять с Force/Sense LO" },
];

const CHECKLIST: { id: string; text: string }[] = [
  { id: "passport", text: "Записать модель, серийный номер, прошивку, дату калибровки и температуру" },
  { id: "warmup", text: "Прогрев 60 минут и self-calibration (*CAL?)" },
  { id: "cables", text: "Чистые контакты, короткие неподвижные кабели, экранирование, воспроизводимое положение образца" },
  { id: "dut-conditions", text: "Задать температуру, освещение, площадь и структуру DUT" },
  { id: "four-wire", text: "Четырёхпроводная схема (Force/Sense HI и LO подключены к DUT)" },
  { id: "discharge", text: "При выключенном выходе — проверить отсутствие внешнего заряда и безопасный разряд" },
];

const CHECK_COMMANDS: { cmd: string; comment: string }[] = [
  { cmd: "*IDN?", comment: "идентификация: производитель, модель, серийный номер, версия прошивки" },
  { cmd: "*TST?", comment: "self-test прибора" },
  { cmd: "*CAL?", comment: "self-calibration — после 60 минут прогрева" },
  { cmd: ":SENS:REM ON", comment: "четырёхпроводная схема (remote sense)" },
];

export default function EquipmentPanel() {
  const instrument = useQscvStore((s) => s.instrument);
  const setInstrument = useQscvStore((s) => s.setInstrument);
  const busy = useQscvStore((s) => s.busy);
  const [saving, setSaving] = useState(false);
  const [checked, setChecked] = useState<Record<string, boolean>>({});

  const checkedCount = useMemo(() => CHECKLIST.filter((c) => checked[c.id]).length, [checked]);

  const upd = (patch: Partial<InstrumentPassport>) => setInstrument({ ...instrument, ...patch });

  const onSave = async () => {
    setSaving(true);
    try {
      await api.saveInstrument(instrument);
      toast.success("Паспорт прибора сохранён");
    } catch (e) {
      toast.error(`Не удалось сохранить паспорт: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const saveDisabled = saving || busy !== null;

  return (
    <div className="space-y-4">
      <PanelHeader
        title="Оборудование"
        subtitle="Методика §4: паспорт прибора, подключение, прогрев, self-calibration и проверки перед измерением"
      />

      {/* Предупреждение безопасности §4 */}
      <Alert variant="destructive">
        <ShieldAlert />
        <AlertTitle>Предельное напряжение и ток (§4)</AlertTitle>
        <AlertDescription>
          Предельное напряжение B2910BL не является разрешённым напряжением тонкого оксида. Ток compliance выбирают по
          DUT, а не по максимальной возможности SMU.
        </AlertDescription>
      </Alert>

      {/* Паспорт прибора */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Паспорт прибора</CardTitle>
          <CardDescription>
            Данные экземпляра SMU и условия измерения; сохраняются в базе и попадают в метаданные журнала (§13)
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <div className="space-y-1.5">
              <Label className="text-xs" htmlFor="eq-model">Модель</Label>
              <Input id="eq-model" value={instrument.model} onChange={(e) => upd({ model: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs" htmlFor="eq-serial">Серийный номер</Label>
              <Input
                id="eq-serial"
                placeholder="MY12345678"
                value={instrument.serialNumber}
                onChange={(e) => upd({ serialNumber: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs" htmlFor="eq-fw">Прошивка</Label>
              <Input
                id="eq-fw"
                placeholder="например, 1.8.0"
                value={instrument.firmware}
                onChange={(e) => upd({ firmware: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs" htmlFor="eq-cal">Дата калибровки</Label>
              <Input
                id="eq-cal"
                type="date"
                value={instrument.calibrationDate}
                onChange={(e) => upd({ calibrationDate: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs" htmlFor="eq-temp">Температура, °C</Label>
              <Input
                id="eq-temp"
                type="number"
                step="0.1"
                value={instrument.temperatureC ?? ""}
                onChange={(e) => {
                  const v = parseFloat(e.target.value);
                  upd({ temperatureC: Number.isFinite(v) ? v : null });
                }}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs" htmlFor="eq-warmup">Прогрев до self-cal, мин</Label>
              <Input
                id="eq-warmup"
                type="number"
                step="1"
                min={0}
                value={instrument.warmupMinutes}
                onChange={(e) => {
                  const v = parseFloat(e.target.value);
                  if (Number.isFinite(v)) upd({ warmupMinutes: v });
                }}
              />
              <p className="text-[11px] leading-snug text-muted-foreground">
                User&apos;s Guide: 60 минут прогрева перед *CAL?
              </p>
            </div>
          </div>

          <Separator />

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2.5">
              <div>
                <div className="text-sm font-medium">Self-test (*TST?)</div>
                <div className="text-[11px] text-muted-foreground">выполнен и пройден</div>
              </div>
              <Switch
                checked={instrument.selfTestDone}
                onCheckedChange={(v) => upd({ selfTestDone: v })}
                aria-label="Self-test выполнен"
              />
            </div>
            <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2.5">
              <div>
                <div className="text-sm font-medium">Self-calibration (*CAL?)</div>
                <div className="text-[11px] text-muted-foreground">после 60 мин прогрева</div>
              </div>
              <Switch
                checked={instrument.selfCalDone}
                onCheckedChange={(v) => upd({ selfCalDone: v })}
                aria-label="Self-calibration выполнена"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Схема подключения</Label>
              <Select value={instrument.wiring} onValueChange={(v) => upd({ wiring: v as "2w" | "4w" })}>
                <SelectTrigger className="w-full" aria-label="Схема подключения">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="4w">4-проводная (Force/Sense HI и LO, :SENS:REM ON)</SelectItem>
                  <SelectItem value="2w">2-проводная</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs" htmlFor="eq-notes">Примечания: кабели, экранирование, guard, оснастка</Label>
            <Textarea
              id="eq-notes"
              rows={2}
              placeholder="Триаксиальный кабель, длина …, экран …, fixture …, воспроизводимое положение образца"
              value={instrument.notes}
              onChange={(e) => upd({ notes: e.target.value })}
            />
          </div>

          <div className="flex justify-end">
            <Button onClick={onSave} disabled={saveDisabled}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Сохранить паспорт
            </Button>
          </div>
          <p className="text-[11px] text-muted-foreground">
            Self-calibration не заменяет сервисную калибровку или характеристику внешней оснастки.
          </p>
        </CardContent>
      </Card>

      {/* Подключение */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Подключение B2910BL к DUT (MOS-структура)</CardTitle>
          <CardDescription>
            Четырёхпроводная схема устраняет падение напряжения на токовых кабелях; guard снижает паразитные токи утечки
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-1/3">B2910BL</TableHead>
                <TableHead>DUT (MOS)</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {WIRING_ROWS.map((r) => (
                <TableRow key={r.b2910}>
                  <TableCell className="font-mono text-xs">{r.b2910}</TableCell>
                  <TableCell className="text-xs">
                    {r.dut}
                    {r.b2910 === "Guard" && (
                      <Badge variant="outline" className="ml-2 border-red-500/60 text-[10px] text-red-600 dark:text-red-400">
                        не соединять с LO
                      </Badge>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Чек-лист §4 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Чек-лист перед измерением (§4)</CardTitle>
          <CardDescription>
            Отметьте выполненные пункты: {checkedCount} из {CHECKLIST.length}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {CHECKLIST.map((c) => (
            <label
              key={c.id}
              className="flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2.5 text-sm transition-colors hover:bg-accent/50"
            >
              <Checkbox
                checked={!!checked[c.id]}
                onCheckedChange={(v) => setChecked((prev) => ({ ...prev, [c.id]: v === true }))}
                className="mt-0.5"
              />
              <span className={checked[c.id] ? "text-muted-foreground line-through" : ""}>{c.text}</span>
            </label>
          ))}
        </CardContent>
      </Card>

      {/* Команды проверки */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Команды проверки</CardTitle>
          <CardDescription>
            Копирование в буфер обмена для отправки прибору через VISA/SCPI-терминал. Источник: B2900B/BL Programming
            Guide / User&apos;s Guide
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {CHECK_COMMANDS.map((c) => (
            <div
              key={c.cmd}
              className="flex flex-col gap-2 rounded-md border px-3 py-2 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0">
                <code className="block break-all font-mono text-xs font-semibold">{c.cmd}</code>
                <p className="mt-0.5 text-[11px] text-muted-foreground">{c.comment}</p>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="shrink-0"
                onClick={async () => {
                  const ok = await copyToClipboard(c.cmd);
                  if (ok) toast.success(`Скопировано: ${c.cmd}`);
                  else toast.error("Не удалось скопировать в буфер обмена");
                }}
              >
                <Copy className="h-3.5 w-3.5" />
                Копировать
              </Button>
            </div>
          ))}
          <Alert>
            <AlertTriangle />
            <AlertDescription>
              Точные параметры, диапазоны и поведение команд сверять с B2900B/BL Programming Guide и SCPI Command
              Reference для установленной версии прошивки.
            </AlertDescription>
          </Alert>
          {instrument.selfCalDone && (
            <div className="flex items-center gap-2 text-xs text-emerald-700 dark:text-emerald-400">
              <Check className="h-3.5 w-3.5" />
              Self-calibration отмечена выполненной {instrument.warmupMinutes >= 60 ? "(прогрев ≥ 60 мин)" : "(прогрев менее 60 мин — проверьте §4)"}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
