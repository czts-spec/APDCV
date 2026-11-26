"use client";
/**
 * QSCV B2910BL — рабочее место квазистатических C–V измерений.
 *
 * Теоретическая и инструментальная основа (ничего не выдумано):
 *  - «Методика квазистатических измерений на Keysight B2910BL» (все §)
 *  - qscv_final.py / physical_observer.py / QSCV_E7_12_B2910BL_FINAL.md
 *  - quasi_static_cv_B2910BL.md (SCPI), B2900B/BL Programming Guide / User's Guide
 *  - Kuhn 1970, Berglund 1966, Terman 1962 (§14)
 *
 * Это веб-GUI: режим симуляции физической модели DUT + генерация проверенного
 * SCPI-скрипта для реального B2910BL + офлайн-анализ imported acquisition.csv.
 */
import { useCallback, useEffect, useState } from "react";
import { useTheme } from "next-themes";
import {
  Activity,
  BookOpen,
  Cable,
  ClipboardList,
  Cpu,
  FlaskConical,
  Gauge,
  Moon,
  ScrollText,
  Sun,
  TerminalSquare,
  Download,
} from "lucide-react";
import { useQscvStore, type TabId } from "@/lib/store";
import { api } from "@/lib/qscv/api-client";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { toast } from "sonner";

import EquipmentPanel from "@/components/qscv/EquipmentPanel";
import PassportForm from "@/components/qscv/PassportForm";
import VerificationPanel from "@/components/qscv/VerificationPanel";
import MeasurementPanel from "@/components/qscv/MeasurementPanel";
import ResultsPanel from "@/components/qscv/ResultsPanel";
import AnalysisPanel from "@/components/qscv/AnalysisPanel";
import JournalPanel from "@/components/qscv/JournalPanel";
import ScpiPanel from "@/components/qscv/ScpiPanel";
import ReferencePanel from "@/components/qscv/ReferencePanel";

const TABS: { id: TabId; label: string; icon: React.ReactNode; hint: string }[] = [
  { id: "equipment", label: "Оборудование", icon: <Cable className="h-4 w-4" />, hint: "§4: паспорт прибора, подключение, self-cal" },
  { id: "passport", label: "Паспорт программы", icon: <ClipboardList className="h-4 w-4" />, hint: "§5: параметры до запуска" },
  { id: "verification", label: "Верификация", icon: <FlaskConical className="h-4 w-4" />, hint: "§6: проверка временного режима на C и RC" },
  { id: "measurement", label: "Измерение", icon: <Activity className="h-4 w-4" />, hint: "§7: пошаговый регламент, стоп-условия" },
  { id: "results", label: "Результаты", icon: <Gauge className="h-4 w-4" />, hint: "§8–12: заряд, C–V, хвост, энергия, неопределённость" },
  { id: "analysis", label: "Анализ данных", icon: <Cpu className="h-4 w-4" />, hint: "qscv_final: фиты, пропуски, fwd/rev" },
  { id: "journal", label: "Журнал CSV", icon: <ScrollText className="h-4 w-4" />, hint: "§13: строка на апертуру, экспорт/импорт" },
  { id: "scpi", label: "SCPI-скрипт", icon: <TerminalSquare className="h-4 w-4" />, hint: "Проверенные команды B2900B/BL" },
  { id: "reference", label: "Справка", icon: <BookOpen className="h-4 w-4" />, hint: "§2–3, §8–11: термины, формулы, примеры" },
];

