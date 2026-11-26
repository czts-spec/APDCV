/**
 * Генератор SCPI-скрипта для Keysight B2910BL.
 *
 * Команды сверены с документами foundation:
 *  - docs/foundation/quasi_static_cv_B2910BL.md (минимальная последовательность sweep);
 *  - docs/foundation/B2900B-BL-Programming-Guide.txt (Edition 1):
 *      :SENS:FUNC / :SENS:REM / :SENS:CURR:PROT / :SENS:CURR:RANG:AUTO /
 *      :SENS:CURR:NPLC / :SOUR:FUNC:MODE / :SOUR:VOLT:MODE SWE|FIX /
 *      :SOUR:VOLT:STAR|STOP|STEP|POIN / :SOUR:SWE:DIR|STA|SPAC|RANG /
 *      :SOUR:VOLT:TRIG (уровень по триггеру) / :SOUR:LIST:VOLT /
 *      :TRIG:SOUR TIM / :TRIG:TIM / :TRIG:COUN / :TRIG:TRAN:DEL / :TRIG:ACQ:DEL /
 *      :SYST:TIME:TIM:COUN:RES:AUTO / :OUTP / :INIT:IMM / *WAI /
 *      :FETC:ARR:CURR? / *CAL? / *TST?
 *  Автоzero и измерительные фильтры — сверять по SCPI Command Reference (§14 №7).
 */

import type { MeasurementConfig, ScpiRequest, ScpiResult } from "./types";
import { apertureFromConfig } from "./smu";

function fmt(x: number): string {
  if (!isFinite(x)) return "NaN";
  // Компактная научная запись без лишних нулей: 0.05, 2E-3, 1E-6
  const s = x.toPrecision(9);
  const n = Number(s);
  if (Number.isInteger(n) && Math.abs(n) < 1e15) return String(n);
  return String(n).replace("e", "E");
}

function nplcOf(cfg: MeasurementConfig): { value: number; note: string } {
  // :SENS:CURR:NPLC — число периодов сети; aperture_s пересчитывается через
  // фактическую частоту сети (§5: «aperture либо NPLC и фактическая частота сети»).
  const aperture = apertureFromConfig(cfg);
  const nplc = cfg.apertureMode === "nplc" ? cfg.nplc : aperture * cfg.lineFreq_Hz;
  return {
    value: Math.round(nplc * 1e6) / 1e6,
    note: `апертура ≈ ${(aperture * 1e3).toPrecision(4)} мс при ${cfg.lineFreq_Hz} Гц`,
  };
}

