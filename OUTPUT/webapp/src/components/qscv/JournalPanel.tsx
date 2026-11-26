"use client";
/**
 * Панель «Журнал» — методика §13.
 * Экспорт апертурного журнала (CSV двух форматов), метаданные (JSON), импорт CSV.
 */
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Braces, Download, Eye, Loader2, ScrollText, Upload } from "lucide-react";
import { useQscvStore } from "@/lib/store";
import { api } from "@/lib/qscv/api-client";
import type { JournalFormat, RunResult } from "@/lib/qscv/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { PanelHeader, downloadText, ScrollBox } from "./shared";

const HEADERS: Record<JournalFormat, string> = {
  methodology: "run,direction,step,v_before_V,v_after_V,a_s,b_s,i_avg_A,i_background_A,u_i_A,pre_settled,status",
  qscv_final:
    "run,direction,step,v_before_V,v_after_V,a_s,b_s,i_A,u_i_A,leak_A,u_leak_ind_A,u_dt_s,pre_settled",
};

export default function JournalPanel() {
  const runs = useQscvStore((s) => s.runs);
  const allRuns = useQscvStore((s) => s.allRuns);
  const setImportedRows = useQscvStore((s) => s.setImportedRows);
  const busy = useQscvStore((s) => s.busy);

  const [selected, setSelected] = useState<string>(runs.length > 0 ? "current:0" : "");
  const [run, setRun] = useState<RunResult | null>(runs.length > 0 ? runs[0] : null);
  const [format, setFormat] = useState<JournalFormat>("methodology");
  const [previewCsv, setPreviewCsv] = useState<string | null>(null);
  const [previewRows, setPreviewRows] = useState(0);
  const [loading, setLoading] = useState(false);
  const [csvText, setCsvText] = useState("");
  const [importing, setImporting] = useState(false);

  const options = useMemo(() => {
    const list: { value: string; label: string }[] = [];
    runs.forEach((r, i) => list.push({ value: `current:${i}`, label: `Текущий: ${r.label}` }));
    allRuns.forEach((r) => list.push({ value: `saved:${r.id}`, label: `Сохранённый: ${r.label}` }));
    return list;
  }, [runs, allRuns]);

  const onPreview = async () => {
    if (!run) {
      toast.error("Прогон не выбран");
      return;
    }
    setLoading(true);
    try {
      const csv = await api.exportJournal(run, format);
      setPreviewCsv(csv);
      setPreviewRows(Math.max(0, csv.trim().split("\n").length - 1));
      toast.success("Предпросмотр журнала готов");
    } catch (e) {
      toast.error(`Ошибка экспорта журнала: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const onDownloadCsv = async () => {
    if (!run) {
      toast.error("Прогон не выбран");
      return;
    }
    setLoading(true);
    try {
      const csv = previewCsv ?? (await api.exportJournal(run, format));
      if (!previewCsv) {
        setPreviewCsv(csv);
        setPreviewRows(Math.max(0, csv.trim().split("\n").length - 1));
      }
      downloadText(csv, `qscv_journal_${format}_${run.id}.csv`, "text/csv;charset=utf-8", true);
      toast.success("CSV сохранён (UTF-8 с BOM для Excel)");
    } catch (e) {
      toast.error(`Ошибка скачивания: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const onDownloadJson = () => {
    if (!run) {
      toast.error("Прогон не выбран");
      return;
    }
    const meta = {
      config: run.config,
      dut: run.dut,
      timing: run.timing,
      createdAt: run.createdAt,
      stopReason: run.stopReason,
    };
    downloadText(JSON.stringify(meta, null, 2), `qscv_metadata_${run.id}.json`, "application/json");
    toast.success("Метаданные сохранены");
  };

  const onLoadSelected = async () => {
    if (!selected) {
      toast.error("Выберите прогон");
      return;
    }
    if (selected.startsWith("current:")) {
      const r = runs[parseInt(selected.slice("current:".length), 10)];
      if (r) {
        setRun(r);
        setPreviewCsv(null);
        toast.info(`Выбран текущий прогон: ${r.label}`);
      }
      return;
    }
    const id = selected.slice("saved:".length);
    setLoading(true);
    try {
      const r = await api.getRun(id);
      setRun(r);
      setPreviewCsv(null);
      toast.success(`Прогон загружен: ${r.label}`);
    } catch (e) {
      toast.error(`Не удалось загрузить прогон: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const onImport = async (text: string) => {
    const t = text.trim();
    if (!t) {
      toast.error("Пустой CSV — вставьте текст или выберите файл");
      return;
    }
    setImporting(true);
    try {
      const rows = await api.importJournal(t);
      setImportedRows(rows);
      toast.success(`Импортировано строк: ${rows.length}. Далее — вкладка «Анализ данных»`);
    } catch (e) {
      toast.error(`Ошибка импорта: ${(e as Error).message}`);
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="space-y-4">
      <PanelHeader
        title="Журнал"
        subtitle="Методика §13: строка на каждую апертуру; экспорт CSV и метаданных, импорт для офлайн-анализа"
      />

      {/* Источник прогона */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Прогон для экспорта</CardTitle>
          <CardDescription>Текущие прогоны сессии или сохранённые в базе данных</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {options.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Нет прогонов: выполните измерение или дождитесь сохранённых прогонов в базе.
            </p>
          ) : (
            <div className="flex flex-col gap-2 sm:flex-row">
              <Select value={selected} onValueChange={setSelected}>
                <SelectTrigger className="w-full sm:w-80" aria-label="Прогон">
                  <SelectValue placeholder="Выберите прогон" />
                </SelectTrigger>
                <SelectContent>
                  {options.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button variant="outline" onClick={onLoadSelected} disabled={loading || busy !== null}>
                {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                Загрузить
              </Button>
            </div>
          )}

          {run && (
            <div className="rounded-md border bg-muted/30 p-3 text-xs">
              <div className="font-medium">{run.label}</div>
              <div className="mt-1 text-muted-foreground">
                {run.steps.length} шагов · {run.steps.reduce((n, s) => n + s.apertures.length, 0)} апертур ·{" "}
                {run.stopped ? `остановлен (${run.stopReason ?? "?"})` : "завершён штатно"} ·{" "}
                {new Date(run.createdAt).toLocaleString("ru-RU")}
              </div>
            </div>
          )}

          <Separator />

          {/* Формат */}
          <div className="space-y-1.5">
            <Label className="text-xs">Формат журнала</Label>
            <Select value={format} onValueChange={(v) => setFormat(v as JournalFormat)}>
              <SelectTrigger className="w-full sm:w-96" aria-label="Формат журнала">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="methodology">methodology (§13: статус и фон по апертурам)</SelectItem>
                <SelectItem value="qscv_final">qscv_final (acquisition.csv)</SelectItem>
              </SelectContent>
            </Select>
            <div className="rounded-md bg-muted/50 px-3 py-2 font-mono text-[11px] break-all">{HEADERS[format]}</div>
          </div>

          {/* Действия */}
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
            <Button onClick={onPreview} disabled={!run || loading}>
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Eye className="h-4 w-4" />}
              Предпросмотр
            </Button>
            <Button variant="outline" onClick={onDownloadCsv} disabled={!run || loading}>
              <Download className="h-4 w-4" />
              Скачать CSV
            </Button>
            <Button variant="outline" onClick={onDownloadJson} disabled={!run}>
              <Braces className="h-4 w-4" />
              Скачать метаданные (JSON)
            </Button>
          </div>
          <p className="text-[11px] text-muted-foreground">
            Метаданные: отдельно сохранить настройки диапазонов, NPLC/aperture, таймеров, autozero/фильтров, калибровки,
            температуры, оснастки и подготовки (§13).
          </p>

          {/* Предпросмотр */}
          {previewCsv !== null && (
            <div>
              <div className="mb-2 text-xs font-semibold">
                Предпросмотр: {previewRows} строк данных
              </div>
              <ScrollBox className="max-h-96">
                <pre className="p-3 font-mono text-[11px] leading-relaxed whitespace-pre">{previewCsv}</pre>
              </ScrollBox>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Импорт */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Импорт CSV</CardTitle>
          <CardDescription>
            Строки попадают в «Анализ данных»; заголовок — как в формате qscv_final
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea
            rows={4}
            placeholder={`Вставьте CSV с заголовком:\n${HEADERS.qscv_final}`}
            value={csvText}
            onChange={(e) => setCsvText(e.target.value)}
            className="font-mono text-[11px]"
          />
          <div className="flex flex-col gap-2 sm:flex-row">
            <Button variant="outline" onClick={() => onImport(csvText)} disabled={importing}>
              {importing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
              Импортировать текст
            </Button>
            <Input
              type="file"
              accept=".csv,.txt"
              aria-label="Файл CSV для импорта"
              className="max-w-72 text-xs"
              onChange={async (e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                const text = await f.text();
                setCsvText(text.slice(0, 100000));
                await onImport(text);
              }}
            />
          </div>
        </CardContent>
      </Card>

      <Alert>
        <ScrollText />
        <AlertTitle>Журнал — первичные данные (§13)</AlertTitle>
        <AlertDescription>
          Строка на каждую апертуру с фактическими границами [a_s, b_s]: это первичная запись, из которой любая
          обработка (C–V, фиты, пары) воспроизводима офлайн. Импортированные строки передаются на вкладку «Анализ
          данных» без повторного запроса к прибору.
        </AlertDescription>
      </Alert>
    </div>
  );
}
