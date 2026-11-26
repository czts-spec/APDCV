"use client";
/**
 * Глобальное состояние QSCV-воркбенча (zustand).
 * Панели — управляемые компоненты; состояние и загрузка данных — здесь/в page.tsx.
 */
import { create } from "zustand";
import type {
  AcquisitionRow,
  AnalyzeOptions,
  AnalyzeResult,
  DutModel,
  InstrumentPassport,
  MeasurementConfig,
  RunResult,
  VerificationRequest,
  VerificationResult,
} from "@/lib/qscv/types";
import { DEFAULT_CONFIG, DEFAULT_INSTRUMENT, DUT_PRESETS } from "@/lib/qscv/types";

export type TabId =
  | "equipment"
  | "passport"
  | "verification"
  | "measurement"
  | "results"
  | "analysis"
  | "journal"
  | "scpi"
  | "reference";

export const DEFAULT_ANALYZE_OPTIONS: AnalyzeOptions = {
  model: "exp2",
  gapMode: "reject",
  gapCurrentBound_A: 1e-11,
  tailPoints: 10,
  tailLimit_A: 1e-12,
  slopeLimit_A_s: 1e-10,
  kFactor: 1.96,
  uCurrentCommon_A: 0,
  uLeakCommon_A: 0,
  uClockRel: 0,
  uDv_V: 0,
};

interface QscvState {
  activeTab: TabId;
  instrument: InstrumentPassport;
  config: MeasurementConfig;
  dutLabel: string;

  // результаты
  runs: RunResult[]; // последние прогоны (fwd, rev, ...)
  allRuns: { id: string; label: string; createdAt: string }[];
  verification: VerificationResult | null;
  analysis: AnalyzeResult | null;
  importedRows: AcquisitionRow[] | null;

  // загрузка
  busy: string | null;

  setTab: (t: TabId) => void;
  setInstrument: (p: InstrumentPassport) => void;
  setConfig: (c: MeasurementConfig) => void;
  setDutLabel: (l: string) => void;
  setRuns: (r: RunResult[]) => void;
  setVerification: (v: VerificationResult | null) => void;
  setAnalysis: (a: AnalyzeResult | null) => void;
  setImportedRows: (r: AcquisitionRow[] | null) => void;
  setBusy: (b: string | null) => void;
  refreshRuns: (items: { id: string; label: string; createdAt: string }[]) => void;
}

export const useQscvStore = create<QscvState>((set) => ({
  activeTab: "equipment",
  instrument: DEFAULT_INSTRUMENT,
  config: DEFAULT_CONFIG,
  dutLabel: "demo_qscv",
  runs: [],
  allRuns: [],
  verification: null,
  analysis: null,
  importedRows: null,
  busy: null,
  setTab: (t) => set({ activeTab: t }),
  setInstrument: (p) => set({ instrument: p }),
  setConfig: (c) => set({ config: c }),
  setDutLabel: (l) => set({ dutLabel: l }),
  setRuns: (r) => set({ runs: r }),
  setVerification: (v) => set({ verification: v }),
  setAnalysis: (a) => set({ analysis: a }),
  setImportedRows: (r) => set({ importedRows: r }),
  setBusy: (b) => set({ busy: b }),
  refreshRuns: (items) => set({ allRuns: items }),
}));

export function currentDut(dutLabel: string): DutModel {
  return DUT_PRESETS[dutLabel] ?? DUT_PRESETS.demo_qscv;
}

export function defaultVerificationRequest(): VerificationRequest {
  return {
    refType: "rc",
    cRef_F: 1e-9,
    rRef_ohm: 1e6,
    dv_V: 0.02,
    timing: {
      interval_s: 0.001,
      aperture_s: 0.0008,
      tranDelay_s: 0,
      acqDelay_s: 0,
      tranTime_s: 0.0001,
      count: 60,
    },
    tolerancePct: 2,
    noise_A: 2e-13,
  };
}