/** Протокол A — линейная развёртка (метод Куна на staircase sweep). */
function protocolA(req: ScpiRequest): { lines: string[]; commands: { cmd: string; comment: string }[] } {
  const cfg = req.config;
  const { value: nplc, note: nplcNote } = nplcOf(cfg);
  const nPoints = Math.max(2, Math.round(Math.abs(cfg.vEnd_V - cfg.vStart_V) / cfg.vStep_V) + 1);
  const lines: string[] = [];
  const rev = req.direction === "reverse";

  lines.push("*RST", "*CLS");
  lines.push(':OUTP OFF', "");
  lines.push('\' --- Измерение тока (quasi_static_cv_B2910BL.md)');
  lines.push(':SENS:FUNC "CURR"');
  lines.push(":SENS:REM ON  \' 4-проводная схема; для 2-проводной — OFF (§4)");
  lines.push(`:SENS:CURR:PROT ${fmt(cfg.iCompliance_A)}  \' compliance: по DUT, не по максимуму SMU (§4)`);
  if (cfg.iRangeMode === "auto") {
    lines.push(":SENS:CURR:RANG:AUTO ON");
  } else {
    lines.push(`:SENS:CURR:RANG ${fmt(cfg.iRange_A)}  \' фиксированный диапазон`);
  }
  lines.push(`:SENS:CURR:NPLC ${fmt(nplc)}  \' ${nplcNote}`);
  lines.push("");
  lines.push("' --- Источник напряжения: линейная развёртка");
  lines.push(":SOUR:FUNC:MODE VOLT");
  lines.push(":SOUR:VOLT:MODE SWE");
  if (rev) {
    lines.push(`:SOUR:VOLT:STAR ${fmt(cfg.vEnd_V)}`);
    lines.push(`:SOUR:VOLT:STOP ${fmt(cfg.vStart_V)}`);
    lines.push(":SOUR:SWE:DIR DOWN  \' обратный ход");
  } else {
    lines.push(`:SOUR:VOLT:STAR ${fmt(cfg.vStart_V)}`);
    lines.push(`:SOUR:VOLT:STOP ${fmt(cfg.vEnd_V)}`);
    lines.push(":SOUR:SWE:DIR UP");
  }
  lines.push(`:SOUR:VOLT:STEP ${fmt(cfg.vStep_V)}`);
  lines.push(":SOUR:SWE:SPAC LIN  \' линейный шаг");
  lines.push(":SOUR:SWE:RANG FIX  \' диапазон источника не менять на развёртке");
  lines.push("");
  lines.push("' --- Триггерная модель: таймер, период точки = период ступени");
  lines.push(":TRIG:SOUR TIM");
  lines.push(`:TRIG:TIM ${fmt(cfg.trigInterval_s)}  \' T; скорость развёртки r = ΔV/T = ${(cfg.vStep_V / cfg.trigInterval_s).toPrecision(4)} В/с`);
  lines.push(`:TRIG:COUN ${nPoints}  \' точек развёртки`);
  lines.push(`:TRIG:TRAN:DEL ${fmt(cfg.tranDelay_s)}`);
  lines.push("");
  lines.push(":OUTP ON");
  lines.push(":INIT:IMM");
  lines.push("*WAI");
  lines.push(":FETC:ARR:CURR?  \' массив токов: C_i = |I_i/(ΔV/T)| (Кун на staircase)");
  lines.push(":OUTP OFF");

  const commands = [
    { cmd: "*RST / *CLS", comment: "сброс и очистка статусов" },
    { cmd: ':SENS:FUNC "CURR"', comment: "измеряемая величина — ток" },
    { cmd: ":SENS:REM ON", comment: "4-проводная схема (Kelvin); OFF — 2-проводная" },
    { cmd: ":SENS:CURR:PROT <А>", comment: "compliance — ограничение тока защиты DUT" },
    { cmd: ":SENS:CURR:RANG:AUTO ON | :SENS:CURR:RANG <А>", comment: "диапазон измерения тока" },
    { cmd: ":SENS:CURR:NPLC <n>", comment: "интегрирование: n периодов сети (апертура = n/lineFreq)" },
    { cmd: ":SOUR:FUNC:MODE VOLT", comment: "источник напряжения" },
    { cmd: ":SOUR:VOLT:MODE SWE", comment: "режим развёртки (staircase sweep)" },
    { cmd: ":SOUR:VOLT:STAR <В> / :STOP <В> / :STEP <В>", comment: "границы и шаг развёртки" },
    { cmd: ":SOUR:SWE:DIR UP|DOWN", comment: "направление (обратный ход)" },
    { cmd: ":SOUR:SWE:SPAC LIN", comment: "линейное распределение точек" },
    { cmd: ":SOUR:SWE:RANG FIX", comment: "диапазон источника фиксирован на развёртке" },
    { cmd: ":TRIG:SOUR TIM", comment: "источник триггера — таймер" },
    { cmd: ":TRIG:TIM <с>", comment: "интервал триггера (период ступени T)" },
    { cmd: ":TRIG:COUN <n>", comment: "число точек развёртки" },
    { cmd: ":TRIG:TRAN:DEL <с>", comment: "задержка изменения источника от триггера" },
    { cmd: ":OUTP ON / :INIT:IMM / *WAI", comment: "запуск и ожидание завершения" },
    { cmd: ":FETC:ARR:CURR?", comment: "чтение массива результатов" },
  ];
  return { lines, commands };
}

