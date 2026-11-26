/**
 * Журнал данных (методика §13) и обмен с qscv_final.py.
 *
 * Формат «methodology» — минимальный CSV методики, одна строка на апертуру:
 *   run,direction,step,v_before_V,v_after_V,a_s,b_s,i_avg_A,i_background_A,u_i_A,pre_settled,status
 *
 * Формат «qscv_final» — acquisition.csv офлайн-анализатора qscv_final.py:
 *   run,direction,step,v_before_V,v_after_V,a_s,b_s,i_A,u_i_A,leak_A,u_leak_ind_A,u_dt_s,pre_settled
 *
 * Отдельно сохраняются настройки (диапазоны, NPLC/aperture, таймеры, autozero/
 * фильтры, калибровка, температура, оснастка, подготовка) — метаданные JSON.
 */

import type { AcquisitionRow, JournalFormat, RunResult } from "./types";

/** Минимальная потеря точности: JS-строка числа — кратчайшая точная запись. */
function num(x: number): string {
  if (!isFinite(x)) return "";
  return String(x);
}

const HEADER_METHODOLOGY =
  "run,direction,step,v_before_V,v_after_V,a_s,b_s,i_avg_A,i_background_A,u_i_A,pre_settled,status";
const HEADER_QSCV_FINAL =
  "run,direction,step,v_before_V,v_after_V,a_s,b_s,i_A,u_i_A,leak_A,u_leak_ind_A,u_dt_s,pre_settled";

function csvEscape(s: string): string {
  if (/[",\n;]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

export function journalCsv(run: RunResult, format: JournalFormat): string {
  const rows: string[] = [];
  rows.push(format === "methodology" ? HEADER_METHODOLOGY : HEADER_QSCV_FINAL);
  for (const step of run.steps) {
    for (const ap of step.apertures) {
      if (format === "methodology") {
        const status = step.flags.length > 0 ? step.flags.join(";") : "OK";
        rows.push(
          [
            csvEscape(run.label),
            step.direction,
            step.step,
            num(step.vBefore_V),
            num(step.vAfter_V),
            num(ap.a_s),
            num(ap.b_s),
            num(ap.i_avg_A),
            num(ap.i_background_A),
            num(ap.u_i_A),
            step.preSettled ? "true" : "false",
            csvEscape(status),
          ].join(","),
        );
      } else {
        rows.push(
          [
            csvEscape(run.label),
            step.direction,
            step.step,
            num(step.vBefore_V),
            num(step.vAfter_V),
            num(ap.a_s),
            num(ap.b_s),
            num(ap.i_avg_A),
            num(ap.u_i_A),
            num(ap.i_background_A),
            num(0), // u_leak_ind_A — независимая неопределённость фона
            num(0), // u_dt_s — неопределённость границ окна
            step.preSettled ? "true" : "false",
          ].join(","),
        );
      }
    }
  }
  return rows.join("\n") + "\n";
}

/** Метаданные прогона (§13: «отдельно сохранить настройки»). */
export function journalMetadata(run: RunResult): string {
  return JSON.stringify(
    {
      run: {
        id: run.id,
        label: run.label,
        direction: run.direction,
        cycle: run.cycle,
        protocol: run.protocol,
        createdAt: run.createdAt,
        stopped: run.stopped,
        stopReason: run.stopReason,
      },
      config: run.config, // диапазоны, NPLC/aperture, таймеры, autozero/фильтры, подготовка, допуски
      dut: run.dut,
      timing: run.timing, // фактические границы окон (§6)
      energy: {
        wPortTotal_J: run.wPortTotal_J,
        dUTotal_J: run.dUTotal_J,
        dissipatedTotal_J: run.dissipatedTotal_J,
        loopArea_J: run.loopArea_J,
      },
      note: "Журнал: одна строка на апертуру (методика §13). Настройки сохранены отдельно. В отчёте разделить непосредственно измеренное, рассчитанное из данных и восстановленное по модели.",
    },
    null,
    2,
  );
}

const QSCV_REQUIRED = [
  "run",
  "direction",
  "step",
  "v_before_V",
  "v_after_V",
  "a_s",
  "b_s",
  "i_A",
  "u_i_A",
  "leak_A",
  "u_leak_ind_A",
  "u_dt_s",
  "pre_settled",
];

function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          cur += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        cur += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      out.push(cur);
      cur = "";
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out;
}

/**
 * Разбор acquisition.csv (формат qscv_final.py). Ошибки собираются в errors,
 * исключения не выбрасываются (устойчивость API).
 */
export function parseAcquisitionCsv(text: string): { rows: AcquisitionRow[]; errors: string[] } {
  const rows: AcquisitionRow[] = [];
  const errors: string[] = [];
  const lines = text.split(/\r?\n/).filter((l) => l.trim().length > 0);
  if (lines.length === 0) {
    return { rows, errors: ["Пустой CSV"] };
  }
  const header = splitCsvLine(lines[0]).map((h) => h.trim());
  const missing = QSCV_REQUIRED.filter((c) => !header.includes(c));
  if (missing.length > 0) {
    return { rows, errors: [`Отсутствуют обязательные столбцы: ${missing.join(", ")}`] };
  }
  const idx: Record<string, number> = {};
  header.forEach((h, i) => {
    idx[h] = i;
  });
  for (let li = 1; li < lines.length; li++) {
    const cells = splitCsvLine(lines[li]);
    const lineNo = li + 1;
    const getNum = (col: string): number => {
      const v = Number(cells[idx[col]]);
      if (!isFinite(v)) {
        errors.push(`Строка ${lineNo}: нечисловое значение «${col}» = «${cells[idx[col]]}»`);
        return NaN;
      }
      return v;
    };
    const preRaw = (cells[idx["pre_settled"]] ?? "").trim().toLowerCase();
    const row: AcquisitionRow = {
      run: (cells[idx["run"]] ?? "").trim(),
      direction: (cells[idx["direction"]] ?? "").trim(),
      step: getNum("step"),
      v_before_V: getNum("v_before_V"),
      v_after_V: getNum("v_after_V"),
      a_s: getNum("a_s"),
      b_s: getNum("b_s"),
      i_A: getNum("i_A"),
      u_i_A: getNum("u_i_A"),
      leak_A: getNum("leak_A"),
      u_leak_ind_A: getNum("u_leak_ind_A"),
      u_dt_s: getNum("u_dt_s"),
      pre_settled: preRaw === "true" || preRaw === "1",
    };
    if (errors.some((e) => e.startsWith(`Строка ${lineNo}:`))) continue;
    if (!row.run || !row.direction) {
      errors.push(`Строка ${lineNo}: пустые run/direction`);
      continue;
    }
    rows.push(row);
  }
  if (rows.length === 0 && errors.length === 0) {
    errors.push("Нет строк данных");
  }
  return { rows, errors };
}