export default function Home() {
  const {
    activeTab,
    setTab,
    instrument,
    config,
    dutLabel,
    runs,
    allRuns,
    refreshRuns,
    setRuns,
    busy,
    setBusy,
  } = useQscvStore();
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    // Загрузка паспорта прибора и списка прогонов
    api
      .getInstrument()
      .then((p) => useQscvStore.getState().setInstrument(p))
      .catch(() => {});
    api
      .listRuns()
      .then((items) => refreshRuns(items.map((r) => ({ id: r.id, label: r.label, createdAt: r.createdAt }))))
      .catch(() => {});
  }, [refreshRuns]);

  const openRun = useCallback(
    async (id: string) => {
      setBusy("load-run");
      try {
        const run = await api.getRun(id);
        setRuns([run]);
        setTab("results");
        toast.success(`Прогон загружен: ${run.label}`);
      } catch (e) {
        toast.error(`Не удалось загрузить прогон: ${(e as Error).message}`);
      } finally {
        setBusy(null);
      }
    },
    [setBusy, setRuns, setTab],
  );

  return (
    <div className="min-h-screen flex flex-col bg-background">
      {/* Заголовок */}
      <header className="sticky top-0 z-40 border-b bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80">
        <div className="flex h-14 items-center gap-3 px-4">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary text-primary-foreground font-mono text-[10px] font-bold leading-none">
              QS<br />CV
            </div>
            <div className="leading-tight">
              <div className="text-sm font-semibold tracking-tight">
                QSCV B2910BL <span className="text-muted-foreground font-normal">— квазистатические C–V измерения</span>
              </div>
              <div className="text-[11px] text-muted-foreground">
                Методика §1–14 · qscv_final · physical_observer · Keysight B2900B/BL Programming Guide
              </div>
            </div>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <a
              href="/api/download/script"
              className="hidden sm:inline-flex h-9 items-center gap-1.5 rounded-md border bg-card px-3 text-[12px] font-medium transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              title="Автономный однофайловый скрипт: tkinter-GUI + PyVISA (опционально), диапазон расширен до фемтофарад; стандартная библиотека Python"
            >
              <Download className="h-4 w-4" />
              <span className="whitespace-nowrap">qscv_b2910bl.py · фемтофарады</span>
            </a>
            <div className="hidden md:flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] text-muted-foreground">
              <span className={cn("h-2 w-2 rounded-full", busy ? "bg-amber-500 animate-pulse" : runs.length > 0 ? "bg-emerald-500" : "bg-muted-foreground/40")} />
              {busy ? "Выполняется…" : runs.length > 0 ? `Прогонов: ${runs.length}` : "Режим симуляции (прибор не подключён)"}
            </div>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Переключить тему"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            >
              {mounted && theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </Button>
          </div>
        </div>
      </header>

      <div className="flex flex-1 flex-col lg:flex-row">
        {/* Навигация */}
        <nav
          aria-label="Разделы"
          className="shrink-0 border-b bg-card lg:w-60 lg:border-b-0 lg:border-r"
        >
          <div className="flex gap-1 overflow-x-auto p-2 lg:flex-col lg:overflow-visible">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                title={t.hint}
                className={cn(
                  "flex min-w-max items-center gap-2 rounded-md px-3 py-2 text-left text-[13px] font-medium transition-colors",
                  "hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  activeTab === t.id
                    ? "bg-primary text-primary-foreground hover:bg-primary hover:text-primary-foreground"
                    : "text-muted-foreground",
                )}
              >
                {t.icon}
                <span className="whitespace-nowrap">{t.label}</span>
              </button>
            ))}
          </div>
          {allRuns.length > 0 && (
            <div className="hidden lg:block border-t p-2">
              <div className="px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                Сохранённые прогоны
              </div>
              <div className="max-h-56 overflow-y-auto">
                {allRuns.map((r) => (
                  <button
                    key={r.id}
                    onClick={() => openRun(r.id)}
                    className="w-full truncate rounded-md px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            </div>
          )}
        </nav>

        {/* Контент */}
        <main className="min-w-0 flex-1 p-4 md:p-6">
          <div className="mx-auto max-w-6xl">
            {activeTab === "equipment" && <EquipmentPanel />}
            {activeTab === "passport" && <PassportForm />}
            {activeTab === "verification" && <VerificationPanel />}
            {activeTab === "measurement" && <MeasurementPanel />}
            {activeTab === "results" && <ResultsPanel />}
            {activeTab === "analysis" && <AnalysisPanel />}
            {activeTab === "journal" && <JournalPanel />}
            {activeTab === "scpi" && <ScpiPanel />}
            {activeTab === "reference" && <ReferencePanel />}
          </div>
        </main>
      </div>

      {/* Футер */}
      <footer className="mt-auto border-t bg-card">
        <div className="flex flex-col gap-1 px-4 py-2 text-[11px] text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
          <div>
            {instrument.model || "B2910BL"} · {instrument.wiring === "4w" ? "4-проводная схема (Force/Sense HI → затвор)" : "2-проводная схема"} ·{" "}
            {config.protocol === "aperture_charge" ? "Протокол B: зарядовый (апертуры)" : "Протокол A: линейная развёртка (Кун)"}
          </div>
          <div className="font-mono">
            DUT: {dutLabel} · I<sub>comp</sub> {(config.iCompliance_A * 1e6).toPrecision(3)} мкА · ΔV {config.vStep_V * 1e3} мВ
          </div>
        </div>
      </footer>
      <Sonner position="bottom-right" richColors closeButton />
    </div>
  );
}