/** Протокол B — зарядовый (апертурный) регламент §7.2: :SOUR:VOLT:TRIG + :FETC:ARR. */
function protocolB(req: ScpiRequest): { lines: string[]; commands: { cmd: string; comment: string }[] } {
  const cfg = req.config;
  const { value: nplc, note: nplcNote } = nplcOf(cfg);
  const lines: string[] = [];
  const rev = req.direction === "reverse";

  const nSteps = Math.max(1, Math.round(Math.abs(cfg.vEnd_V - cfg.vStart_V) / cfg.vStep_V));
  const points: number[] = [];
  for (let i = 0; i <= nSteps; i++) {
    points.push(i === nSteps ? cfg.vEnd_V : cfg.vStart_V + i * cfg.vStep_V);
  }
  const seq = rev ? [...points].reverse() : points;

  lines.push("*RST", "*CLS");
  lines.push(':OUTP OFF', "");
  lines.push("' --- Измерение тока");
  lines.push(':SENS:FUNC "CURR"');
  lines.push(":SENS:REM ON");
  lines.push(`:SENS:CURR:PROT ${fmt(cfg.iCompliance_A)}  \' compliance по DUT (§4)`);
  if (cfg.iRangeMode === "auto") {
    lines.push(":SENS:CURR:RANG:AUTO ON");
  } else {
    lines.push(`:SENS:CURR:RANG ${fmt(cfg.iRange_A)}`);
  }
  lines.push(`:SENS:CURR:NPLC ${fmt(nplc)}  \' ${nplcNote}`);
  lines.push(":SYST:TIME:TIM:COUN:RES:AUTO ON  \' разрешение таймера (Programming Guide)");
  lines.push("");
  lines.push("' --- Источник: фиксированный уровень, изменение по триггеру");
  lines.push(":SOUR:FUNC:MODE VOLT");
  lines.push(":SOUR:VOLT:MODE FIX");
  lines.push("");

  // Подготовка §7.1: начальный bias
  lines.push(`\' === Подготовка §7.1: начальный bias ${fmt(cfg.preBias_V)} В, выдержка ${fmt(cfg.preBiasDuration_s)} с ===`);
  lines.push(":OUTP ON");
  lines.push(`:SOUR:VOLT ${fmt(cfg.preBias_V)}`);
  lines.push(`\' ... выдержка до критерия установления (|dI/dt| ≤ ${fmt(cfg.settleDIdt_A_s)} А/с) ...`);
  lines.push("");

  const emitStep = (k: number) => {
    const vPrev = seq[k];
    const vNext = seq[k + 1];
    lines.push(`\' === Шаг ${k + 1}/${nSteps}: ${fmt(vPrev)} В → ${fmt(vNext)} В ===`);
    lines.push(`:SOUR:VOLT ${fmt(vPrev)}  \' зафиксировать V_prev (наследуется от предыдущего шага)`);
    lines.push(`:SOUR:VOLT:TRIG ${fmt(vNext)}  \' уровень V_next — выводится по триггеру`);
    lines.push(":TRIG:SOUR TIM");
    lines.push(`:TRIG:TIM ${fmt(cfg.trigInterval_s)}  \' период апертур (период таймера ≠ апертура, §6)`);
    lines.push(`:TRIG:COUN ${cfg.trigCount}  \' апертур на шаг`);
    lines.push(`:TRIG:TRAN:DEL ${fmt(cfg.tranDelay_s)}  \' задержка источника`);
    lines.push(`:TRIG:ACQ:DEL ${fmt(cfg.acqDelay_s)}  \' задержка первой апертуры`);
    lines.push(":INIT:IMM");
    lines.push("*WAI");
    lines.push(":FETC:ARR:CURR?  \' средние токи апертур → строки журнала §13");
    lines.push("");
  };

  const maxEmit = 8;
  for (let k = 0; k < nSteps; k++) {
    if (k >= maxEmit) {
      lines.push(`\' === Шаги ${maxEmit + 1}…${nSteps}: повторить блок для каждого перехода ===`);
      lines.push(`\' === (V_prev наследуется от предыдущего шага; триггерная модель неизменна) ===`);
      break;
    }
    emitStep(k);
  }

  lines.push("' === Завершение: безопасный возврат bias и разряд DUT (§7.3) ===");
  lines.push(`:SOUR:VOLT ${fmt(cfg.preBias_V)}`);
  lines.push("' ... безопасный разряд по выбранному протоколу ...");
  lines.push(":OUTP OFF");

  const commands = [
    { cmd: ":SYST:TIME:TIM:COUN:RES:AUTO ON", comment: "автоматическое разрешение таймера триггера" },
    { cmd: ":SOUR:VOLT:MODE FIX", comment: "фиксированный уровень источника" },
    { cmd: ":SOUR:VOLT <В>", comment: "немедленная установка уровня (V_prev, подготовка)" },
    { cmd: ":SOUR:VOLT:TRIG <В>", comment: "уровень по триггеру — синхронизация перехода с измерением" },
    { cmd: ":TRIG:ACQ:DEL <с>", comment: "задержка измерения от триггера (граница первого окна a_1)" },
    { cmd: ":INIT:IMM", comment: "запуск триггерной последовательности шага" },
    { cmd: ":FETC:ARR:CURR?", comment: "чтение N средних токов апертур шага" },
  ];
  return { lines, commands };
}

export function generateScpi(req: ScpiRequest): ScpiResult {
  const cfg = req.config;
  const isA = (req.protocol ?? cfg.protocol) === "kuhn_staircase";
  const gen = isA ? protocolA(req) : protocolB(req);
  const lines = [...gen.lines];

  if (req.includeReverse) {
    const revReq: ScpiRequest = { ...req, direction: req.direction === "forward" ? "reverse" : "forward" };
    const genRev = isA ? protocolA(revReq) : protocolB(revReq);
    lines.push("");
    lines.push("' ============================================================");
    lines.push("' === ОБРАТНЫЙ ХОД (§7.3: с документированной историей на развороте) ===");
    lines.push("' ============================================================");
    lines.push(...genRev.lines);
  }

  const notes = [
    "Сверьте синтаксис и допустимые значения по B2900B/BL SCPI Command Reference для установленной версии прошивки (методика §14, №7).",
    "Autozero и измерительные фильтры задаются по SCPI Command Reference — их синтаксис в Programming Guide не приведён (в паспорте программы §5 они фиксируются отдельными полями).",
    "*WAI блокирует выполнение до завершения; время ответа VISA не является временем физического перехода (§6).",
    "Период таймера (:TRIG:TIM) не равен апертуре; фактические границы окон [a_j, b_j] фиксируются в журнале §13.",
    "Команды сверены с B2900B/BL Series Programming Guide, Edition 1 и quasi_static_cv_B2910BL.md (документы в docs/foundation/).",
    "До DUT выполнить опыт на известных C и RC (§6) — вкладка «Верификация».",
  ];

  return {
    script: lines.join("\n") + "\n",
    commands: gen.commands,
    notes,
  };
}
