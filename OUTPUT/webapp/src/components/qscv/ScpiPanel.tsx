"use client";
/**
 * Панель «SCPI-скрипт» — генерация проверенной последовательности команд
 * для B2910BL (quasi_static_cv_B2910BL.md, B2900B/BL Programming Guide).
 */
import { useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, Copy, Download, Loader2, TerminalSquare, Wand2 } from "lucide-react";
import { useQscvStore } from "@/lib/store";
import { api } from "@/lib/qscv/api-client";
import type { Direction, Protocol, ScpiResult } from "@/lib/qscv/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PanelHeader, copyToClipboard, downloadText, ScrollBox } from "./shared";

export default function ScpiPanel() {
  const config = useQscvStore((s) => s.config);
  const [protocol, setProtocol] = useState<Protocol>(config.protocol);
  const [direction, setDirection] = useState<Direction>("forward");
  const [includeReverse, setIncludeReverse] = useState(true);
  const [result, setResult] = useState<ScpiResult | null>(null);
  const [generating, setGenerating] = useState(false);

  const onGenerate = async () => {
    setGenerating(true);
    try {
      const res = await api.scpi({ config, protocol, direction, includeReverse });
      setResult(res);
      toast.success(`Скрипт сгенерирован: команд ${res.commands.length}`);
    } catch (e) {
      toast.error(`Ошибка генерации: ${(e as Error).message}`);
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="space-y-4">
      <PanelHeader
        title="SCPI-скрипт"
        subtitle="Генерация последовательности команд для реального B2910BL по текущему паспорту программы"
      />

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Параметры генерации</CardTitle>
          <CardDescription>Численные значения берутся из вкладки «Паспорт программы»</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Протокол</Label>
              <Select value={protocol} onValueChange={(v) => setProtocol(v as Protocol)}>
                <SelectTrigger className="w-full" aria-label="Протокол">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="kuhn_staircase">A — линейная развёртка (Кун)</SelectItem>
                  <SelectItem value="aperture_charge">B — зарядовый (апертуры)</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Основное направление</Label>
              <Select value={direction} onValueChange={(v) => setDirection(v as Direction)}>
                <SelectTrigger className="w-full" aria-label="Направление">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="forward">прямой ход (UP)</SelectItem>
                  <SelectItem value="reverse">обратный ход (DOWN)</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2.5">
              <div>
                <div className="text-sm font-medium">Обе развёртки</div>
                <div className="text-[11px] text-muted-foreground">прямой + обратный ход в одном скрипте</div>
              </div>
              <Switch checked={includeReverse} onCheckedChange={setIncludeReverse} aria-label="Включить обратный ход" />
            </div>
          </div>
          <Button onClick={onGenerate} disabled={generating}>
            {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
            Сгенерировать
          </Button>
        </CardContent>
      </Card>

      {result && (
        <>
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <CardTitle className="text-sm">Скрипт</CardTitle>
                  <CardDescription>Одна команда на строку; комментарии — после «—»</CardDescription>
                </div>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={async () => {
                      const ok = await copyToClipboard(result.script);
                      if (ok) toast.success("Скрипт скопирован в буфер обмена");
                      else toast.error("Не удалось скопировать");
                    }}
                  >
                    <Copy className="h-3.5 w-3.5" />
                    Копировать
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      downloadText(result.script, `qscv_${protocol}_${direction}.scpi`, "text/plain");
                      toast.success("Файл .scpi сохранён");
                    }}
                  >
                    <Download className="h-3.5 w-3.5" />
                    Скачать .scpi
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <ScrollBox className="max-h-[600px]">
                <pre className="p-3 font-mono text-xs leading-relaxed whitespace-pre">{result.script}</pre>
              </ScrollBox>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Команды с комментариями ({result.commands.length})</CardTitle>
            </CardHeader>
            <CardContent>
              <ScrollBox className="max-h-72">
                <Table>
                  <TableHeader className="sticky top-0 z-10 bg-card">
                    <TableRow>
                      <TableHead className="w-1/2">Команда</TableHead>
                      <TableHead>Комментарий</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {result.commands.map((c, i) => (
                      <TableRow key={i}>
                        <TableCell className="font-mono text-xs break-all">{c.cmd}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">{c.comment}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ScrollBox>
            </CardContent>
          </Card>

          {result.notes.length > 0 && (
            <div className="space-y-2">
              {result.notes.map((n, i) => (
                <Alert key={i}>
                  <AlertTriangle />
                  <AlertDescription>{n}</AlertDescription>
                </Alert>
              ))}
            </div>
          )}
        </>
      )}

      {!result && (
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-10 text-center">
            <TerminalSquare className="h-8 w-8 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">
              Задайте параметры и нажмите «Сгенерировать» — скрипт появится здесь.
            </p>
          </CardContent>
        </Card>
      )}

      <Alert>
        <AlertTriangle />
        <AlertTitle>Сверка обязательна</AlertTitle>
        <AlertDescription>
          Численные настройки согласуют с временными диаграммами Programming Guide и SCPI Reference именно B2910BL (§5).
          Сверьте autozero/фильтры по SCPI Command Reference.
        </AlertDescription>
      </Alert>
    </div>
  );
}
