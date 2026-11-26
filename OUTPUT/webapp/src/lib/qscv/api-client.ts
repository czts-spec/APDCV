/**
 * Типизированный клиент API для QSCV-воркбенча.
 * Все запросы — относительные пути (требование шлюза).
 */
import type {
  AnalyzeOptions,
  AnalyzeResult,
  AcquisitionRow,
  InstrumentPassport,
  JournalFormat,
  MeasurementConfig,
  RunResult,
  RunSummary,
  ScpiRequest,
  ScpiResult,
  VerificationRequest,
  VerificationResult,
} from "./types";

async function post<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${url}: ${res.status} ${text.slice(0, 300)}`);
  }
  return (await res.json()) as T;
}

async function get<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${url}: ${res.status} ${text.slice(0, 300)}`);
  }
  return (await res.json()) as T;
}

export const api = {
  // Паспорт прибора
  getInstrument: () => get<InstrumentPassport>("/api/instrument"),
  saveInstrument: (p: InstrumentPassport) => post<InstrumentPassport>("/api/instrument", p),

  // Паспорт программы
  listConfigs: () => get<{ id: string; name: string; createdAt: string }[]>("/api/config"),
  saveConfig: (data: { config: MeasurementConfig; dutLabel?: string }) =>
    post<{ id: string }>("/api/config", data),

  // Верификация (§6)
  verify: (req: VerificationRequest) => post<VerificationResult>("/api/verify", req),

  // Выполнение измерения (симуляция)
  runMeasurement: (body: {
    config: MeasurementConfig;
    dutLabel?: string;
    persist?: boolean;
  }) => post<RunResult[]>("/api/run", body),

  // Сохранённые прогоны
  listRuns: () => get<RunSummary[]>("/api/runs"),
  getRun: (id: string) => get<RunResult>(`/api/runs/${id}`),
  deleteRun: (id: string) =>
    fetch(`/api/runs/${id}`, { method: "DELETE" }).then((r) => {
      if (!r.ok) throw new Error(`delete failed: ${r.status}`);
      return r.json();
    }),

  // Офлайн-анализ (порт qscv_final)
  analyze: (body: { rows: AcquisitionRow[]; options: AnalyzeOptions }) =>
    post<AnalyzeResult>("/api/analyze", body),

  // SCPI-скрипт
  scpi: (req: ScpiRequest) => post<ScpiResult>("/api/scpi", req),

  // Журнал CSV
  exportJournal: async (run: RunResult, format: JournalFormat): Promise<string> => {
    const res = await fetch("/api/journal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run, format }),
    });
    if (!res.ok) throw new Error(`journal export failed: ${res.status}`);
    return await res.text();
  },

  // Импорт acquisition.csv
  importJournal: async (csvText: string): Promise<AcquisitionRow[]> => {
    const res = await fetch("/api/journal", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ csvText }),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text.slice(0, 300));
    }
    const json = (await res.json()) as { rows: AcquisitionRow[]; errors: string[] };
    if (json.errors.length > 0) throw new Error(json.errors.join("; "));
    return json.rows;
  },
};
