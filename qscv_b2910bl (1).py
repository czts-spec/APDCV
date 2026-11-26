#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QSCV B2910BL — квазистатическая C-V на Keysight B2910BL.

Автономный однофайловый скрипт (только стандартная библиотека Python;
pyvisa — опционально, только для реального прибора).

Порт веб-приложения QSCV B2910BL (Next.js) с расширением диапазона
ёмкостей до фемтофарад. Основания — документы docs/foundation/:
  * quasi_static_cv_B2910BL.md — методика и SCPI-последовательность;
  * QSCV_E7_12_B2910BL_FINAL.md — методика измерений (§ указаны в коде);
  * physical_observer.py / PHYSICAL_OBSERVER.md — пассивная модель
    DUT: C0 || G || Σ (R_m последовательно с C_m), точное аналитическое
    распространение по линейным сегментам;
  * qscv_final.py — офлайн-анализатор (экспоненциальная подгонка,
    пропуски, хвост, пары прямой/обратный ход);
  * B2900B-BL Series User's Guide, Edition 1 — диапазоны и параметры:
      таблица 2-1:  B2910BL: set 0.1 пА / 1 мкВ, measure 0.01 пА (10 фА) / 0.1 мкВ, ±210 В;
      таблица 2-7:  диапазоны измерения тока: 10 нА (разрешение 10 фА), 100 нА (100 фА),
                    1 мкА (1 пА), 10 мкА, 100 мкА, 1 мА, 10 мА, 100 мА;
      «Performing Low Current Measurements»: по умолчанию диапазоны 10 нА и 100 нА
                    НЕ используются — для малых токов задавать FIXED 10 нА/100 нА;
      Measurement Time = Aperture time + Overhead time; MANUAL speed — апертура
                    в секундах или NPLC.
  * B2900B-BL Series Programming Guide, Edition 1 — триггерная модель
    (:TRIG:SOUR TIM / :TRIG:TIM / :TRIG:COUN / :TRIG:TRAN:DEL / :TRIG:ACQ:DEL,
    :SOUR:VOLT:TRIG, :FETC:ARR:CURR?, *WAI и др.).

Режимы:
  python qscv_b2910bl.py              GUI (tkinter), по умолчанию симуляция;
  python qscv_b2910bl.py --demo DIR   офлайн-прогон: журналы CSV, метаданные, SCPI;
  python qscv_b2910bl.py --help       справка.

Что скрипт НЕ делает (не выдумано):
  * не содержит неподтверждённых SCPI-команд — только сверенные с
    Programming Guide / quasi_static_cv_B2910BL.md; autozero и измерительные
    фильтры задаются по SCPI Command Reference пользователем отдельно;
  * не гарантирует микросекундную синхронизацию Python/VISA-цикла — тайминг
    обеспечивается триггерной моделью прибора (методика §6);
  * параметры шумовой модели (u_i, offset) — ВХОДНЫЕ ДАННЫЕ ДЛЯ СИМУЛЯЦИИ,
    а не паспортные значения; для реальных измерений их задают из
    свидетельства о калибровке/фактического шума (§12).

Все величины СИ: В, А, Кл (C), Ф, с, Ом, См, Дж.
"""

from __future__ import annotations

import argparse
import json
import math
import queue
import sys
import threading
import time
import uuid as _uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

APP_VERSION = "1.0.0"
APP_TITLE = "QSCV B2910BL — квазистатическая C-V (фемтофарады)"

# ============================== PRNG (порт smu.ts) ==============================

def mulberry32(seed: int) -> Callable[[], float]:
    """Детерминированный PRNG mulberry32 — тот же seed, та же последовательность,
    что и в веб-приложении (JS Math.imul семантика, 32-битные операции)."""
    state = seed & 0xFFFFFFFF

    def imul(a: int, b: int) -> int:
        # JS Math.imul: 32-битное знаковое умножение
        p = (a * b) & 0xFFFFFFFF
        return p - 0x100000000 if p & 0x80000000 else p

    def rnd() -> float:
        nonlocal state
        state = (state + 0x6D2B79F5) & 0xFFFFFFFF
        t = state
        t = imul(t ^ (t >> 15), 1 | t) & 0xFFFFFFFF
        t = (t + imul(t ^ (t >> 7), 61 | t)) & 0xFFFFFFFF
        t = t ^ (t >> 14)
        return (t & 0xFFFFFFFF) / 4294967296.0

    return rnd


def gauss(rng: Callable[[], float]) -> float:
    """Нормальное распределение N(0,1), метод Бокса–Мюллера (порт smu.ts)."""
    u = 0.0
    v = 0.0
    while u == 0:
        u = rng()
    while v == 0:
        v = rng()
    return math.sqrt(-2.0 * math.log(u)) * math.cos(2.0 * math.pi * v)


# ============================== Форматирование (порт format.ts) ==============================

PREFIXES: List[Tuple[float, str]] = [
    (1e12, "Т"), (1e9, "Г"), (1e6, "М"), (1e3, "к"), (1.0, ""),
    (1e-3, "м"), (1e-6, "мк"), (1e-9, "н"), (1e-12, "п"), (1e-15, "ф"),
]


def format_eng(value: float, unit: str, digits: int = 3) -> str:
    """Инженерная нотация: 10 пКл, 1.000 нФ, 20.0 мс (порт format.ts)."""
    if value is None or not math.isfinite(value):
        return "—"
    if value == 0:
        return f"0 {unit}"
    a = abs(value)
    for factor, prefix in PREFIXES:
        if a >= factor:
            scaled = value / factor
            mag = math.floor(math.log10(abs(scaled)))
            decimals = max(0, digits - 1 - mag)
            return f"{scaled:.{decimals}f} {prefix}{unit}"
    return f"{value:.2e} {unit}"


def format_seconds(s: float, digits: int = 3) -> str:
    return format_eng(s, "с", digits)


def format_amps(a: float, digits: int = 3) -> str:
    return format_eng(a, "А", digits)


def format_farads(f: float, digits: int = 3) -> str:
    return format_eng(f, "Ф", digits)


def format_coulombs(q: float, digits: int = 3) -> str:
    return format_eng(q, "Кл", digits)


def format_joules(j: float, digits: int = 3) -> str:
    return format_eng(j, "Дж", digits)


def format_volts(v: float, digits: int = 3) -> str:
    return format_eng(v, "В", digits)


def format_ohms(r: float, digits: int = 3) -> str:
    return format_eng(r, "Ом", digits)


def format_sci(value: float, digits: int = 3) -> str:
    """Компактная научная запись для таблиц: 1.23e-9."""
    if value is None or not math.isfinite(value):
        return "—"
    return f"{value:.{digits - 1}e}"


def fmt_scpi(x: float) -> str:
    """Компактная научная запись для SCPI: 0.05, 2E-3, 1E-6 (порт scpi.ts fmt)."""
    if not math.isfinite(x):
        return "NaN"
    s = f"{x:.9g}"
    n = float(s)
    if n.is_integer() and abs(n) < 1e15:
        return str(int(n))
    return s.replace("e", "E")

# ============================== Типы (порт types.ts) ==============================

# ------------------------------ Паспорт прибора (§4) ------------------------------

@dataclass
class InstrumentPassport:
    model: str = "B2910BL"
    serial_number: str = ""
    firmware: str = ""
    calibration_date: str = ""
    temperature_c: Optional[float] = 23.0
    warmup_minutes: int = 60  # прогрев до self-cal (User's Guide: 60 минут)
    self_cal_done: bool = False  # *CAL? выполнена
    self_test_done: bool = False  # *TST? выполнен
    wiring: str = "4w"  # "4w": :SENS:REM ON — Force/Sense HI→затвор, LO→подложка
    notes: str = ""  # кабели, экранирование, guard, положение образца


# ------------------------------ Модель DUT (симуляция) ------------------------------
# Пассивная линейная модель PHYSICAL_OBSERVER: параллельные ветви
#   C0 (быстрая, Q_fast = C0·V) || G (утечка, i = G·V)
#   || (R1 последовательно с C1): tau = R1·C1, x — напряжение на C1.
# Нелинейная Q_fast(V) — только помеченная демонстрационная модель (сигмоид C(V)).

@dataclass
class DutBranch:
    c_F: float
    tau_s: float  # tau = R·C; R = tau/C


@dataclass
class DutModel:
    kind: str = "linear"  # linear | mos_demo | ref_c | ref_rc
    label: str = ""
    c0_F: float = 0.0
    g_S: float = 0.0
    branches: List[DutBranch] = field(default_factory=list)
    # Нелинейная Q_fast(V) — только kind == "mos_demo":
    # C_fast(V) = cMin + (cOx − cMin)·σ((vfb − V)/width)
    cOx_F: float = 0.0
    cMin_F: float = 0.0
    vfb_V: float = 0.0
    width_V: float = 0.0


def _dut(**kw) -> DutModel:
    base = dict(kind="linear", label="", c0_F=0.0, g_S=0.0, branches=[],
                cOx_F=0.0, cMin_F=0.0, vfb_V=0.0, width_V=0.0)
    base["branches"] = [DutBranch(**b) for b in base["branches"]]
    branches = [DutBranch(**b) for b in kw.pop("branches", [])]
    base.update(kw)
    base["branches"] = branches
    return DutModel(**base)


DUT_PRESETS: Dict[str, DutModel] = {
    "demo_qscv": _dut(
        kind="linear",
        label="Демо qscv_final: 1 нФ (τ=3 мс) + 0.2 нФ (τ=20 мс) + утечка",
        g_S=2e-11,
        branches=[dict(c_F=1e-9, tau_s=0.003), dict(c_F=0.2e-9, tau_s=0.02)],
    ),
    "observer_demo": _dut(
        kind="linear",
        label="Демо physical_observer: C0=1 нФ, G=0.1 нС, C1=0.3 нФ, τ=12 мс",
        c0_F=1e-9, g_S=1e-10,
        branches=[dict(c_F=3e-10, tau_s=0.012)],
    ),
    "mos_demo": _dut(
        kind="mos_demo",
        label="MOS-демо (нелинейная Q_fast + ловушки + утечка) — демонстрационная модель",
        g_S=1e-11,
        branches=[dict(c_F=1.5e-10, tau_s=0.02)],
        cOx_F=1e-9, cMin_F=2e-10, vfb_V=0.05, width_V=0.03,
    ),
    # ----- Расширение диапазона: фемтофарады -----
    "mos_ff_demo": _dut(
        kind="mos_demo",
        label="MOS-демо фФ: cOx=100 фФ, cMin=20 фФ, ловушка 15 фФ (τ=0.5 с) + утечка 2 фС",
        g_S=2e-15,
        branches=[dict(c_F=15e-15, tau_s=0.5)],
        cOx_F=100e-15, cMin_F=20e-15, vfb_V=0.05, width_V=0.03,
    ),
    "ref_c": _dut(
        kind="ref_c",
        label="Эталонный конденсатор 1 нФ (проверка §6)",
        c0_F=1e-9,
    ),
    "ref_rc": _dut(
        kind="ref_rc",
        label="Эталон RC: 1 нФ, τ=1 мс (R=1 МОм) — проверка §6",
        branches=[dict(c_F=1e-9, tau_s=1e-3)],
    ),
    "ref_c_100f": _dut(
        kind="ref_c",
        label="Эталонный конденсатор 100 фФ (проверка §6, малые ёмкости)",
        c0_F=100e-15,
    ),
    "ref_c_10f": _dut(
        kind="ref_c",
        label="Эталонный конденсатор 10 фФ (проверка §6, малые ёмкости)",
        c0_F=10e-15,
    ),
    "ref_c_1f": _dut(
        kind="ref_c",
        label="Эталонный конденсатор 1 фФ (проверка §6, малые ёмкости)",
        c0_F=1e-15,
    ),
    "ref_rc_100f": _dut(
        kind="ref_rc",
        label="Эталон RC фФ: 100 фФ, R=1 ГОм (τ=0.1 с) — проверка §6",
        branches=[dict(c_F=100e-15, tau_s=0.1)],
    ),
}


# ------------------------------ Паспорт программы (§5) ------------------------------

@dataclass
class MeasurementConfig:
    name: str = "Зарядовый протокол — демо qscv_final"
    protocol: str = "aperture_charge"  # kuhn_staircase | aperture_charge

    # Напряжение (§5)
    vStart_V: float = 0.0
    vEnd_V: float = 0.1
    vStep_V: float = 0.02
    directions: List[str] = field(default_factory=lambda: ["forward", "reverse"])
    cycles: int = 1

    # Ток (§5)
    iCompliance_A: float = 1e-6
    iRangeMode: str = "auto"  # auto | fixed
    iRange_A: float = 10e-9

    # Интегрирование (§5)
    speedMode: str = "manual"  # auto|short|medium|normal|long|manual (User's Guide)
    apertureMode: str = "seconds"  # nplc | seconds
    nplc: float = 1.0
    aperture_s: float = 0.0008
    lineFreq_Hz: float = 50.0

    # Тайминг (§5): триггерная модель B2900 (Programming Guide, Fig. 1-2)
    trigInterval_s: float = 0.001
    trigCount: int = 100
    tranDelay_s: float = 0.0
    acqDelay_s: float = 0.0
    tranTime_s: float = 1e-6

    # Подготовка (§5, §7.1)
    preBias_V: float = 0.0
    preBiasDuration_s: float = 0.3
    settleDIdt_A_s: float = 0.0

    # Фон (§5)
    backgroundMode: str = "none"  # none|blank|mean_tail|linear_tail

    # Допуск (§5, §9)
    tailPoints: int = 10
    tailLimit_A: float = 1e-12
    slopeLimit_A_s: float = 1e-10
    kFactor: float = 1.96
    convergencePct: float = 1.0

    # Пропуски и модель (qscv_final: --gap-mode, --model)
    gapMode: str = "reject"  # reject | bound | model
    gapCurrentBound_A: float = 1e-11
    fitModel: str = "exp2"  # none | exp1 | exp2
    stopOnTailFail: bool = False

    # Неопределённость (§12)
    uCurrentCommon_A: float = 0.0
    uLeakCommon_A: float = 0.0
    uClockRel: float = 0.0
    uDv_V: float = 0.0

    # Служебные циклы (§5)
    autozero: bool = True   # сверять по SCPI Reference
    outputFilter: bool = False

    # Шумовая модель SMU (симуляция)
    uIOffset_A: float = 2e-13
    uIGainRel: float = 0.0
    iOffset_A: float = 0.0


# ------------------------------ Данные апертур (§13) ------------------------------

@dataclass
class ApertureRecord:
    j: int
    a_s: float
    b_s: float
    i_avg_A: float
    i_background_A: float
    u_i_A: float
    status: str = "OK"  # OK | OVLD | COMP | ...


# ------------------------------ Результаты анализа шага ------------------------------

@dataclass
class FitComponent:
    tau_s: float
    a_A: float
    q_C: float  # компонентный заряд A·τ (§9: не доказывает механизм ловушек)


@dataclass
class FitInfo:
    model: str
    baseline_A: float
    components: List[FitComponent]
    reducedChi2: float
    condition: float
    success: bool
    remainingTail_C: float
    uGapFitOnly_C: float


@dataclass
class TruthInfo:
    """Только для симуляции: истинное разложение заряда перехода (§13: «истина»)."""
    qFast_C: float
    qBranch_C: List[float]
    qLeak_C: float
    qRegistered_C: float
    qInGaps_C: float
    qTotal_C: float
    cSecTrue_F: float


@dataclass
class KuhnInfo:
    iMeas_A: float
    rampRate_Vps: float
    cKuhn_F: float
    cKuhnLeakCorrected_F: float
    apertureStart_s: float


@dataclass
class StepResult:
    step: int
    direction: str
    vBefore_V: float
    vAfter_V: float
    vMid_V: float
    dv_V: float
    preSettled: bool
    apertures: List[ApertureRecord]
    gaps_s: List[Tuple[float, float]]
    gapDuration_s: float
    qObs_C: float
    uQ_C: float
    qGapModel_C: float
    qGapBound_C: float
    qCorrected_C: float
    cSec_F: float
    uC_F: float
    tailB_A: float
    tailS_A_s: float
    tailUB_A: float
    tailUS_A_s: float
    tailOk: bool
    fit: Optional[FitInfo]
    wPort_J: float
    dU_J: float
    dissipated_J: float
    energyResidual_J: float
    flags: List[str]
    usableForPair: bool
    kuhn: Optional[KuhnInfo] = None
    truth: Optional[TruthInfo] = None


@dataclass
class CvPoint:
    vMid_V: float
    cSec_F: float
    uC_F: float
    cSecTrue_F: Optional[float]
    direction: str
    usable: bool


@dataclass
class RunTiming:
    tranDelay_s: float
    acqDelay_s: float
    tranTime_s: float
    interval_s: float
    aperture_s: float
    firstA_s: float
    firstB_s: float
    lastB_s: float
    initialGap_s: float
    deadTime_s: float


@dataclass
class RunResult:
    id: str
    label: str
    direction: str
    cycle: int
    protocol: str
    config: MeasurementConfig
    dut: DutModel
    steps: List[StepResult] = field(default_factory=list)
    stopped: bool = False
    stopReason: Optional[str] = None
    cvCurve: List[CvPoint] = field(default_factory=list)
    wPortTotal_J: float = 0.0
    dUTotal_J: float = 0.0
    dissipatedTotal_J: float = 0.0
    loopArea_J: float = 0.0
    timing: Optional[RunTiming] = None
    createdAt: str = ""


# ------------------------------ Верификация (§6) ------------------------------

@dataclass
class VerificationTiming:
    interval_s: float
    aperture_s: float
    tranDelay_s: float
    acqDelay_s: float
    tranTime_s: float
    count: int


@dataclass
class VerificationRequest:
    refType: str = "rc"  # "c" | "rc"
    cRef_F: float = 1e-9
    rRef_ohm: float = 1e6
    dv_V: float = 0.02
    timing: VerificationTiming = field(default_factory=lambda: VerificationTiming(
        interval_s=0.001, aperture_s=0.0008, tranDelay_s=0.0,
        acqDelay_s=0.0, tranTime_s=0.0001, count=60))
    tolerancePct: float = 2.0
    noise_A: float = 2e-13


@dataclass
class VerificationCheck:
    label: str
    value: str
    ok: Optional[bool]  # None = информационное
    note: str = ""


@dataclass
class VerificationResult:
    apertures: List[ApertureRecord]
    qExpected_C: float
    qRecovered_C: float
    qRegisteredTrue_C: float
    qInGaps_C: float
    errorRelPct: float
    tolerancePct: float
    pass_: bool
    checks: List[VerificationCheck]
    timing: RunTiming


# ------------------------------ Офлайн-анализ (порт qscv_final) ------------------------------

@dataclass
class AcquisitionRow:
    run: str
    direction: str
    step: float
    v_before_V: float
    v_after_V: float
    a_s: float
    b_s: float
    i_A: float
    u_i_A: float
    leak_A: float
    u_leak_ind_A: float
    u_dt_s: float
    pre_settled: bool


@dataclass
class AnalyzeOptions:
    model: str = "exp2"
    gapMode: str = "reject"
    gapCurrentBound_A: float = 1e-11
    tailPoints: int = 10
    tailLimit_A: float = 1e-12
    slopeLimit_A_s: float = 1e-10
    kFactor: float = 1.96
    uCurrentCommon_A: float = 0.0
    uLeakCommon_A: float = 0.0
    uClockRel: float = 0.0
    uDv_V: float = 0.0


@dataclass
class AnalyzeStepRow:
    run: str
    direction: str
    step: float
    vMid_V: float
    qObserved_C: float
    uQ_C: float
    qGapModel_C: float
    qGapBound_C: float
    qCorrected_C: float
    cSec_F: float
    uC_F: float
    tailCurrent_A: float
    tailSlope_A_s: float
    tailSettled: bool
    preSettled: bool
    gapDuration_s: float
    usableForPair: bool
    flags: List[str]
    fit: Optional[FitInfo]
    gaps_s: List[Tuple[float, float]]


@dataclass
class FwdRevPair:
    run: str
    vMid_V: float
    cForward_F: float
    cReverseInterpolated_F: float
    deltaC_F: float


@dataclass
class AnalyzeResult:
    steps: List[AnalyzeStepRow]
    pairs: List[FwdRevPair]
    errors: List[str]


# ------------------------------ Прочее ------------------------------

STOP_REASONS = [
    "BREAKDOWN_OR_LEAKAGE_GROWTH",
    "OVERLOAD",
    "COMPLIANCE",
    "INSTABILITY",
    "TIMING_LOSS",
    "TAIL_NOT_SETTLED",
]

STOP_REASON_RU: Dict[str, str] = {
    "BREAKDOWN_OR_LEAKAGE_GROWTH": "Пробой или рост утечки",
    "OVERLOAD": "Перегрузка диапазона",
    "COMPLIANCE": "Превышение compliance",
    "INSTABILITY": "Неустойчивость (знакочередующиеся остатки)",
    "TIMING_LOSS": "Потеря временной привязки (заряд в начальном пропуске)",
    "TAIL_NOT_SETTLED": "Хвост не прошёл допуск (stopOnTailFail)",
    "USER_STOP": "Остановлено пользователем",
}

FLAG_DESCRIPTIONS: Dict[str, str] = {
    "PRECONDITION_NOT_CONFIRMED": "§7.1: начальное состояние не подтверждено отдельной подготовкой",
    "TAIL_NOT_SETTLED": "§9: критерий хвоста |B|+k·u(B)≤I_lim или |S|+k·u(S)≤S_lim не выполнен",
    "GAPS_UNACCOUNTED": "пропуски между окнами не учтены (gap-mode reject)",
    "GAPS_BOUNDED_NOT_CORRECTED": "пропуски оценены границей, но не исправлены (bound)",
    "MODEL_DEPENDENT_GAP_CORRECTION": "коррекция пропусков по экспоненциальной модели — модельно зависима",
    "FIT_DIAGNOSTIC_WARNING": "диагностика подгонки: reduced chi² > 5 или обусловленность > 1e8",
    "COMPLIANCE_EXCEEDED": "ток превысил compliance",
    "RANGE_OVERLOAD": "перегрузка диапазона измерения тока",
    "MISSED_START": "§6: начальный импульс мог пройти до первой апертуры (потеря привязки)",
    "INITIAL_GAP_PRESENT": "ненулевая задержка до первой апертуры",
    "FIRST_STEP_DIAGNOSTIC": "§7.1: первый шаг — только диагностический",
    "BLANK_UNAVAILABLE": "режим blank: отдельный фоновый проход не реализован в этом прогоне",
    "BACKGROUND_UNAVAILABLE": "протокол A: вычитание фона недоступно (одиночное окно)",
    "TAIL_FIT_SINGULAR": "взвешенная подгонка хвоста вырождена (окна с нулевым разбросом)",
}

WIRING_ROWS = [
    ("Force HI", "затвор (gate)", "выход источника напряжения"),
    ("Force LO", "подложка/тыльный контакт (back contact)", "возвратный ток"),
    ("Sense HI", "затвор, отдельным выводом", "удалённое измерение потенциала затвора (Kelvin)"),
    ("Sense LO", "подложка, отдельным выводом", "компенсация падения на цепи возврата"),
    ("Guard", "экран кабеля затвора", "не соединять с Force LO; Guard следует потенциалу HI"),
]

EQUIPMENT_CHECKLIST = [
    "Прогрев прибора ≥ 60 минут до self-cal (User's Guide)",
    "*TST? пройден; *CAL? выполнена и записана в паспорт",
    "Четырёхпроводная схема (Kelvin), :SENS:REM ON",
    "Экран (guard) подключён к Guard прибора, НЕ к Force LO",
    "Воспроизводимое положение образца и кабелей (§4)",
    "Температура зафиксирована в паспорте прибора",
]

SAFETY_TEXT = (
    "При работе с заряженными ёмкостями и полупроводниковыми структурами соблюдайте "
    "меры §4: перед подключением/отключением DUT выход прибора выключен (:OUTP OFF), "
    "напряжение источника — 0 В; после завершения — безопасный разряд DUT по выбранному "
    "протоколу (§7.3). Максимальное напряжение B2910BL: ±210 В (User's Guide, табл. 2-1)."
)

# ============================== Модель SMU (порт smu.ts) ==============================

# Измерительные диапазоны тока B2900B/BL (А) — User's Guide, табл. 2-7.
# B2910BL: доступны все, включая 10 нА (разрешение 10 фА) и 100 нА (100 фА).
CURRENT_RANGES = [10e-9, 100e-9, 1e-6, 10e-6, 100e-6, 1e-3, 10e-3, 100e-3]

# Разрешение измерения по диапазонам (А) — User's Guide, табл. 2-7.
CURRENT_RANGE_RESOLUTIONS: Dict[float, float] = {
    10e-9: 10e-15,   # 10 фА — младший диапазон B2910BL
    100e-9: 100e-15,
    1e-6: 1e-12,
    10e-6: 10e-12,
    100e-6: 100e-12,
    1e-3: 1e-9,
    10e-3: 10e-9,
    100e-3: 100e-9,
}

SMU_V_MAX = 210.0  # B2910BL: ±210 В (User's Guide, табл. 2-1/2-3)


def aperture_from_config(cfg: MeasurementConfig) -> float:
    """Длительность апертуры (с) из паспорта программы (§5).

    speedMode: short = 0.01 PLC, medium = 0.1 PLC, normal/auto = 1 PLC,
    long = 10 PLC, manual: nplc/lineFreq либо aperture_s."""
    if cfg.speedMode == "short":
        return 0.01 / cfg.lineFreq_Hz
    if cfg.speedMode == "medium":
        return 0.1 / cfg.lineFreq_Hz
    if cfg.speedMode in ("normal", "auto"):
        return 1.0 / cfg.lineFreq_Hz
    if cfg.speedMode == "long":
        return 10.0 / cfg.lineFreq_Hz
    if cfg.speedMode == "manual":
        if cfg.apertureMode == "nplc":
            if not (cfg.lineFreq_Hz > 0):
                raise ValueError("Некорректная частота сети (lineFreq_Hz должна быть > 0)")
            return cfg.nplc / cfg.lineFreq_Hz
        return cfg.aperture_s
    raise ValueError(f"Неизвестный speedMode: {cfg.speedMode}")


def aperture_times(cfg: MeasurementConfig) -> Tuple[List[float], List[float]]:
    """Границы окон [a_j, b_j] от триггера перехода (§5, §6).

    Протокол B: a_j = (j−1)·trigInterval + acqDelay; b_j = a_j + aperture.
    Протокол A: одна апертура a_1 = acqDelay."""
    aperture = aperture_from_config(cfg)
    if not (aperture > 0) or not math.isfinite(aperture):
        raise ValueError(f"Некорректная длительность апертуры: {aperture} с")
    if cfg.protocol == "kuhn_staircase":
        return [cfg.acqDelay_s], [cfg.acqDelay_s + aperture]
    if cfg.trigCount < 1:
        raise ValueError("trigCount должен быть ≥ 1")
    if aperture > cfg.trigInterval_s:
        raise ValueError(
            f"Длительность апертуры ({aperture:.6g} с) превышает период триггера "
            f"({cfg.trigInterval_s:.6g} с): уменьшите апертуру или увеличьте интервал")
    a: List[float] = []
    b: List[float] = []
    for j in range(1, cfg.trigCount + 1):
        aj = (j - 1) * cfg.trigInterval_s + cfg.acqDelay_s
        a.append(aj)
        b.append(aj + aperture)
    return a, b


def pick_range(i_abs: float, cfg: MeasurementConfig) -> Tuple[float, bool]:
    """Выбор диапазона измерения тока по |I|.

    fixed: диапазон iRange_A; перегрузка — |iAbs| > 1.05·range.
    auto: наименьший диапазон ≥ |iAbs|/1.05."""
    if cfg.iRangeMode == "fixed":
        return cfg.iRange_A, i_abs > 1.05 * cfg.iRange_A
    rng = CURRENT_RANGES[-1]
    for r in CURRENT_RANGES:
        if r >= i_abs / 1.05:
            rng = r
            break
    return rng, i_abs > 1.05 * rng


def smu_noise_config(cfg: MeasurementConfig) -> Tuple[Callable[[float], float], float]:
    """Шумовая модель SMU (симуляция): uI(i) = uIOffset_A + uIGainRel·|i|."""
    return (lambda i_abs: cfg.uIOffset_A + cfg.uIGainRel * abs(i_abs)), cfg.iOffset_A


# ============================== Физическая модель DUT (порт model.ts) ==============================

def _softplus(z: float) -> float:
    return z + math.log1p(math.exp(-z)) if z > 0 else math.log1p(math.exp(z))


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def fast_charge(v: float, dut: DutModel) -> float:
    """Q_fast(V): быстрый заряд ветви. linear/ref_*: c0·v;
    mos_demo: cMin·V − (cOx − cMin)·width·softplus((vfb − V)/width) + const."""
    if dut.kind != "mos_demo":
        return dut.c0_F * v
    w = dut.width_V
    z = (dut.vfb_V - v) / w
    return dut.cMin_F * v - (dut.cOx_F - dut.cMin_F) * w * _softplus(z)


def fast_capacitance(v: float, dut: DutModel) -> float:
    """C_fast(V) = dQ_fast/dV; mos_demo: cMin + (cOx − cMin)·sigmoid((vfb−v)/width)."""
    if dut.kind != "mos_demo":
        return dut.c0_F
    z = (dut.vfb_V - v) / dut.width_V
    return dut.cMin_F + (dut.cOx_F - dut.cMin_F) * _sigmoid(z)


def equilibrium_charge(v: float, dut: DutModel) -> float:
    """Q_eq(V) = Q_fast(V) + Σ c_m·V. Методика §3: C_qs = dQ_eq/dV."""
    q = fast_charge(v, dut)
    for br in dut.branches:
        q += br.c_F * v
    return q


# U_fast для mos_demo: кумулятивная трапеция по сетке (кэш на объекте dut).
_UFAST_POINTS = 20000


def _build_ufast_grid(dut: DutModel, half_width: float) -> Tuple[float, List[float]]:
    h = (2 * half_width) / _UFAST_POINTS
    cum = [0.0] * (_UFAST_POINTS + 1)
    prev = -half_width * fast_capacitance(-half_width, dut)
    for i in range(1, _UFAST_POINTS + 1):
        x = -half_width + i * h
        f = x * fast_capacitance(x, dut)
        cum[i] = cum[i - 1] + ((prev + f) / 2) * h
        prev = f
    return half_width, cum


def _ufast_interp(entry: Tuple[float, List[float]], v: float) -> float:
    half_width, cum = entry
    h = (2 * half_width) / _UFAST_POINTS
    pos = (v + half_width) / h
    i = max(0, min(_UFAST_POINTS - 1, int(math.floor(pos))))
    frac = pos - i
    return cum[i] + (cum[i + 1] - cum[i]) * frac


def fast_stored_energy(v: float, dut: DutModel) -> float:
    """Запасённая энергия быстрой ветви: ½·c0·v² (линейные) или Ufast(v) (mos_demo)."""
    if dut.kind != "mos_demo":
        return 0.5 * dut.c0_F * v * v
    entry = getattr(dut, "_ufast_cache", None)
    if entry is None or abs(v) > entry[0]:
        entry = _build_ufast_grid(dut, abs(v) + 1)
        dut._ufast_cache = entry  # type: ignore[attr-defined]
    return _ufast_interp(entry, v) - _ufast_interp(entry, 0.0)


@dataclass
class ModelState:
    x: List[float] = field(default_factory=list)  # напряжения на C_m релаксационных ветвей


@dataclass
class VoltKnot:
    t: float
    v: float


@dataclass
class SegmentResult:
    xNew: List[float]
    qFast: float
    qBranch: List[float]
    qLeak: float
    wPort: float
    dissipated: float
    dU: float


def propagate_segment(state: ModelState, v0: float, s: float, d: float,
                      dut: DutModel) -> SegmentResult:
    """Точное распространение состояния по ЛИНЕЙНОМУ сегменту v(t) = v0 + s·t,
    t ∈ [0, d] (порт physical_observer.py, trajectory). Все интегралы точные."""
    v1 = v0 + s * d
    n = len(dut.branches)

    v_integral = v0 * d + (s * d * d) / 2
    v2_integral = v0 * v0 * d + v0 * s * d * d + (s * s * d * d * d) / 3
    leak_work = dut.g_S * v2_integral
    leak_charge = dut.g_S * v_integral

    x_new: List[float] = [0.0] * n
    q_branch: List[float] = [0.0] * n
    branch_work_sum = 0.0
    branch_loss_sum = 0.0
    branch_du = 0.0

    for m in range(n):
        C = dut.branches[m].c_F
        tau = dut.branches[m].tau_s
        x = state.x[m] if m < len(state.x) else 0.0
        z = d / tau
        e = math.exp(-z)
        one = -math.expm1(-z)
        A = (v0 - x) / tau - s
        x2 = x + s * d + A * tau * one
        if z > 1e-3:
            moment = tau * tau * (one - z * e)
        else:
            z2 = z * z
            z3 = z2 * z
            z4 = z3 * z
            z5 = z4 * z
            z6 = z5 * z
            moment = tau * tau * (z2 / 2 - z3 / 3 + z4 / 8 - z5 / 30 + z6 / 144)
        branch_work = C * (s * v_integral + A * (v0 * tau * one + s * moment))
        branch_loss = C * tau * (s * s * d + 2 * s * A * tau * one +
                                 (A * A * tau * -math.expm1(-2 * z)) / 2)
        x_new[m] = x2
        q_branch[m] = C * (x2 - x)
        branch_work_sum += branch_work
        branch_loss_sum += branch_loss
        branch_du += 0.5 * C * (x2 * x2 - x * x)

    u0 = fast_stored_energy(v0, dut)
    u1 = fast_stored_energy(v1, dut)
    q_fast = fast_charge(v1, dut) - fast_charge(v0, dut)

    return SegmentResult(
        xNew=x_new, qFast=q_fast, qBranch=q_branch, qLeak=leak_charge,
        wPort=u1 - u0 + leak_work + branch_work_sum,
        dissipated=leak_work + branch_loss_sum,
        dU=u1 - u0 + branch_du,
    )


def settle_state(v: float, dut: DutModel) -> ModelState:
    """Полностью релаксированное состояние при напряжении v: x_m = v."""
    return ModelState([v for _ in dut.branches])


def relax_state(state: ModelState, v_hold: float, duration: float, dut: DutModel) -> ModelState:
    """Аналитическая релаксация при постоянном vHold за время duration:
    x_m' = vHold + (x_m − vHold)·e^(−duration/tau_m)."""
    xs: List[float] = []
    for m, br in enumerate(dut.branches):
        x = state.x[m] if m < len(state.x) else v_hold
        if duration <= 0:
            xs.append(x)
        else:
            xs.append(v_hold + (x - v_hold) * math.exp(-duration / br.tau_s))
    return ModelState(xs)


def didt_at(state: ModelState, v: float, dut: DutModel) -> float:
    """|dI/dt| при постоянном v: Σ_m C_m·|v − x_m|/tau_m²."""
    total = 0.0
    for m, br in enumerate(dut.branches):
        x = state.x[m] if m < len(state.x) else v
        total += (br.c_F * abs(v - x)) / (br.tau_s * br.tau_s)
    return total


@dataclass
class WalkRange:
    l: float
    r: float
    qFast: float = 0.0
    qBranch: List[float] = field(default_factory=list)
    qLeak: float = 0.0


@dataclass
class ProfileWalk:
    stateEnd: ModelState
    wPort: float
    dissipated: float
    dU: float
    residual: float  # wPort − dissipated − dU (контроль баланса §10)
    qFast: float
    qBranch: List[float]
    qLeak: float


def walk_profile(knots: List[VoltKnot], state0: ModelState, ranges: List[WalkRange],
                 dut: DutModel) -> ProfileWalk:
    """Один проход по кусочно-линейному профилю: точное распространение состояния
    через ВСЕ сегменты (включая пропуски наблюдения) с накоплением зарядов
    в заданные диапазоны [l, r]. Границы диапазонов — узлы профиля."""
    state = ModelState(list(state0.x))
    w_port = 0.0
    dissipated = 0.0
    d_u = 0.0
    q_leak = 0.0

    for k in range(len(knots) - 1):
        t0 = knots[k].t
        t1 = knots[k + 1].t
        d = t1 - t0
        if d <= 0:
            continue
        v0 = knots[k].v
        s = (knots[k + 1].v - v0) / d
        res = propagate_segment(state, v0, s, d, dut)
        state = ModelState(res.xNew)
        w_port += res.wPort
        dissipated += res.dissipated
        d_u += res.dU
        q_leak += res.qLeak
        mid = (t0 + t1) / 2
        for rg in ranges:
            if rg.l <= mid <= rg.r:
                rg.qFast += res.qFast
                for m in range(len(res.qBranch)):
                    if m >= len(rg.qBranch):
                        rg.qBranch.extend([0.0] * (m + 1 - len(rg.qBranch)))
                    rg.qBranch[m] += res.qBranch[m]
                rg.qLeak += res.qLeak
                break

    q_branch_total = [0.0] * len(dut.branches)
    for m, br in enumerate(dut.branches):
        x0 = state0.x[m] if m < len(state0.x) else 0.0
        x1 = state.x[m] if m < len(state.x) else 0.0
        q_branch_total[m] = br.c_F * (x1 - x0)
    q_fast_total = (fast_charge(knots[-1].v, dut) - fast_charge(knots[0].v, dut)
                    if knots else 0.0)

    return ProfileWalk(state, w_port, dissipated, d_u,
                       w_port - dissipated - d_u, q_fast_total, q_branch_total, q_leak)


@dataclass
class WindowChargeResult:
    iAvg: float
    qFast: float
    qBranch: List[float]
    qLeak: float
    stateAtB: ModelState


def window_charge(knots: List[VoltKnot], t_start_state: ModelState, a: float, b: float,
                  dut: DutModel) -> WindowChargeResult:
    """Истинный заряд окна [a, b]: состояние непрерывно переносится от knots[0].t до b."""
    if len(knots) < 2:
        raise ValueError("windowCharge: профиль напряжения должен содержать минимум 2 узла")
    t_first = knots[0].t
    t_last = knots[-1].t
    if not (a >= t_first) or not (b <= t_last) or not (b > a):
        raise ValueError(f"windowCharge: окно [{a}, {b}] вне профиля [{t_first}, {t_last}] или пусто")

    state = ModelState(list(t_start_state.x))
    q_fast = 0.0
    q_branch = [0.0] * len(dut.branches)
    q_leak = 0.0

    for k in range(len(knots) - 1):
        t0 = knots[k].t
        t1 = knots[k + 1].t
        d = t1 - t0
        if d <= 0:
            continue
        if t0 >= b:
            break
        v0k = knots[k].v
        s = (knots[k + 1].v - v0k) / d

        pre_end = min(t1, a)
        if pre_end > t0:
            res = propagate_segment(state, v0k, s, pre_end - t0, dut)
            state = ModelState(res.xNew)

        l = max(t0, a)
        r = min(t1, b)
        if r > l:
            v0sub = v0k + s * (l - t0)
            res = propagate_segment(state, v0sub, s, r - l, dut)
            q_fast += res.qFast
            for m in range(len(res.qBranch)):
                q_branch[m] += res.qBranch[m]
            q_leak += res.qLeak
            state = ModelState(res.xNew)
        if b <= t1:
            break

    total = q_fast + q_leak + sum(q_branch)
    return WindowChargeResult(total / (b - a), q_fast, q_branch, q_leak, state)

# ============================== Офлайн-анализатор (порт analysis.ts / qscv_final.py) ==============================
#
# Чистые функции. Нарушения данных собираются в flags 'DATA_INVALID:…' / 'INVALID_UI'
# вместо исключений. Постоянная составляющая B подгонки НЕ интегрируется в хвосте
# до бесконечности — remainingTail записывается, но НЕ прибавляется к результату.

@dataclass
class StepAnalysisInput:
    a: List[float]
    b: List[float]
    i: List[float]
    uI: List[float]
    leak: List[float]
    uLeakInd: List[float]
    uDt: List[float]
    preSettled: bool
    dv: float
    vBefore: float
    vAfter: float
    options: AnalyzeOptions


@dataclass
class StepAnalysisOutput:
    qObs: float
    uQ: float
    qGapModel: float
    qGapBound: float
    qCorrected: float
    cSec: float
    uC: float
    tailB: float
    tailS: float
    tailUB: float
    tailUS: float
    tailOk: bool
    gaps: List[Tuple[float, float]]
    gapDuration: float
    fit: Optional[FitInfo]
    flags: List[str]
    usableForPair: bool


def _zero_output(flags: List[str]) -> StepAnalysisOutput:
    return StepAnalysisOutput(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, False, [], 0.0, None, flags, False)


def mean_exp(a: float, b: float, tau: float) -> float:
    """Среднее значение e^(−t/τ) по окну [a, b] (порт mean_exp)."""
    return tau * math.exp(-a / tau) * -math.expm1(-(b - a) / tau) / (b - a)


def exp_integral(p: Sequence[float], l: float, r: float, n: int) -> float:
    """∫ по [l, r] модели i(t) = B + Σ A_m·e^(−t/τ_m) (порт exp_integral)."""
    val = p[0] * (r - l)
    for m in range(n):
        tau = math.exp(p[2 + 2 * m])
        val += p[1 + 2 * m] * tau * (math.exp(-l / tau) - math.exp(-r / tau))
    return val


def exp_pred(p: Sequence[float], a: Sequence[float], b: Sequence[float], n: int) -> List[float]:
    """Предсказанные средние токи окон (параметры в масштабированных единицах)."""
    y = [p[0]] * len(a)
    for m in range(n):
        tau = math.exp(p[2 + 2 * m])
        amp = p[1 + 2 * m]
        for j in range(len(a)):
            y[j] += amp * mean_exp(a[j], b[j], tau)
    return y


def geomspace(start: float, end: float, n: int) -> List[float]:
    if n <= 1:
        return [start]
    return [start * (end / start) ** (i / (n - 1)) for i in range(n)]


# ------------------------------ Линейная алгебра ------------------------------

def solve_linear(A: List[List[float]], b: List[float]) -> Optional[List[float]]:
    """Решение A·x = b (Гаусс с частичным выбором ведущего элемента); None при вырождении."""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = col
        for i in range(col + 1, n):
            if abs(M[i][col]) > abs(M[piv][col]):
                piv = i
        if not math.isfinite(M[piv][col]) or abs(M[piv][col]) < 1e-300:
            return None
        if piv != col:
            M[piv], M[col] = M[col], M[piv]
        for i in range(col + 1, n):
            f = M[i][col] / M[col][col]
            if f == 0:
                continue
            for k in range(col, n + 1):
                M[i][k] -= f * M[col][k]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = M[i][n]
        for k in range(i + 1, n):
            s -= M[i][k] * x[k]
        x[i] = s / M[i][i]
    return x


def jacobi_eigen(A_in: List[List[float]]) -> Tuple[List[float], List[List[float]]]:
    """Собственные значения/векторы симметричной матрицы (метод вращений Якоби)."""
    n = len(A_in)
    A = [row[:] for row in A_in]
    V = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _sweep in range(100):
        off = 0.0
        for i in range(n):
            for j in range(i + 1, n):
                off += A[i][j] * A[i][j]
        if off < 1e-28:
            break
        for p in range(n - 1):
            for q in range(p + 1, n):
                apq = A[p][q]
                if apq == 0 or not math.isfinite(apq):
                    continue
                app = A[p][p]
                aqq = A[q][q]
                theta = (aqq - app) / (2 * apq)
                t = (1 if theta >= 0 else -1) / (abs(theta) + math.sqrt(theta * theta + 1))
                c = 1 / math.sqrt(t * t + 1)
                s = t * c
                A[p][p] = app - t * apq
                A[q][q] = aqq + t * apq
                A[p][q] = 0.0
                A[q][p] = 0.0
                for r in range(n):
                    if r == p or r == q:
                        continue
                    arp = A[r][p]
                    arq = A[r][q]
                    nrp = c * arp - s * arq
                    nrq = s * arp + c * arq
                    A[r][p] = nrp
                    A[p][r] = nrp
                    A[r][q] = nrq
                    A[q][r] = nrq
                for r in range(n):
                    vrp = V[r][p]
                    vrq = V[r][q]
                    V[r][p] = c * vrp - s * vrq
                    V[r][q] = s * vrp + c * vrq
    return [A[i][i] for i in range(n)], V


def pinv_sym(H: List[List[float]]) -> Tuple[List[List[float]], float]:
    """Псевдообратная симметричной матрицы через собственное разложение;
    tol = max|λ|·1e-12; cond = sqrt(λmax/λmin>tol)."""
    n = len(H)
    if n == 0:
        return [], math.inf
    values, vectors = jacobi_eigen(H)
    max_abs = max(abs(v) for v in values) if values else 0.0
    if not (max_abs > 0):
        return [[0.0] * n for _ in range(n)], math.inf
    tol = max_abs * 1e-12
    lambda_max = -math.inf
    lambda_min = math.inf
    for v in values:
        lambda_max = max(lambda_max, v)
        if v > tol:
            lambda_min = min(lambda_min, v)
    cond = (math.sqrt(lambda_max / lambda_min)
            if lambda_max > 0 and math.isfinite(lambda_min) and lambda_min > 0
            else math.inf)
    inv = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            s = 0.0
            for k in range(n):
                if values[k] > tol:
                    s += (vectors[i][k] * vectors[j][k]) / values[k]
            inv[i][j] = s
    return inv, cond


# ------------------------------ Levenberg–Marquardt ------------------------------

def numerical_jacobian(residual: Callable[[List[float]], List[float]], p: List[float],
                       r0: List[float], lower: List[float],
                       upper: List[float]) -> List[List[float]]:
    """Центрально-разностный якобиан с ограничением шага рамками параметров."""
    n = len(p)
    m = len(r0)
    J = [[0.0] * n for _ in range(m)]
    for j in range(n):
        h = max(abs(p[j]) * 1e-6, 1e-8)
        hi = min(upper[j], p[j] + h)
        lo = max(lower[j], p[j] - h)
        step = hi - lo
        if not (step > 0) or not math.isfinite(step):
            continue
        p_hi = p[:]
        p_hi[j] = hi
        p_lo = p[:]
        p_lo[j] = lo
        r_hi = residual(p_hi)
        r_lo = residual(p_lo)
        for i in range(m):
            J[i][j] = (r_hi[i] - r_lo[i]) / step
    return J


def lm_fit(residual: Callable[[List[float]], List[float]], p0: List[float],
           lower: List[float], upper: List[float],
           max_nfev: int) -> Tuple[List[float], bool, int]:
    """Ограниченный LM-подгонщик (порт lmFit из analysis.ts)."""
    n = len(p0)

    def clamp(p: List[float]) -> List[float]:
        return [min(max(v, lower[i]), upper[i]) for i, v in enumerate(p)]

    p = clamp(p0[:])
    r = residual(p)
    nfev = 1
    cost = 0.5 * sum(v * v for v in r)
    lam = 1e-3
    converged = False
    stall = 0
    no_progress = 0
    rejections = 0

    for _it in range(2000):
        J = numerical_jacobian(residual, p, r, lower, upper)
        g = [0.0] * n
        H = [[0.0] * n for _ in range(n)]
        for i in range(len(J)):
            for j in range(n):
                g[j] += J[i][j] * r[i]
                for k in range(j, n):
                    H[j][k] += J[i][j] * J[i][k]
        for j in range(n):
            for k in range(j):
                H[j][k] = H[k][j]
        M = [[H[j][k] * (1 + lam) + 1e-12 if j == k else H[j][k] for k in range(n)]
             for j in range(n)]
        delta = solve_linear(M, [-v for v in g])
        if delta is None:
            lam *= 10
            if lam > 1e14:
                break
            continue
        p_new = clamp([v + delta[i] for i, v in enumerate(p)])
        r_new = residual(p_new)
        nfev += 1
        cost_new = 0.5 * sum(v * v for v in r_new)
        if cost_new < cost:
            d_cost = cost - cost_new
            p, r, cost = p_new, r_new, cost_new
            lam = max(lam / 3, 1e-12)
            rejections = 0
            if d_cost <= 1e-12 * max(1, cost):
                stall += 1
                if stall >= 2:
                    converged = True
                    break
            else:
                stall = 0
            # ftol-подобный критерий (ослабленный относительно scipy ftol=1e-8)
            if d_cost <= 1e-6 * max(1, cost):
                no_progress += 1
                if no_progress >= 8:
                    converged = True
                    break
            else:
                no_progress = 0
            if max(abs(v) for v in delta) < 1e-12:
                converged = True
                break
        else:
            lam *= 4
            stall = 0
            rejections += 1
            if rejections >= 20:
                converged = True
                break
            if lam > 1e14:
                break
        if nfev >= max_nfev:
            break
    return p, converged, nfev


@dataclass
class ExpFitResult:
    physical: List[float]
    covPhys: List[List[float]]
    red: float
    cond: float
    success: bool


def fit_exponential(a: List[float], b: List[float], y: List[float], u: List[float],
                    nexp: int) -> ExpFitResult:
    """Порт fit_exponential (qscv_final.py): масштабированные параметры
    [B/scale, A1/scale, ln τ1, ...], LM-подгонка, ковариация через pinv."""
    length = len(y)
    scale = max(max(abs(v) for v in y), max(u) if u else 0.0, 1e-30)

    min_dt = min(bj - aj for aj, bj in zip(a, b))
    lo = max(min_dt / 100, 1e-9)
    hi = max(b[-1] * 100, lo * 1000)

    p0: List[float] = [y[-1] / scale]
    for tau in geomspace(max(lo * 10, b[-1] / 100), max(lo * 20, b[-1] / 2), nexp):
        p0.append((y[0] - y[-1]) / scale / nexp)
        p0.append(math.log(tau))
    lower: List[float] = [-math.inf]
    upper: List[float] = [math.inf]
    for _m in range(nexp):
        lower += [-math.inf, math.log(lo)]
        upper += [math.inf, math.log(hi)]

    def residual(p: List[float]) -> List[float]:
        pred = exp_pred(p, a, b, nexp)
        return [(pred[j] - y[j] / scale) / (u[j] / scale) for j in range(length)]

    p, converged, _nfev = lm_fit(residual, p0, lower, upper, 10000)

    # Диагностика в точке решения.
    r = residual(p)
    J = numerical_jacobian(residual, p, r, lower, upper)
    pc = 1 + 2 * nexp
    H = [[0.0] * pc for _ in range(pc)]
    for i in range(len(J)):
        for j in range(pc):
            for k in range(pc):
                H[j][k] += J[i][j] * J[i][k]
    ssq = sum(v * v for v in r)
    dof = length - pc
    red = ssq / (dof if dof > 0 else 1)
    inv, cond = pinv_sym(H)
    scale_red = max(1, red)
    cov_scaled = [[v * scale_red for v in row] for row in inv]

    transform = [1.0] * pc
    transform[0] = scale
    for m in range(nexp):
        transform[1 + 2 * m] = scale
    physical = [p[i] * transform[i] for i in range(pc)]
    cov_phys = [[cov_scaled[i][j] * transform[i] * transform[j] for j in range(pc)]
                for i in range(pc)]
    return ExpFitResult(physical, cov_phys, red, cond, converged)


def numerical_uncertainty(fun: Callable[[List[float]], float], p: List[float],
                          cov: List[List[float]]) -> float:
    """Порт numerical_uncertainty: центральные разности, шаг из qscv_final."""
    n = len(p)
    grad = [0.0] * n
    for j in range(n):
        floor_ = 1e-18 if (j == 0 or j % 2 == 1) else 1e-6
        h = max(abs(p[j]) * 1e-5, floor_)
        pp = p[:]
        pp[j] = p[j] + h
        pm = p[:]
        pm[j] = p[j] - h
        grad[j] = (fun(pp) - fun(pm)) / (2 * h)
    acc = 0.0
    for i in range(n):
        for j in range(n):
            acc += grad[i] * cov[i][j] * grad[j]
    return math.sqrt(max(0, acc))


# ------------------------------ Анализ шага (порт analyseStep) ------------------------------

def analyse_step(inp: StepAnalysisInput) -> StepAnalysisOutput:
    a, b, i, uI, leak, uLeakInd, uDt = (inp.a, inp.b, inp.i, inp.uI, inp.leak,
                                        inp.uLeakInd, inp.uDt)
    options = inp.options
    length = len(a)
    flags: List[str] = []

    def invalid(flag: str) -> StepAnalysisOutput:
        return _zero_output([flag])

    if length == 0 or any(len(arr) != length for arr in (b, i, uI, leak, uLeakInd, uDt)):
        return invalid("DATA_INVALID:length_mismatch")
    all_numeric = [a, b, i, uI, leak, uLeakInd, uDt, [inp.vBefore], [inp.vAfter], [inp.dv]]
    for arr in all_numeric:
        for v in arr:
            if not math.isfinite(v):
                return invalid("DATA_INVALID:nonfinite")
    for arr in (uI, uLeakInd, uDt):
        for v in arr:
            if v < 0:
                return invalid("DATA_INVALID:negative_uncertainty")
    for j in range(length):
        if not (b[j] - a[j] > 0):
            return invalid("DATA_INVALID:nonpositive_window")
    if a[0] < 0:
        return invalid("DATA_INVALID:negative_start")
    for j in range(1, length):
        if a[j] < b[j - 1] - 1e-12:
            return invalid("DATA_INVALID:overlap")
    if length < max(options.tailPoints, 8):
        return invalid("DATA_INVALID:too_few_windows")
    dv = inp.dv
    if dv == 0:
        return invalid("DATA_INVALID:zero_voltage_step")

    y = [i[j] - leak[j] for j in range(length)]
    ui = [math.hypot(uI[j], uLeakInd[j]) for j in range(length)]
    for v in ui:
        if not (v > 0):
            return invalid("INVALID_UI")

    # Пропуски: от 0 до первой, между окнами.
    gaps: List[Tuple[float, float]] = []
    edge = 0.0
    for j in range(length):
        if a[j] - edge > 1e-12:
            gaps.append((edge, a[j]))
        edge = b[j]
    gap_duration = sum(r - l for l, r in gaps)

    # Хвост: взвешенная линейная подгонка y = B + S·(t − t̄) по последним n окнам.
    n = min(options.tailPoints, length)
    t: List[float] = []
    yy: List[float] = []
    uu: List[float] = []
    for j in range(length - n, length):
        t.append((a[j] + b[j]) / 2)
        yy.append(y[j])
        uu.append(ui[j])
    t_mean = sum(t) / n

    S11 = S1x = Sxx = Sy = Sxy = 0.0
    for j in range(n):
        w = 1 / (uu[j] * uu[j])
        x = t[j] - t_mean
        S11 += w
        S1x += w * x
        Sxx += w * x * x
        Sy += w * yy[j]
        Sxy += w * x * yy[j]
    det = S11 * Sxx - S1x * S1x
    tail_b = tail_s = 0.0
    tail_ub = tail_us = 0.0
    tail_ok = False
    if not math.isfinite(det) or abs(det) < 1e-300:
        flags.append("TAIL_FIT_SINGULAR")
        tail_ub = tail_us = math.inf
    else:
        inv00 = Sxx / det
        inv01 = -S1x / det
        inv11 = S11 / det
        tail_b = inv00 * Sy + inv01 * Sxy
        tail_s = inv01 * Sy + inv11 * Sxy
        chi2 = 0.0
        for j in range(n):
            resid = tail_b + tail_s * (t[j] - t_mean) - yy[j]
            chi2 += (resid * resid) / (uu[j] * uu[j])
        red_tail = chi2 / max(1, n - 2)
        scale_cov = max(1, red_tail)
        cov00 = inv00 * scale_cov
        cov11 = inv11 * scale_cov
        tail_ub = math.sqrt(cov00 + options.uCurrentCommon_A ** 2 + options.uLeakCommon_A ** 2)
        tail_us = math.sqrt(cov11)
        tail_ok = (abs(tail_b) + options.kFactor * tail_ub <= options.tailLimit_A and
                   abs(tail_s) + options.kFactor * tail_us <= options.slopeLimit_A_s)

    # Заряд и бюджет неопределённости (§12).
    q = 0.0
    u_ind_sq = 0.0
    duration = 0.0
    for j in range(length):
        dt = b[j] - a[j]
        q += y[j] * dt
        u_ind_sq += (dt * ui[j]) ** 2 + (y[j] * uDt[j]) ** 2
        duration += dt
    u_ind = math.sqrt(u_ind_sq)
    u_q = math.sqrt(u_ind ** 2 +
                    (duration * options.uCurrentCommon_A) ** 2 +
                    (duration * options.uLeakCommon_A) ** 2 +
                    (q * options.uClockRel) ** 2)

    if not inp.preSettled:
        flags.append("PRECONDITION_NOT_CONFIRMED")
    if not tail_ok:
        flags.append("TAIL_NOT_SETTLED")

    # Экспоненциальная подгонка.
    q_gap = 0.0
    gap_bound = 0.0
    fit: Optional[FitInfo] = None
    nexp = 0
    fit_physical: Optional[List[float]] = None
    fit_cov: Optional[List[List[float]]] = None

    if options.model in ("exp1", "exp2"):
        nexp = 1 if options.model == "exp1" else 2
        if length <= 1 + 2 * nexp:
            return invalid("DATA_INVALID:insufficient_samples_for_fit")
        f = fit_exponential(a, b, y, ui, nexp)
        fit_physical = f.physical
        fit_cov = f.covPhys
        components: List[FitComponent] = []
        remaining_tail = 0.0
        for m in range(nexp):
            tau = math.exp(f.physical[2 + 2 * m])
            amp = f.physical[1 + 2 * m]
            qc = amp * tau
            components.append(FitComponent(tau, amp, qc))
            remaining_tail += qc * math.exp(-b[-1] / tau)
        components.sort(key=lambda c: c.tau_s)
        fit = FitInfo(model=options.model, baseline_A=f.physical[0], components=components,
                      reducedChi2=f.red, condition=f.cond, success=f.success,
                      remainingTail_C=remaining_tail, uGapFitOnly_C=0.0)
        if not f.success or f.red > 5 or f.cond > 1e8:
            flags.append("FIT_DIAGNOSTIC_WARNING")

    # Пропуски: reject / bound / model.
    if gaps:
        if options.gapMode == "reject":
            flags.append("GAPS_UNACCOUNTED")
        elif options.gapMode == "bound":
            gap_bound = gap_duration * options.gapCurrentBound_A
            flags.append("GAPS_BOUNDED_NOT_CORRECTED")
        else:
            if fit_physical is not None and fit_cov is not None:
                def fun(pp: List[float]) -> float:
                    return sum(exp_integral(pp, l, r, nexp) for l, r in gaps)
                q_gap = fun(fit_physical)
                if fit is not None:
                    fit.uGapFitOnly_C = numerical_uncertainty(fun, fit_physical, fit_cov)
                flags.append("MODEL_DEPENDENT_GAP_CORRECTION")
            else:
                flags.append("GAPS_UNACCOUNTED")

    corrected = q + q_gap
    c_sec = corrected / dv
    u_c = math.sqrt((u_q / dv) ** 2 + ((corrected * options.uDv_V) / (dv * dv)) ** 2)

    return StepAnalysisOutput(
        qObs=q, uQ=u_q, qGapModel=q_gap, qGapBound=gap_bound, qCorrected=corrected,
        cSec=c_sec, uC=u_c, tailB=tail_b, tailS=tail_s, tailUB=tail_ub, tailUS=tail_us,
        tailOk=tail_ok, gaps=gaps, gapDuration=gap_duration, fit=fit, flags=flags,
        usableForPair=inp.preSettled and tail_ok and not gaps,
    )


# ------------------------------ Пары прямой/обратный ход ------------------------------

def interp_linear(xs: List[float], ys: List[float], x: float) -> float:
    if len(xs) == 1:
        return ys[0]
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) >> 1
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    f = (x - xs[lo]) / (xs[hi] - xs[lo])
    return ys[lo] + f * (ys[hi] - ys[lo])


def pair_forward_reverse(rows: List[AnalyzeStepRow]) -> Tuple[List[FwdRevPair], List[str]]:
    """Порт main из qscv_final.py: интерполяция обратного хода к vMid прямого."""
    pairs: List[FwdRevPair] = []
    errors: List[str] = []
    runs: Dict[str, List[AnalyzeStepRow]] = {}
    for row in rows:
        runs.setdefault(row.run, []).append(row)
    for run, rs in runs.items():
        f = sorted((r for r in rs if r.direction == "forward" and r.usableForPair),
                   key=lambda r: r.vMid_V)
        r = sorted((x for x in rs if x.direction == "reverse" and x.usableForPair),
                   key=lambda x: x.vMid_V)
        if not f or not r:
            continue
        if any(r[k].vMid_V == r[k - 1].vMid_V for k in range(1, len(r))):
            errors.append(f"Прогон «{run}»: дубликаты vMid в обратном ходе — "
                          "разделите циклы на отдельные прогоны")
            continue
        for fr in f:
            x = fr.vMid_V
            if len(r) < 2 or x < r[0].vMid_V or x > r[-1].vMid_V:
                continue
            c_rev = interp_linear([q.vMid_V for q in r], [q.cSec_F for q in r], x)
            pairs.append(FwdRevPair(run, x, fr.cSec_F, c_rev, fr.cSec_F - c_rev))
    return pairs, errors


def analyze_acquisition(rows: List[AcquisitionRow], options: AnalyzeOptions) -> AnalyzeResult:
    """Порт analyse_qs + main из qscv_final.py: группировка по (run, direction, step)."""
    steps: List[AnalyzeStepRow] = []
    errors: List[str] = []
    groups: Dict[Tuple[str, str, float], List[AcquisitionRow]] = {}
    order: List[Tuple[str, str, float]] = []
    for r in rows:
        key = (r.run, r.direction, r.step)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    for key in order:
        g = sorted(groups[key], key=lambda r: r.a_s)
        req = ["v_before_V", "v_after_V", "a_s", "b_s", "i_A", "u_i_A", "leak_A",
               "u_leak_ind_A", "u_dt_s"]
        if any(not math.isfinite(getattr(g[0], c)) for c in req):
            errors.append(f"{key}: нечисловые данные")
            continue
        v_bef = g[0].v_before_V
        v_aft = g[0].v_after_V
        if len({r.v_before_V for r in g}) != 1 or len({r.v_after_V for r in g}) != 1:
            errors.append(f"{key}: непоследовательное напряжение шага")
            continue
        a = [r.a_s for r in g]
        b = [r.b_s for r in g]
        dv = v_aft - v_bef
        if dv == 0:
            errors.append(f"{key}: нулевой шаг напряжения")
            continue
        pre = all(r.pre_settled for r in g)
        inp = StepAnalysisInput(
            a=a, b=b,
            i=[r.i_A for r in g],
            uI=[r.u_i_A for r in g],
            leak=[r.leak_A for r in g],
            uLeakInd=[r.u_leak_ind_A for r in g],
            uDt=[r.u_dt_s for r in g],
            preSettled=pre, dv=dv, vBefore=v_bef, vAfter=v_aft,
            options=options,
        )
        out = analyse_step(inp)
        if out.flags and out.flags[0].startswith("DATA_INVALID"):
            errors.append(f"{key}: {out.flags[0]}")
            continue
        steps.append(AnalyzeStepRow(
            run=key[0], direction=key[1], step=key[2],
            vMid_V=(v_bef + v_aft) / 2,
            qObserved_C=out.qObs, uQ_C=out.uQ,
            qGapModel_C=out.qGapModel, qGapBound_C=out.qGapBound,
            qCorrected_C=out.qCorrected, cSec_F=out.cSec, uC_F=out.uC,
            tailCurrent_A=out.tailB, tailSlope_A_s=out.tailS,
            tailSettled=out.tailOk, preSettled=pre,
            gapDuration_s=out.gapDuration, usableForPair=out.usableForPair,
            flags=out.flags, fit=out.fit, gaps_s=out.gaps,
        ))

    pairs, pair_errors = pair_forward_reverse(steps)
    return AnalyzeResult(steps=steps, pairs=pairs, errors=errors + pair_errors)

# ============================== Движок измерений (порт engine.ts) ==============================
#
# Протокол B (aperture_charge): N апертур на шаг, анализ хвоста, пропуски.
# Протокол A (kuhn_staircase): одиночное измерение на ступень, C = |Ī/r|.
# Верификация §6: восстановление известного заряда C·ΔV на эталоне.
#
# Состояние ModelState переносится НЕПРЕРЫВНО через все шаги, направления
# и циклы (physical_observer). Условия остановки §7.3 проверяются после
# каждого шага; при срабатывании остальные шаги не выполняются.

NUMERIC_CONFIG_FIELDS = [
    "vStart_V", "vEnd_V", "vStep_V", "iCompliance_A", "iRange_A", "nplc", "aperture_s",
    "lineFreq_Hz", "trigInterval_s", "trigCount", "tranDelay_s", "acqDelay_s", "tranTime_s",
    "preBias_V", "preBiasDuration_s", "settleDIdt_A_s", "tailPoints", "tailLimit_A",
    "slopeLimit_A_s", "kFactor", "convergencePct", "gapCurrentBound_A", "uCurrentCommon_A",
    "uLeakCommon_A", "uClockRel", "uDv_V", "uIOffset_A", "uIGainRel", "iOffset_A",
]


def validate_config(cfg: MeasurementConfig) -> None:
    """Валидация паспорта программы (§5). Бросает ValueError с русским сообщением."""
    if cfg is None:
        raise ValueError("Некорректный паспорт программы")
    for f in NUMERIC_CONFIG_FIELDS:
        v = getattr(cfg, f)
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(f"Нечисловое значение поля {f}")
    if cfg.cycles < 1:
        raise ValueError("cycles должен быть целым ≥ 1")
    if not cfg.directions:
        raise ValueError("directions: укажите хотя бы одно направление (forward/reverse)")
    if len(set(cfg.directions)) != len(cfg.directions):
        raise ValueError("directions: дубликаты направлений")
    for d in cfg.directions:
        if d not in ("forward", "reverse"):
            raise ValueError(f"directions: недопустимое направление «{d}»")
    if cfg.vStep_V <= 0:
        raise ValueError("Шаг напряжения vStep_V должен быть > 0")
    if cfg.vEnd_V == cfg.vStart_V:
        raise ValueError("vEnd_V не должен совпадать с vStart_V")
    if cfg.lineFreq_Hz <= 0:
        raise ValueError("Частота сети lineFreq_Hz должна быть > 0")
    if cfg.iCompliance_A <= 0:
        raise ValueError("Комплаенс iCompliance_A должен быть > 0")
    if cfg.iRangeMode == "fixed" and cfg.iRange_A <= 0:
        raise ValueError("Фиксированный диапазон iRange_A должен быть > 0")
    if cfg.trigInterval_s <= 0:
        raise ValueError("Период триггера trigInterval_s должен быть > 0")
    if cfg.tranDelay_s < 0:
        raise ValueError("Задержка источника tranDelay_s должна быть ≥ 0")
    if cfg.acqDelay_s < 0:
        raise ValueError("Задержка измерения acqDelay_s должна быть ≥ 0")
    if cfg.tranTime_s <= 0:
        raise ValueError("Длительность перехода источника tranTime_s должна быть > 0")
    if cfg.preBiasDuration_s < 0:
        raise ValueError("Длительность подготовки preBiasDuration_s должна быть ≥ 0")
    if cfg.tailPoints < 3:
        raise ValueError("tailPoints должен быть ≥ 3")
    if cfg.tailLimit_A <= 0:
        raise ValueError("Допуск хвоста tailLimit_A должен быть > 0")
    if cfg.slopeLimit_A_s <= 0:
        raise ValueError("Допуск наклона slopeLimit_A_s должен быть > 0")
    if cfg.kFactor <= 0:
        raise ValueError("Коэффициент k должен быть > 0")
    if (cfg.uCurrentCommon_A < 0 or cfg.uLeakCommon_A < 0 or cfg.uClockRel < 0 or
            cfg.uDv_V < 0 or cfg.uIOffset_A < 0 or cfg.uIGainRel < 0):
        raise ValueError("Неопределённости не могут быть отрицательными")
    if cfg.gapMode == "model" and cfg.fitModel == "none":
        raise ValueError("Коррекция пропусков по модели требует fitModel = exp1 или exp2")
    if cfg.gapMode == "bound" and not (cfg.gapCurrentBound_A > 0):
        raise ValueError("Режим пропусков bound требует gapCurrentBound_A > 0")

    aperture = aperture_from_config(cfg)
    if cfg.protocol == "aperture_charge":
        if cfg.trigCount < 8:
            raise ValueError("Протокол B (aperture_charge) требует trigCount ≥ 8 окон на шаг (анализ хвоста)")
        if aperture > cfg.trigInterval_s:
            raise ValueError(
                f"Длительность апертуры ({aperture:.6g} с) превышает период триггера "
                f"({cfg.trigInterval_s:.6g} с): уменьшите апертуру или увеличьте интервал")
        if cfg.uIOffset_A <= 0 and cfg.uIGainRel <= 0:
            raise ValueError("Для протокола B требуется положительная неопределённость тока (uIOffset_A или uIGainRel)")
    else:
        if cfg.acqDelay_s + aperture > cfg.trigInterval_s:
            raise ValueError("Протокол A: апертура должна укладываться в период ступени (acqDelay + апертура ≤ trigInterval)")
        if cfg.tranDelay_s + cfg.tranTime_s > cfg.trigInterval_s:
            raise ValueError("Протокол A: переход источника должен завершаться внутри периода ступени")


def build_step_knots(v_prev: float, v_next: float, tran_delay: float, tran_time: float,
                     end_t: float, win_a: List[float], win_b: List[float]) -> List[VoltKnot]:
    """Узлы профиля напряжения шага: 3 опорные точки + все границы окон."""
    def v_at(t: float) -> float:
        if t <= tran_delay:
            return v_prev
        if t >= tran_delay + tran_time:
            return v_next
        return v_prev + ((v_next - v_prev) * (t - tran_delay)) / tran_time

    times = sorted({0.0, tran_delay, tran_delay + tran_time, end_t,
                    *win_a, *win_b})
    return [VoltKnot(t, v_at(t)) for t in times]


def _range_total(rg: WalkRange) -> float:
    return rg.qFast + rg.qLeak + sum(rg.qBranch)


def _gap_ranges_for(win_a: List[float], win_b: List[float], end_t: float,
                    mk: Callable[[float, float], WalkRange]) -> List[WalkRange]:
    """Диапазоны пропусков: [0, a_1], между окнами, [b_N, endT]."""
    out: List[WalkRange] = []
    if win_a[0] > 0:
        out.append(mk(0.0, win_a[0]))
    for j in range(len(win_a) - 1):
        if win_a[j + 1] > win_b[j]:
            out.append(mk(win_b[j], win_a[j + 1]))
    if end_t > win_b[-1]:
        out.append(mk(win_b[-1], end_t))
    return out


def _new_run_id() -> str:
    return "run-" + _uuid.uuid4().hex[:12]


def execute_measurement(cfg: MeasurementConfig, dut: DutModel, seed: int = 31,
                        progress: Optional[Callable[[str], bool]] = None) -> List[RunResult]:
    """Выполнение последовательности (§7): циклы × направления × шаги (симуляция).

    progress(direction_label, ...) — необязательный колбэк после каждого шага;
    возврат False из колбэка немедленно останавливает последовательность
    (кнопка «Стоп» в GUI)."""
    validate_config(cfg)
    rng = mulberry32(seed)
    noise_ui, noise_offset = smu_noise_config(cfg)
    aperture = aperture_from_config(cfg)
    win_a, win_b = aperture_times(cfg)
    n_win = len(win_a)
    is_proto_b = cfg.protocol == "aperture_charge"
    eff_tail_points = min(cfg.tailPoints, n_win)
    n_steps = max(1, round(abs(cfg.vEnd_V - cfg.vStart_V) / cfg.vStep_V))

    fwd_points: List[float] = []
    for i in range(n_steps + 1):
        fwd_points.append(cfg.vEnd_V if i == n_steps else cfg.vStart_V + i * cfg.vStep_V)

    # Подготовка §7.1: релаксация на preBias; переход к первой точке развёртки.
    max_tau = max((br.tau_s for br in dut.branches), default=0.0)
    state0 = relax_state(settle_state(cfg.preBias_V, dut), cfg.preBias_V,
                         cfg.preBiasDuration_s, dut)
    first_dir = cfg.directions[0]
    first_point = fwd_points[0] if first_dir == "forward" else fwd_points[n_steps]
    if abs(first_point - cfg.preBias_V) > 1e-12:
        state0 = relax_state(state0, first_point, cfg.preBiasDuration_s, dut)
    prebias_confirmed = (cfg.preBiasDuration_s >= 5 * max_tau and
                         (cfg.settleDIdt_A_s <= 0 or
                          didt_at(state0, first_point, dut) <= cfg.settleDIdt_A_s))

    runs: List[RunResult] = []
    tail_b_history: List[float] = []
    executed_steps = 0
    stop_reason: Optional[str] = None
    state = state0
    interrupted = False

    def mk_range(l: float, r: float) -> WalkRange:
        return WalkRange(l, r, 0.0, [0.0] * len(dut.branches), 0.0)

    analysis_options = AnalyzeOptions(
        model=cfg.fitModel, gapMode=cfg.gapMode, gapCurrentBound_A=cfg.gapCurrentBound_A,
        tailPoints=eff_tail_points, tailLimit_A=cfg.tailLimit_A,
        slopeLimit_A_s=cfg.slopeLimit_A_s, kFactor=cfg.kFactor,
        uCurrentCommon_A=cfg.uCurrentCommon_A, uLeakCommon_A=cfg.uLeakCommon_A,
        uClockRel=cfg.uClockRel, uDv_V=cfg.uDv_V,
    )

    def make_run(direction: str, cycle: int) -> RunResult:
        label = ("Прямой ход" if direction == "forward" else "Обратный ход")
        if cfg.cycles > 1:
            label += f", цикл {cycle}"
        return RunResult(
            id=_new_run_id(), label=label, direction=direction, cycle=cycle,
            protocol=cfg.protocol, config=replace(cfg), dut=replace(dut,
                branches=[DutBranch(b.c_F, b.tau_s) for b in dut.branches]),
            timing=RunTiming(
                tranDelay_s=cfg.tranDelay_s, acqDelay_s=cfg.acqDelay_s,
                tranTime_s=cfg.tranTime_s, interval_s=cfg.trigInterval_s,
                aperture_s=aperture, firstA_s=win_a[0], firstB_s=win_b[0],
                lastB_s=win_b[-1],
                initialGap_s=win_a[0] - (cfg.tranDelay_s + cfg.tranTime_s),
                deadTime_s=cfg.trigInterval_s - aperture,
            ),
            createdAt=datetime.now().astimezone().isoformat(timespec="seconds"),
        )

    def run_step(direction: str, k: int, v_prev: float, v_next: float,
                 pre_settled: bool) -> Tuple[StepResult, ModelState, Optional[str]]:
        nonlocal state
        dv = v_next - v_prev
        v_mid = (v_prev + v_next) / 2
        end_t = (max(win_b[-1], cfg.tranDelay_s + cfg.tranTime_s) if is_proto_b
                 else cfg.trigInterval_s)

        knots = build_step_knots(v_prev, v_next, cfg.tranDelay_s, cfg.tranTime_s,
                                 end_t, win_a, win_b)
        ap_ranges = [mk_range(aj, win_b[j]) for j, aj in enumerate(win_a)]
        gap_ranges = _gap_ranges_for(win_a, win_b, end_t, mk_range)
        initial_gap_range = gap_ranges[0] if win_a[0] > 0 else None
        walk = walk_profile(knots, state, ap_ranges + gap_ranges, dut)
        state_after = walk.stateEnd

        # ---- Измерение апертур (шум + offset прибора, диапазон, комплаенс) ----
        apertures: List[ApertureRecord] = []
        i_meas_arr: List[float] = []
        u_i_arr: List[float] = []
        step_flags: set = set()
        for j in range(n_win):
            dt = win_b[j] - win_a[j]
            i_avg_true = _range_total(ap_ranges[j]) / dt
            i_meas = i_avg_true + noise_offset + gauss(rng) * noise_ui(abs(i_avg_true))
            u_i_rec = noise_ui(abs(i_meas))
            _range, overload = pick_range(abs(i_meas), cfg)
            status = "OK"
            if overload:
                status = "OVLD"
                step_flags.add("RANGE_OVERLOAD")
            if abs(i_meas) > cfg.iCompliance_A:
                status = "COMP"
                step_flags.add("COMPLIANCE_EXCEEDED")
            i_meas_arr.append(i_meas)
            u_i_arr.append(u_i_rec)
            apertures.append(ApertureRecord(j + 1, win_a[j], win_b[j], i_meas, 0.0, u_i_rec, status))

        # ---- Истина модели (§13: «истина» перехода) ----
        q_fast_total = fast_charge(v_next, dut) - fast_charge(v_prev, dut)
        q_branch_total = [br.c_F * ((state_after.x[m] if m < len(state_after.x) else 0) -
                                    (state.x[m] if m < len(state.x) else 0))
                          for m, br in enumerate(dut.branches)]
        q_eq_branches = sum(br.c_F * (v_next - v_prev) for br in dut.branches)
        q_total = q_fast_total + q_eq_branches
        q_registered = sum(_range_total(rg) for rg in ap_ranges)
        q_in_gaps = sum(rg.qFast + sum(rg.qBranch) for rg in gap_ranges)
        truth = TruthInfo(
            qFast_C=q_fast_total, qBranch_C=q_branch_total, qLeak_C=walk.qLeak,
            qRegistered_C=q_registered, qInGaps_C=q_in_gaps, qTotal_C=q_total,
            cSecTrue_F=q_total / dv,
        )

        # ---- Анализ ----
        u_dt = [(cfg.uClockRel * (win_b[j] - win_a[j]) if cfg.uClockRel > 0 else 0.0)
                for j in range(n_win)]
        u_leak_ind = [0.0] * n_win

        if is_proto_b:
            base_input = StepAnalysisInput(
                a=win_a[:], b=win_b[:], i=i_meas_arr[:], uI=u_i_arr[:],
                leak=[0.0] * n_win, uLeakInd=u_leak_ind[:], uDt=u_dt,
                preSettled=pre_settled, dv=dv, vBefore=v_prev, vAfter=v_next,
                options=analysis_options,
            )
            leak = [0.0] * n_win
            if cfg.backgroundMode in ("mean_tail", "linear_tail"):
                pass1 = analyse_step(replace(base_input,
                    options=replace(analysis_options, model="none")))
                p1bad = any(f.startswith("DATA_INVALID") for f in pass1.flags)
                if not p1bad and math.isfinite(pass1.tailB):
                    if cfg.backgroundMode == "mean_tail":
                        leak = [pass1.tailB] * n_win
                    else:
                        t_mean = sum((win_a[j] + win_b[j]) / 2
                                     for j in range(n_win - eff_tail_points, n_win)) / eff_tail_points
                        leak = [pass1.tailB + pass1.tailS * ((win_a[j] + win_b[j]) / 2 - t_mean)
                                for j in range(n_win)]
            elif cfg.backgroundMode == "blank":
                step_flags.add("BLANK_UNAVAILABLE")
            for j in range(n_win):
                apertures[j].i_background_A = leak[j]
            analysis = analyse_step(replace(base_input, leak=leak[:]))
        else:
            # Протокол A: одиночное окно, Kuhn C = |Ī/r|; анализ хвоста неприменим.
            if cfg.backgroundMode != "none":
                step_flags.add("BACKGROUND_UNAVAILABLE")
            r = dv / cfg.trigInterval_s
            dt = win_b[0] - win_a[0]
            q_obs = i_meas_arr[0] * dt
            u_q = u_i_arr[0] * dt
            analysis = StepAnalysisOutput(
                qObs=q_obs, uQ=u_q, qGapModel=0.0, qGapBound=0.0, qCorrected=q_obs,
                cSec=abs(i_meas_arr[0] / r), uC=u_i_arr[0] / abs(r),
                tailB=0.0, tailS=0.0, tailUB=0.0, tailUS=0.0, tailOk=True,
                gaps=[], gapDuration=0.0, fit=None, flags=[],
                usableForPair=pre_settled,
            )

        # ---- Флаги шага ----
        flags = list(dict.fromkeys(list(analysis.flags) + sorted(step_flags)))
        if not pre_settled and "PRECONDITION_NOT_CONFIRMED" not in flags:
            flags.append("FIRST_STEP_DIAGNOSTIC")
        initial_gap = win_a[0] - (cfg.tranDelay_s + cfg.tranTime_s)
        if initial_gap > 0:
            flags.append("MISSED_START")
        elif (win_a[0] > 0 and initial_gap_range is not None and
              abs(initial_gap_range.qFast) > 0.5 * abs(q_fast_total)):
            flags.append("MISSED_START")
        if win_a[0] > 0:
            flags.append("INITIAL_GAP_PRESENT")
        flags = list(dict.fromkeys(flags))

        step = StepResult(
            step=k, direction=direction, vBefore_V=v_prev, vAfter_V=v_next,
            vMid_V=v_mid, dv_V=dv, preSettled=pre_settled, apertures=apertures,
            gaps_s=analysis.gaps, gapDuration_s=analysis.gapDuration,
            qObs_C=analysis.qObs, uQ_C=analysis.uQ,
            qGapModel_C=analysis.qGapModel, qGapBound_C=analysis.qGapBound,
            qCorrected_C=analysis.qCorrected, cSec_F=analysis.cSec, uC_F=analysis.uC,
            tailB_A=analysis.tailB, tailS_A_s=analysis.tailS,
            tailUB_A=analysis.tailUB, tailUS_A_s=analysis.tailUS, tailOk=analysis.tailOk,
            fit=analysis.fit, wPort_J=walk.wPort, dU_J=walk.dU,
            dissipated_J=walk.dissipated, energyResidual_J=walk.residual,
            flags=flags, usableForPair=analysis.usableForPair, truth=truth,
        )
        if not is_proto_b:
            r = dv / cfg.trigInterval_s
            step.kuhn = KuhnInfo(
                iMeas_A=i_meas_arr[0], rampRate_Vps=r,
                cKuhn_F=abs(i_meas_arr[0] / r),
                cKuhnLeakCorrected_F=abs((i_meas_arr[0] - apertures[0].i_background_A) / r),
                apertureStart_s=win_a[0],
            )

        # ---- Условия остановки §7.3 ----
        tail_b_history.append(abs(analysis.tailB))
        stop: Optional[str] = None
        if "RANGE_OVERLOAD" in step_flags:
            stop = "OVERLOAD"
        elif "COMPLIANCE_EXCEEDED" in step_flags:
            stop = "COMPLIANCE"
        else:
            if abs(analysis.tailB) > 10 * cfg.tailLimit_A and len(tail_b_history) >= 3:
                L = len(tail_b_history)
                if tail_b_history[L - 3] < tail_b_history[L - 2] < tail_b_history[L - 1]:
                    stop = "BREAKDOWN_OR_LEAKAGE_GROWTH"
            if not stop and is_proto_b:
                # Неустойчивость: знакочередующиеся остатки хвоста ≥ 5 окон подряд.
                prev_sign = 0
                alt = 0
                max_alt = 0
                t_mean = sum((win_a[j] + win_b[j]) / 2
                             for j in range(n_win - eff_tail_points, n_win)) / eff_tail_points
                for j in range(n_win - eff_tail_points, n_win):
                    yj = i_meas_arr[j] - apertures[j].i_background_A
                    uj = u_i_arr[j]
                    res = yj - (analysis.tailB + analysis.tailS * ((win_a[j] + win_b[j]) / 2 - t_mean))
                    if abs(res) > 2 * uj:
                        sg = 1 if res > 0 else -1
                        if sg == prev_sign:
                            alt = 1
                        else:
                            alt = 1 if prev_sign == 0 else alt + 1
                        prev_sign = sg
                        max_alt = max(max_alt, alt)
                    else:
                        prev_sign = 0
                        alt = 0
                if max_alt >= 5:
                    stop = "INSTABILITY"
            if not stop:
                q_missed = (initial_gap_range.qFast + sum(initial_gap_range.qBranch)
                            if initial_gap_range is not None else 0.0)
                if initial_gap > 0 and abs(q_missed) > (cfg.convergencePct / 100) * abs(q_total):
                    stop = "TIMING_LOSS"
            if not stop and cfg.stopOnTailFail and not analysis.tailOk:
                stop = "TAIL_NOT_SETTLED"

        return step, state_after, stop

    # ---- Циклы × направления × шаги ----
    for cycle in range(1, cfg.cycles + 1):
        if stop_reason or interrupted:
            break
        for direction in cfg.directions:
            if stop_reason or interrupted:
                break
            points = fwd_points if direction == "forward" else list(reversed(fwd_points))
            run: Optional[RunResult] = None
            for k in range(n_steps):
                if stop_reason or interrupted:
                    break
                v_prev = points[k]
                v_next = points[k + 1]
                is_first_overall = executed_steps == 0
                pre_settled = prebias_confirmed if is_first_overall else k > 0
                step, state_after, stop = run_step(direction, k, v_prev, v_next, pre_settled)
                state = state_after
                if run is None:
                    run = make_run(direction, cycle)
                    runs.append(run)
                run.steps.append(step)
                run.cvCurve.append(CvPoint(
                    vMid_V=step.vMid_V, cSec_F=step.cSec_F, uC_F=step.uC_F,
                    cSecTrue_F=step.truth.cSecTrue_F if step.truth else None,
                    direction=direction, usable=step.usableForPair,
                ))
                run.wPortTotal_J += step.wPort_J
                run.dUTotal_J += step.dU_J
                run.dissipatedTotal_J += step.dissipated_J
                executed_steps += 1
                if progress is not None:
                    if not progress(f"{run.label}: шаг {k + 1}/{n_steps}"):
                        interrupted = True
                        stop = "USER_STOP"
                if stop:
                    stop_reason = stop
                    run.stopped = True
                    run.stopReason = stop

    if interrupted and not stop_reason:
        stop_reason = "USER_STOP"
        if runs:
            runs[-1].stopped = True
            runs[-1].stopReason = "USER_STOP"

    # ---- Площадь петли ∮V dQ по циклу (прямой + обратный ход, §10) ----
    by_cycle: Dict[int, List[RunResult]] = {}
    for r_ in runs:
        by_cycle.setdefault(r_.cycle, []).append(r_)
    for rs in by_cycle.values():
        fwd = next((r_ for r_ in rs if r_.direction == "forward"), None)
        rev = next((r_ for r_ in rs if r_.direction == "reverse"), None)
        if fwd is None or rev is None:
            continue
        path = fwd.steps + rev.steps
        if len(path) < 2:
            continue
        qs: List[float] = []
        q_acc = 0.0
        for st in path:
            qs.append(q_acc)
            q_acc += st.qObs_C
        area = 0.0
        for k in range(len(path)):
            q_next = qs[k + 1] if k + 1 < len(path) else 0.0  # замыкание петли: Q → Q_0
            area += path[k].vMid_V * (q_next - qs[k])
        fwd.loopArea_J = abs(area)
        rev.loopArea_J = abs(area)

    return runs


# ============================== Верификация (§6) ==============================

def execute_verification(req: VerificationRequest) -> VerificationResult:
    """Проверка восстановления известного заряда C·ΔV на эталоне
    (конденсатор или RC), одиночный переход 0 → dv_V."""
    t = req.timing
    numeric = [req.cRef_F, req.rRef_ohm, req.dv_V, req.tolerancePct, req.noise_A,
               t.interval_s, t.aperture_s, t.tranDelay_s, t.acqDelay_s, t.tranTime_s, t.count]
    for v in numeric:
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError("Верификация: все параметры должны быть конечными числами")
    if req.refType not in ("c", "rc"):
        raise ValueError("Верификация: refType должен быть «c» или «rc»")
    if req.cRef_F <= 0:
        raise ValueError("Верификация: cRef_F должен быть > 0")
    if req.dv_V == 0:
        raise ValueError("Верификация: dv_V не должен быть нулевым")
    if req.tolerancePct <= 0:
        raise ValueError("Верификация: допуск tolerancePct должен быть > 0")
    if req.noise_A < 0:
        raise ValueError("Верификация: шум не может быть отрицательным")
    if req.refType == "rc" and not (req.rRef_ohm > 0):
        raise ValueError("Верификация: для refType = rc требуется rRef_ohm > 0")
    if t.count < 1:
        raise ValueError("Верификация: количество апертур должно быть ≥ 1")
    if t.aperture_s <= 0:
        raise ValueError("Верификация: апертура должна быть > 0")
    if t.interval_s <= 0:
        raise ValueError("Верификация: интервал триггера должен быть > 0")
    if t.aperture_s > t.interval_s:
        raise ValueError("Верификация: апертура превышает интервал триггера (окна перекрываются)")
    if t.tranDelay_s < 0 or t.acqDelay_s < 0:
        raise ValueError("Верификация: задержки не могут быть отрицательными")
    if t.tranTime_s <= 0:
        raise ValueError("Верификация: длительность фронта должна быть > 0")

    if req.refType == "c":
        dut = DutModel(kind="ref_c", label=f"Эталонный конденсатор {req.cRef_F} Ф",
                       c0_F=req.cRef_F)
    else:
        dut = DutModel(kind="ref_rc",
                       label=f"Эталон RC: {req.cRef_F} Ф, R = {req.rRef_ohm} Ом",
                       branches=[DutBranch(req.cRef_F, req.rRef_ohm * req.cRef_F)])

    n = max(1, round(t.count))
    win_a: List[float] = []
    win_b: List[float] = []
    for j in range(1, n + 1):
        aj = (j - 1) * t.interval_s + t.acqDelay_s
        win_a.append(aj)
        win_b.append(aj + t.aperture_s)
    v_prev = 0.0
    v_next = req.dv_V
    end_t = max(win_b[-1], t.tranDelay_s + t.tranTime_s)

    knots = build_step_knots(v_prev, v_next, t.tranDelay_s, t.tranTime_s, end_t, win_a, win_b)

    def mk_range(l: float, r: float) -> WalkRange:
        return WalkRange(l, r, 0.0, [0.0] * len(dut.branches), 0.0)

    ap_ranges = [mk_range(aj, win_b[j]) for j, aj in enumerate(win_a)]
    gap_ranges = _gap_ranges_for(win_a, win_b, end_t, mk_range)
    state0 = settle_state(v_prev, dut)
    walk_profile(knots, state0, ap_ranges + gap_ranges, dut)

    # Шум: постоянная неопределённость noise_A, смещения нет, seed 31.
    rng = mulberry32(31)
    apertures: List[ApertureRecord] = []
    i_meas_arr: List[float] = []
    for j in range(n):
        dt = win_b[j] - win_a[j]
        i_avg_true = _range_total(ap_ranges[j]) / dt
        i_meas = i_avg_true + gauss(rng) * req.noise_A
        i_meas_arr.append(i_meas)
        apertures.append(ApertureRecord(j + 1, win_a[j], win_b[j], i_meas, 0.0,
                                        req.noise_A, "OK"))

    q_recovered = sum(i_meas_arr[j] * (win_b[j] - win_a[j]) for j in range(n))
    q_expected = req.cRef_F * req.dv_V
    error_rel_pct = (q_recovered - q_expected) / q_expected * 100
    pass_ = abs(error_rel_pct) <= req.tolerancePct

    q_registered_true = sum(_range_total(rg) for rg in ap_ranges)
    q_in_gaps = sum(rg.qFast + sum(rg.qBranch) for rg in gap_ranges)
    bad_status = any(ap.status != "OK" for ap in apertures)

    checks = [
        VerificationCheck("Начало изменения напряжения",
                          f"t = {format_seconds(t.tranDelay_s)}, фронт {format_seconds(t.tranTime_s)}",
                          None, ""),
        VerificationCheck("Задержка до первой апертуры", format_seconds(win_a[0]),
                          win_a[0] <= t.tranDelay_s + t.tranTime_s, ""),
        VerificationCheck("Длительность апертуры / вес", format_seconds(t.aperture_s),
                          t.aperture_s > 0, ""),
        VerificationCheck("Мёртвое время между апертурами",
                          format_seconds(t.interval_s - t.aperture_s), None,
                          "период таймера ≠ апертура (§6)"),
        VerificationCheck("Перегрузка / compliance",
                          "обнаружены" if bad_status else "не обнаружены", not bad_status, ""),
        VerificationCheck("Восстановление известного заряда C·ΔV",
                          f"{error_rel_pct:.3e} %", pass_, ""),
        VerificationCheck("Заряд в пропусках (истина)", format_coulombs(q_in_gaps), None,
                          "если ≠ 0 — часть заряда не зарегистрирована"),
    ]

    return VerificationResult(
        apertures=apertures, qExpected_C=q_expected, qRecovered_C=q_recovered,
        qRegisteredTrue_C=q_registered_true, qInGaps_C=q_in_gaps,
        errorRelPct=error_rel_pct, tolerancePct=req.tolerancePct, pass_=pass_,
        checks=checks,
        timing=RunTiming(
            tranDelay_s=t.tranDelay_s, acqDelay_s=t.acqDelay_s, tranTime_s=t.tranTime_s,
            interval_s=t.interval_s, aperture_s=t.aperture_s,
            firstA_s=win_a[0], firstB_s=win_b[0], lastB_s=win_b[-1],
            initialGap_s=win_a[0] - (t.tranDelay_s + t.tranTime_s),
            deadTime_s=t.interval_s - t.aperture_s,
        ),
    )

# ============================== Генератор SCPI (порт scpi.ts) ==============================
#
# Команды сверены с документами foundation:
#  * quasi_static_cv_B2910BL.md (минимальная последовательность sweep);
#  * B2900B-BL Programming Guide, Edition 1:
#      :SENS:FUNC / :SENS:REM / :SENS:CURR:PROT / :SENS:CURR:RANG:AUTO /
#      :SENS:CURR:NPLC / :SOUR:FUNC:MODE / :SOUR:VOLT:MODE SWE|FIX /
#      :SOUR:VOLT:STAR|STOP|STEP / :SOUR:SWE:DIR|SPAC|RANG /
#      :SOUR:VOLT (уровень) / :SOUR:VOLT:TRIG (уровень по триггеру) /
#      :TRIG:SOUR TIM / :TRIG:TIM / :TRIG:COUN / :TRIG:TRAN:DEL / :TRIG:ACQ:DEL /
#      :SYST:TIME:TIM:COUN:RES:AUTO / :OUTP / :INIT:IMM / *WAI /
#      :FETC:ARR:CURR? / *CAL? / *TST?
#  Autozero и измерительные фильтры — сверять по SCPI Command Reference (§14 №7).

@dataclass
class ScpiCommandDoc:
    cmd: str
    comment: str


def _nplc_of(cfg: MeasurementConfig) -> Tuple[float, str]:
    """:SENS:CURR:NPLC — число периодов сети; апертура пересчитывается через
    фактическую частоту сети (§5)."""
    aperture = aperture_from_config(cfg)
    nplc = cfg.nplc if cfg.apertureMode == "nplc" else aperture * cfg.lineFreq_Hz
    return (round(nplc * 1e6) / 1e6,
            f"апертура ≈ {aperture * 1e3:.4g} мс при {cfg.lineFreq_Hz:g} Гц")


def _protocol_a(cfg: MeasurementConfig, direction: str) -> Tuple[List[str], List[ScpiCommandDoc]]:
    """Протокол A — линейная развёртка (метод Куна на staircase sweep)."""
    nplc, nplc_note = _nplc_of(cfg)
    n_points = max(2, round(abs(cfg.vEnd_V - cfg.vStart_V) / cfg.vStep_V) + 1)
    rev = direction == "reverse"
    lines: List[str] = []

    lines += ["*RST", "*CLS", ":OUTP OFF", ""]
    lines += ["' --- Измерение тока (quasi_static_cv_B2910BL.md)",
              ':SENS:FUNC "CURR"',
              ":SENS:REM ON  ' 4-проводная схема; для 2-проводной — OFF (§4)",
              f":SENS:CURR:PROT {fmt_scpi(cfg.iCompliance_A)}  ' compliance: по DUT, не по максимуму SMU (§4)"]
    if cfg.iRangeMode == "auto":
        lines.append(":SENS:CURR:RANG:AUTO ON")
    else:
        lines.append(f":SENS:CURR:RANG {fmt_scpi(cfg.iRange_A)}  ' фиксированный диапазон")
    lines.append(f":SENS:CURR:NPLC {fmt_scpi(nplc)}  ' {nplc_note}")
    lines += ["", "' --- Источник напряжения: линейная развёртка",
              ":SOUR:FUNC:MODE VOLT",
              ":SOUR:VOLT:MODE SWE"]
    if rev:
        lines += [f":SOUR:VOLT:STAR {fmt_scpi(cfg.vEnd_V)}",
                  f":SOUR:VOLT:STOP {fmt_scpi(cfg.vStart_V)}",
                  ":SOUR:SWE:DIR DOWN  ' обратный ход"]
    else:
        lines += [f":SOUR:VOLT:STAR {fmt_scpi(cfg.vStart_V)}",
                  f":SOUR:VOLT:STOP {fmt_scpi(cfg.vEnd_V)}",
                  ":SOUR:SWE:DIR UP"]
    lines += [f":SOUR:VOLT:STEP {fmt_scpi(cfg.vStep_V)}",
              ":SOUR:SWE:SPAC LIN  ' линейный шаг",
              ":SOUR:SWE:RANG FIX  ' диапазон источника не менять на развёртке",
              "",
              "' --- Триггерная модель: таймер, период точки = период ступени",
              ":TRIG:SOUR TIM",
              f":TRIG:TIM {fmt_scpi(cfg.trigInterval_s)}  ' T; скорость развёртки r = ΔV/T = {cfg.vStep_V / cfg.trigInterval_s:.4g} В/с",
              f":TRIG:COUN {n_points}  ' точек развёртки",
              f":TRIG:TRAN:DEL {fmt_scpi(cfg.tranDelay_s)}",
              "",
              ":OUTP ON",
              ":INIT:IMM",
              "*WAI",
              ":FETC:ARR:CURR?  ' массив токов: C_i = |I_i/(ΔV/T)| (Кун на staircase)",
              ":OUTP OFF"]

    commands = [
        ScpiCommandDoc("*RST / *CLS", "сброс и очистка статусов"),
        ScpiCommandDoc(':SENS:FUNC "CURR"', "измеряемая величина — ток"),
        ScpiCommandDoc(":SENS:REM ON", "4-проводная схема (Kelvin); OFF — 2-проводная"),
        ScpiCommandDoc(":SENS:CURR:PROT <А>", "compliance — ограничение тока защиты DUT"),
        ScpiCommandDoc(":SENS:CURR:RANG:AUTO ON | :SENS:CURR:RANG <А>", "диапазон измерения тока"),
        ScpiCommandDoc(":SENS:CURR:NPLC <n>", "интегрирование: n периодов сети (апертура = n/lineFreq)"),
        ScpiCommandDoc(":SOUR:FUNC:MODE VOLT", "источник напряжения"),
        ScpiCommandDoc(":SOUR:VOLT:MODE SWE", "режим развёртки (staircase sweep)"),
        ScpiCommandDoc(":SOUR:VOLT:STAR <В> / :STOP <В> / :STEP <В>", "границы и шаг развёртки"),
        ScpiCommandDoc(":SOUR:SWE:DIR UP|DOWN", "направление (обратный ход)"),
        ScpiCommandDoc(":SOUR:SWE:SPAC LIN", "линейное распределение точек"),
        ScpiCommandDoc(":SOUR:SWE:RANG FIX", "диапазон источника фиксирован на развёртке"),
        ScpiCommandDoc(":TRIG:SOUR TIM", "источник триггера — таймер"),
        ScpiCommandDoc(":TRIG:TIM <с>", "интервал триггера (период ступени T)"),
        ScpiCommandDoc(":TRIG:COUN <n>", "число точек развёртки"),
        ScpiCommandDoc(":TRIG:TRAN:DEL <с>", "задержка изменения источника от триггера"),
        ScpiCommandDoc(":OUTP ON / :INIT:IMM / *WAI", "запуск и ожидание завершения"),
        ScpiCommandDoc(":FETC:ARR:CURR?", "чтение массива результатов"),
    ]
    return lines, commands


def _protocol_b(cfg: MeasurementConfig, direction: str) -> Tuple[List[str], List[ScpiCommandDoc]]:
    """Протокол B — зарядовый (апертурный) регламент §7.2:
    :SOUR:VOLT:TRIG + :FETC:ARR."""
    nplc, nplc_note = _nplc_of(cfg)
    rev = direction == "reverse"
    lines: List[str] = []

    n_steps = max(1, round(abs(cfg.vEnd_V - cfg.vStart_V) / cfg.vStep_V))
    points: List[float] = []
    for i in range(n_steps + 1):
        points.append(cfg.vEnd_V if i == n_steps else cfg.vStart_V + i * cfg.vStep_V)
    seq = list(reversed(points)) if rev else points

    lines += ["*RST", "*CLS", ":OUTP OFF", ""]
    lines += ["' --- Измерение тока",
              ':SENS:FUNC "CURR"',
              ":SENS:REM ON",
              f":SENS:CURR:PROT {fmt_scpi(cfg.iCompliance_A)}  ' compliance по DUT (§4)"]
    if cfg.iRangeMode == "auto":
        lines.append(":SENS:CURR:RANG:AUTO ON")
    else:
        lines.append(f":SENS:CURR:RANG {fmt_scpi(cfg.iRange_A)}")
    lines += [f":SENS:CURR:NPLC {fmt_scpi(nplc)}  ' {nplc_note}",
              ":SYST:TIME:TIM:COUN:RES:AUTO ON  ' разрешение таймера (Programming Guide)",
              "",
              "' --- Источник: фиксированный уровень, изменение по триггеру",
              ":SOUR:FUNC:MODE VOLT",
              ":SOUR:VOLT:MODE FIX",
              "",
              f"' === Подготовка §7.1: начальный bias {fmt_scpi(cfg.preBias_V)} В, "
              f"выдержка {fmt_scpi(cfg.preBiasDuration_s)} с ===",
              ":OUTP ON",
              f":SOUR:VOLT {fmt_scpi(cfg.preBias_V)}",
              f"' ... выдержка до критерия установления (|dI/dt| ≤ {fmt_scpi(cfg.settleDIdt_A_s)} А/с) ...",
              ""]

    def emit_step(k: int) -> None:
        v_prev = seq[k]
        v_next = seq[k + 1]
        lines.append(f"' === Шаг {k + 1}/{n_steps}: {fmt_scpi(v_prev)} В → {fmt_scpi(v_next)} В ===")
        lines.append(f":SOUR:VOLT {fmt_scpi(v_prev)}  ' зафиксировать V_prev (наследуется от предыдущего шага)")
        lines.append(f":SOUR:VOLT:TRIG {fmt_scpi(v_next)}  ' уровень V_next — выводится по триггеру")
        lines.append(":TRIG:SOUR TIM")
        lines.append(f":TRIG:TIM {fmt_scpi(cfg.trigInterval_s)}  ' период апертур (период таймера ≠ апертура, §6)")
        lines.append(f":TRIG:COUN {cfg.trigCount}  ' апертур на шаг")
        lines.append(f":TRIG:TRAN:DEL {fmt_scpi(cfg.tranDelay_s)}  ' задержка источника")
        lines.append(f":TRIG:ACQ:DEL {fmt_scpi(cfg.acqDelay_s)}  ' задержка первой апертуры")
        lines.append(":INIT:IMM")
        lines.append("*WAI")
        lines.append(":FETC:ARR:CURR?  ' средние токи апертур → строки журнала §13")
        lines.append("")

    max_emit = 8
    for k in range(n_steps):
        if k >= max_emit:
            lines.append(f"' === Шаги {max_emit + 1}…{n_steps}: повторить блок для каждого перехода ===")
            lines.append("' === (V_prev наследуется от предыдущего шага; триггерная модель неизменна) ===")
            break
        emit_step(k)

    lines += ["' === Завершение: безопасный возврат bias и разряд DUT (§7.3) ===",
              f":SOUR:VOLT {fmt_scpi(cfg.preBias_V)}",
              "' ... безопасный разряд по выбранному протоколу ...",
              ":OUTP OFF"]

    commands = [
        ScpiCommandDoc(":SYST:TIME:TIM:COUN:RES:AUTO ON", "автоматическое разрешение таймера триггера"),
        ScpiCommandDoc(":SOUR:VOLT:MODE FIX", "фиксированный уровень источника"),
        ScpiCommandDoc(":SOUR:VOLT <В>", "немедленная установка уровня (V_prev, подготовка)"),
        ScpiCommandDoc(":SOUR:VOLT:TRIG <В>", "уровень по триггеру — синхронизация перехода с измерением"),
        ScpiCommandDoc(":TRIG:ACQ:DEL <с>", "задержка измерения от триггера (граница первого окна a_1)"),
        ScpiCommandDoc(":INIT:IMM", "запуск триггерной последовательности шага"),
        ScpiCommandDoc(":FETC:ARR:CURR?", "чтение N средних токов апертур шага"),
    ]
    return lines, commands


SCPI_NOTES = [
    "Сверьте синтаксис и допустимые значения по B2900B/BL SCPI Command Reference для установленной версии прошивки (методика §14, №7).",
    "Autozero и измерительные фильтры задаются по SCPI Command Reference — их синтаксис в Programming Guide не приведён (в паспорте программы §5 они фиксируются отдельными полями).",
    "*WAI блокирует выполнение до завершения; время ответа VISA не является временем физического перехода (§6).",
    "Период таймера (:TRIG:TIM) не равен апертуре; фактические границы окон [a_j, b_j] фиксируются в журнале §13.",
    "Команды сверены с B2900B/BL Series Programming Guide, Edition 1 и quasi_static_cv_B2910BL.md (документы в docs/foundation/).",
    "До DUT выполнить опыт на известных C и RC (§6) — вкладка «Верификация».",
    "Малые токи (фемтофарады): User's Guide «Performing Low Current Measurements» — по умолчанию диапазоны 10 нА и 100 нА НЕ используются; задайте FIXED 10 нА (:SENS:CURR:RANG 1E-8) или AUTO с явно заданным минимумом.",
]


def generate_scpi(cfg: MeasurementConfig, protocol: Optional[str] = None,
                  direction: str = "forward",
                  include_reverse: bool = False) -> Tuple[str, List[ScpiCommandDoc], List[str]]:
    """Полный SCPI-скрипт с комментариями + справочник команд + предупреждения."""
    proto = protocol or cfg.protocol
    gen = _protocol_a if proto == "kuhn_staircase" else _protocol_b
    lines, commands = gen(cfg, direction)

    if include_reverse:
        rev_dir = "reverse" if direction == "forward" else "forward"
        rev_lines, _rev_cmds = gen(cfg, rev_dir)
        lines += ["",
                  "' ============================================================",
                  "' === ОБРАТНЫЙ ХОД (§7.3: с документированной историей на развороте) ===",
                  "' ============================================================"]
        lines += rev_lines

    return "\n".join(lines) + "\n", commands, SCPI_NOTES


# ============================== Журнал данных §13 (порт csv.ts) ==============================

HEADER_METHODOLOGY = ("run,direction,step,v_before_V,v_after_V,a_s,b_s,"
                      "i_avg_A,i_background_A,u_i_A,pre_settled,status")
HEADER_QSCV_FINAL = ("run,direction,step,v_before_V,v_after_V,a_s,b_s,"
                     "i_A,u_i_A,leak_A,u_leak_ind_A,u_dt_s,pre_settled")


def _num(x: float) -> str:
    """Минимальная потеря точности: repr — кратчайшая точная запись числа."""
    if x is None or not math.isfinite(x):
        return ""
    return repr(x)


def _csv_escape(s: str) -> str:
    if any(ch in s for ch in ',\n;'):
        return '"' + s.replace('"', '""') + '"'
    return s


def journal_csv(run: RunResult, fmt: str = "methodology") -> str:
    """Журнал: одна строка на апертуру (методика §13).

    methodology: run,direction,step,v_before_V,v_after_V,a_s,b_s,i_avg_A,
                 i_background_A,u_i_A,pre_settled,status
    qscv_final:  acquisition.csv офлайн-анализатора qscv_final.py."""
    rows: List[str] = [HEADER_METHODOLOGY if fmt == "methodology" else HEADER_QSCV_FINAL]
    for step in run.steps:
        for ap in step.apertures:
            if fmt == "methodology":
                status = ";".join(step.flags) if step.flags else "OK"
                rows.append(",".join([
                    _csv_escape(run.label), step.direction, str(step.step),
                    _num(step.vBefore_V), _num(step.vAfter_V),
                    _num(ap.a_s), _num(ap.b_s),
                    _num(ap.i_avg_A), _num(ap.i_background_A), _num(ap.u_i_A),
                    "true" if step.preSettled else "false",
                    _csv_escape(status),
                ]))
            else:
                rows.append(",".join([
                    _csv_escape(run.label), step.direction, str(step.step),
                    _num(step.vBefore_V), _num(step.vAfter_V),
                    _num(ap.a_s), _num(ap.b_s),
                    _num(ap.i_avg_A), _num(ap.u_i_A),
                    _num(ap.i_background_A), _num(0), _num(0),
                    "true" if step.preSettled else "false",
                ]))
    return "\n".join(rows) + "\n"


def journal_metadata(run: RunResult) -> str:
    """Метаданные прогона (§13: «отдельно сохранить настройки»)."""
    payload = {
        "run": {
            "id": run.id, "label": run.label, "direction": run.direction,
            "cycle": run.cycle, "protocol": run.protocol, "createdAt": run.createdAt,
            "stopped": run.stopped, "stopReason": run.stopReason,
        },
        "config": _config_to_dict(run.config),
        "dut": _dut_to_dict(run.dut),
        "timing": run.timing.__dict__ if run.timing else None,
        "energy": {
            "wPortTotal_J": run.wPortTotal_J, "dUTotal_J": run.dUTotal_J,
            "dissipatedTotal_J": run.dissipatedTotal_J, "loopArea_J": run.loopArea_J,
        },
        "note": ("Журнал: одна строка на апертуру (методика §13). Настройки сохранены "
                 "отдельно. В отчёте разделить непосредственно измеренное, рассчитанное "
                 "из данных и восстановленное по модели."),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _config_to_dict(cfg: MeasurementConfig) -> Dict:
    d = {}
    for k in cfg.__dataclass_fields__:
        v = getattr(cfg, k)
        d[k] = list(v) if isinstance(v, list) else v
    return d


def _dut_to_dict(dut: DutModel) -> Dict:
    return {
        "kind": dut.kind, "label": dut.label, "c0_F": dut.c0_F, "g_S": dut.g_S,
        "branches": [{"c_F": b.c_F, "tau_s": b.tau_s} for b in dut.branches],
        "cOx_F": dut.cOx_F, "cMin_F": dut.cMin_F, "vfb_V": dut.vfb_V, "width_V": dut.width_V,
    }


def _split_csv_line(line: str) -> List[str]:
    out: List[str] = []
    cur = ""
    in_quotes = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_quotes:
            if ch == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    cur += '"'
                    i += 1
                else:
                    in_quotes = False
            else:
                cur += ch
        elif ch == '"':
            in_quotes = True
        elif ch == ",":
            out.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    out.append(cur)
    return out


QSCV_REQUIRED = ["run", "direction", "step", "v_before_V", "v_after_V", "a_s", "b_s",
                 "i_A", "u_i_A", "leak_A", "u_leak_ind_A", "u_dt_s", "pre_settled"]


def parse_acquisition_csv(text: str) -> Tuple[List[AcquisitionRow], List[str]]:
    """Разбор acquisition.csv (формат qscv_final.py). Ошибки собираются в список."""
    rows: List[AcquisitionRow] = []
    errors: List[str] = []
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return rows, ["Пустой CSV"]
    header = [h.strip() for h in _split_csv_line(lines[0])]
    missing = [c for c in QSCV_REQUIRED if c not in header]
    if missing:
        return rows, [f"Отсутствуют обязательные столбцы: {', '.join(missing)}"]
    idx = {h: i for i, h in enumerate(header)}
    for li in range(1, len(lines)):
        cells = _split_csv_line(lines[li])
        line_no = li + 1

        def get_num(col: str) -> float:
            v = float(cells[idx[col]])
            if not math.isfinite(v):
                errors.append(f"Строка {line_no}: нечисловое значение «{col}» = «{cells[idx[col]]}»")
                return math.nan
            return v

        pre_raw = (cells[idx["pre_settled"]] if idx["pre_settled"] < len(cells) else "").strip().lower()
        try:
            row = AcquisitionRow(
                run=(cells[idx["run"]] or "").strip(),
                direction=(cells[idx["direction"]] or "").strip(),
                step=get_num("step"), v_before_V=get_num("v_before_V"),
                v_after_V=get_num("v_after_V"), a_s=get_num("a_s"), b_s=get_num("b_s"),
                i_A=get_num("i_A"), u_i_A=get_num("u_i_A"), leak_A=get_num("leak_A"),
                u_leak_ind_A=get_num("u_leak_ind_A"), u_dt_s=get_num("u_dt_s"),
                pre_settled=pre_raw in ("true", "1"),
            )
        except (ValueError, IndexError):
            errors.append(f"Строка {line_no}: нечисловое значение")
            continue
        if any(e.startswith(f"Строка {line_no}:") for e in errors):
            continue
        if not row.run or not row.direction:
            errors.append(f"Строка {line_no}: пустые run/direction")
            continue
        rows.append(row)
    if not rows and not errors:
        errors.append("Нет строк данных")
    return rows, errors


def steps_csv(runs: List[RunResult]) -> str:
    """Итоговая таблица шагов всех прогонов (для экспорта)."""
    header = ("run,direction,cycle,step,v_before_V,v_after_V,v_mid_V,dv_V,pre_settled,"
              "q_obs_C,u_q_C,q_gap_model_C,q_gap_bound_C,q_corrected_C,c_sec_F,u_c_F,"
              "tail_B_A,tail_S_A_s,tail_uB_A,tail_uS_A_s,tail_settled,usable_for_pair,"
              "w_port_J,d_U_J,dissipated_J,energy_residual_J,flags")
    rows = [header]
    for run in runs:
        for st in run.steps:
            rows.append(",".join([
                _csv_escape(run.label), st.direction, str(run.cycle), str(st.step),
                _num(st.vBefore_V), _num(st.vAfter_V), _num(st.vMid_V), _num(st.dv_V),
                "true" if st.preSettled else "false",
                _num(st.qObs_C), _num(st.uQ_C), _num(st.qGapModel_C), _num(st.qGapBound_C),
                _num(st.qCorrected_C), _num(st.cSec_F), _num(st.uC_F),
                _num(st.tailB_A), _num(st.tailS_A_s), _num(st.tailUB_A), _num(st.tailUS_A_s),
                "true" if st.tailOk else "false", "true" if st.usableForPair else "false",
                _num(st.wPort_J), _num(st.dU_J), _num(st.dissipated_J), _num(st.energyResidual_J),
                _csv_escape(";".join(st.flags) if st.flags else "OK"),
            ]))
    return "\n".join(rows) + "\n"


def pairs_csv(pairs: List[FwdRevPair]) -> str:
    header = "run,v_mid_V,c_forward_F,c_reverse_interpolated_F,delta_c_F"
    rows = [header] + [
        ",".join([_csv_escape(p.run), _num(p.vMid_V), _num(p.cForward_F),
                  _num(p.cReverseInterpolated_F), _num(p.deltaC_F)])
        for p in pairs
    ]
    return "\n".join(rows) + "\n"


def analysis_steps_csv(steps: List[AnalyzeStepRow]) -> str:
    header = ("run,direction,step,v_mid_V,q_observed_C,u_q_C,q_gap_model_C,q_gap_bound_C,"
              "q_corrected_C,c_sec_F,u_c_F,tail_current_A,tail_slope_A_s,tail_settled,"
              "pre_settled,gap_duration_s,usable_for_pair,flags")
    rows = [header]
    for r in steps:
        rows.append(",".join([
            _csv_escape(r.run), r.direction, _num(r.step), _num(r.vMid_V),
            _num(r.qObserved_C), _num(r.uQ_C), _num(r.qGapModel_C), _num(r.qGapBound_C),
            _num(r.qCorrected_C), _num(r.cSec_F), _num(r.uC_F),
            _num(r.tailCurrent_A), _num(r.tailSlope_A_s),
            "true" if r.tailSettled else "false", "true" if r.preSettled else "false",
            _num(r.gapDuration_s), "true" if r.usableForPair else "false",
            _csv_escape(";".join(r.flags) if r.flags else "OK"),
        ]))
    return "\n".join(rows) + "\n"

# ============================== Фемтофарадный консультант (расширение диапазона) ==============================
#
# Диапазон ёмкостей расширен до фемтофарад (1 фФ = 1e-15 Ф). Всё, что ниже —
# расчёт по явным формулам на документированных параметрах B2910BL:
#   * минимальный диапазон измерения тока 10 нА, разрешение 10 фА (User's Guide,
#     табл. 2-7; примечание «a» — доступно для B2910BL);
#   * минимальное разрешение измерения B2910BL 0.01 пА = 10 фА (табл. 2-1);
#   * максимальное напряжение ±210 В (табл. 2-1/2-3);
#   * «Performing Low Current Measurements»: по умолчанию диапазоны 10 нА и 100 нА
#     НЕ используются — для малых токов задавать FIXED 10 нА/100 нА;
#   * Measurement Time = Aperture + Overhead (тайминг обеспечивает триггерная
#     модель прибора, §6).
# Шумовая неопределённость на апертуру u_i — ВХОД модели (по умолчанию 10 фА,
# консервативно равно разрешению диапазона 10 нА); фактическое значение берут
# из свидетельства о калибровке/измерений фона (§12).

@dataclass
class FFInput:
    c_F: float = 100e-15          # ожидаемая ёмкость DUT
    dv_V: float = 1.0             # шаг напряжения
    t_step_s: float = 2.7         # длительность шага ≥ времени релаксации DUT
    aperture_s: float = 0.2       # апертура (10 PLC @ 50 Гц — speed LONG)
    duty: float = 0.9             # доля апертуры в периоде (interval = aperture/duty)
    u_i_A: float = 10e-15         # шумовая неопределённость на апертуру (МОДЕЛЬ)
    target_snr: float = 10.0      # целевое отношение сигнал/шум по заряду
    leak_S: float = 0.0           # ожидаемая утечка DUT (для оценки фона)


@dataclass
class FFResult:
    dq_C: float                 # ΔQ = C·ΔV
    n_windows: int              # число апертур на шаг
    interval_s: float           # период триггера
    i_typ_A: float              # типичный ток сигнала ΔQ/T_step
    u_q_C: float                # неопределённость заряда u_Q = u_i·τ·√N
    snr: float                  # ΔQ/u_Q
    q_lsb_C: float              # заряд, соответствующий одному кванту (u_i·τ)
    i_peak_A: float             # пиковый ток фронта ΔQ/tranTime (модель)
    tran_time_min_s: float      # минимальный фронт, чтобы пик ≤ 10 нА
    q_leak_C: float             # заряд утечки за шаг G·V̄·T_step
    verdict: str                # OK | MARGINAL | BELOW_FLOOR
    verdict_ru: str
    notes: List[str]
    dv_required_V: float        # ΔV, необходимое для target SNR
    feasible: bool              # достижимо в пределах ±210 В


def ff_evaluate(inp: FFInput) -> FFResult:
    """Оценка осуществимости квазистатического измерения ёмкости C.

    Формулы (показываются в интерфейсе):
      ΔQ = C·ΔV;                       T_step = N·(τ + dead)
      I_typ = ΔQ/T_step;               u_Q = u_i·τ·√N   (независимые окна)
      SNR = ΔQ/u_Q;                    q_lsb = u_i·τ
    """
    if inp.c_F <= 0 or inp.dv_V <= 0 or inp.t_step_s <= 0 or inp.aperture_s <= 0:
        raise ValueError("Консультант: C, ΔV, T_step и апертура должны быть > 0")
    if not (0 < inp.duty <= 1):
        raise ValueError("Консультант: duty должен быть в (0, 1]")
    if inp.u_i_A <= 0:
        raise ValueError("Консультант: u_i должна быть > 0")

    interval = inp.aperture_s / inp.duty
    n_windows = max(1, int(math.floor(inp.t_step_s / interval)))
    t_actual = n_windows * interval

    dq = inp.c_F * inp.dv_V
    i_typ = dq / t_actual
    u_q = inp.u_i_A * inp.aperture_s * math.sqrt(n_windows)
    snr = dq / u_q if u_q > 0 else math.inf
    q_lsb = inp.u_i_A * inp.aperture_s
    # Пиковый ток при линейном фронте tranTime: I_peak = ΔQ/tranTime.
    # Минимальный фронт, чтобы пик не перегружал диапазон 10 нА (0 ≤ |I| ≤ 10.6 нА):
    tran_time_min = dq / 10e-9
    i_peak = dq / max(tran_time_min, 1e-9)
    v_avg = inp.dv_V / 2  # грубая оценка среднего напряжения на шаге от нуля
    q_leak = inp.leak_S * v_avg * t_actual

    # Требуемое ΔV для целевого SNR (u_Q фиксирован таймингом):
    dv_required = inp.target_snr * u_q / inp.c_F if inp.c_F > 0 else math.inf
    feasible = dv_required <= 2 * SMU_V_MAX  # амплитуда развёртки в пределах ±210 В

    if snr >= 20 and i_typ >= 0.5 * inp.u_i_A:
        verdict = "OK"
        verdict_ru = "ДОСТИЖИМО: запас по шуму достаточен"
    elif snr >= 5:
        verdict = "MARGINAL"
        verdict_ru = "НА ГРАНИ: результат будет сильно зашумлён; увеличьте ΔV/апертуру или усредните циклы"
    else:
        verdict = "BELOW_FLOOR"
        verdict_ru = ("НИЖЕ ПОЛА: при заданном шуме u_i заряд C·ΔV не разрешим; "
                      "увеличьте ΔV (до ±210 В) или пересмотрите метод")

    notes: List[str] = []
    if i_typ < inp.u_i_A:
        notes.append(f"Типичный ток {format_amps(i_typ)} ниже u_i = {format_amps(inp.u_i_A)}: "
                     "отдельные отсчёты субквантовые — результат держится только на интеграле заряда.")
    if dv_required > inp.dv_V:
        notes.append(f"Для целевого SNR = {inp.target_snr:g} требуется ΔV ≥ "
                     f"{format_volts(dv_required)} (задано {format_volts(inp.dv_V)}).")
    if not feasible:
        notes.append(f"Требуемое ΔV ({format_volts(dv_required)}) превышает возможности "
                     f"B2910BL (±{SMU_V_MAX:g} В) — прямое QSCV для этой ёмкости невозможно.")
    if tran_time_min > 1e-5:
        notes.append(f"Фронт перехода ≥ {format_seconds(tran_time_min)}, чтобы пиковый ток "
                     "не перегружал диапазон 10 нА (задайте tranTime в паспорте).")
    if q_leak > 0.1 * dq:
        notes.append(f"Ожидаемый заряд утечки за шаг {format_coulombs(q_leak)} превышает 10 % "
                     f"сигнала {format_coulombs(dq)} — обязательно вычитание фона (blank/mean_tail).")
    notes.append("Диапазон 10 нА по умолчанию НЕ используется (User's Guide, «Performing Low "
                 "Current Measurements») — задавайте FIXED 10 нА (:SENS:CURR:RANG 1E-8).")

    return FFResult(
        dq_C=dq, n_windows=n_windows, interval_s=interval, i_typ_A=i_typ,
        u_q_C=u_q, snr=snr, q_lsb_C=q_lsb, i_peak_A=i_peak,
        tran_time_min_s=tran_time_min, q_leak_C=q_leak,
        verdict=verdict, verdict_ru=verdict_ru, notes=notes,
        dv_required_V=dv_required, feasible=feasible,
    )


def ff_recommend_config(base: MeasurementConfig, res: FFResult, inp: FFInput) -> MeasurementConfig:
    """Патч паспорта программы под фемтофарадный режим (кнопка «Применить к паспорту»).

    FIXED 10 нА; апертура LONG-класса; допуски хвоста масштабированы под
    типичный ток (5 % сигнала, но не ниже шумового порога k·u_i/√n_tail)."""
    n_tail = max(3, min(res.n_windows, 8))
    tail_floor = 1.96 * inp.u_i_A / math.sqrt(n_tail)
    tail_limit = max(0.05 * res.i_typ_A, tail_floor)
    slope_limit = tail_limit / max(res.interval_s * res.n_windows, 1e-3)
    return replace(
        base,
        protocol="aperture_charge",
        iRangeMode="fixed",
        iRange_A=10e-9,
        speedMode="manual",
        apertureMode="seconds",
        aperture_s=inp.aperture_s,
        trigInterval_s=round(res.interval_s, 9),
        trigCount=max(8, min(res.n_windows, 10000)),
        tranTime_s=max(base.tranTime_s, res.tran_time_min_s),
        backgroundMode="mean_tail" if inp.leak_S > 0 else "none",
        tailPoints=n_tail,
        tailLimit_A=tail_limit,
        slopeLimit_A_s=slope_limit,
        gapMode="model",
        fitModel="exp2",
        uIOffset_A=inp.u_i_A,
    )


# ------------------------------ Пресеты фемтофарадного режима ------------------------------

def _ff_config(**over) -> MeasurementConfig:
    base = MeasurementConfig(
        name="Фемтофарадный протокол B",
        protocol="aperture_charge",
        iCompliance_A=1e-8,
        iRangeMode="fixed",
        iRange_A=10e-9,
        speedMode="manual",
        apertureMode="seconds",
        aperture_s=0.2,           # 10 PLC @ 50 Гц (speed LONG)
        lineFreq_Hz=50.0,
        trigInterval_s=0.222,
        trigCount=12,
        tranDelay_s=0.0,
        acqDelay_s=0.0,
        tranTime_s=1e-5,
        preBias_V=0.0,
        preBiasDuration_s=1.0,
        backgroundMode="mean_tail",
        tailPoints=6,
        tailLimit_A=2e-14,
        slopeLimit_A_s=1e-13,
        gapMode="model",
        fitModel="exp2",
        uIOffset_A=10e-15,
    )
    return replace(base, **over)


FF_CONFIG_PRESETS: List[Tuple[str, str, str, MeasurementConfig]] = [
    # (ключ, заголовок, подпись, конфигурация)
    ("ff_100f", "100 фФ — эталон (протокол B)",
     "Развёртка 0…1 В, шаг 0.25 В, 12 апертур по 200 мс, FIXED 10 нА",
     _ff_config(name="100 фФ эталон — протокол B",
                vStart_V=0.0, vEnd_V=1.0, vStep_V=0.25)),
    ("ff_10f", "10 фФ — эталон (протокол B)",
     "Развёртка 0…5 В (шаг 1 В) — заряд шага 1e-14 Кл, SNR ≈ 2–3 на 10 фА шума",
     _ff_config(name="10 фФ эталон — протокол B",
                vStart_V=0.0, vEnd_V=5.0, vStep_V=1.0)),
    ("ff_1f", "1 фФ — эталон (протокол B)",
     "Развёртка 0…20 В (шаг 5 В): предел метода; заряд шага 5e-15 Кл",
     _ff_config(name="1 фФ эталон — протокол B",
                vStart_V=0.0, vEnd_V=20.0, vStep_V=5.0)),
    ("ff_mos", "MOS-фФ демо (протокол B)",
     "cOx=100 фФ, cMin=20 фФ, ловушка 15 фФ (τ=0.5 с); развёртка ±0.6 В",
     _ff_config(name="MOS-фФ демо — протокол B",
                vStart_V=-0.6, vEnd_V=0.6, vStep_V=0.2,
                trigCount=60, tailLimit_A=1e-14,
                uIOffset_A=3e-15)),
]

# Соответствие пресетов конфигурации → DUT
FF_DUT_OF_PRESET = {
    "ff_100f": "ref_c_100f",
    "ff_10f": "ref_c_10f",
    "ff_1f": "ref_c_1f",
    "ff_mos": "mos_ff_demo",
}


def ff_verification_request(c_F: float, dv_V: float, tolerance_pct: float) -> VerificationRequest:
    """Верификация §6 на фемтофарадном эталоне с таймингом fF-режима."""
    return VerificationRequest(
        refType="c", cRef_F=c_F, rRef_ohm=0.0, dv_V=dv_V,
        timing=VerificationTiming(
            interval_s=0.222, aperture_s=0.2, tranDelay_s=0.0,
            acqDelay_s=0.0, tranTime_s=1e-5, count=12),
        tolerancePct=tolerance_pct, noise_A=10e-15,
    )

# ============================== Бэкенд реального прибора (PyVISA, опционально) ==============================
#
# Тот же конвейер анализа, что и в симуляции: апертуры → analyse_step →
# StepResult/RunResult → журнал §13. Тайминг обеспечивается триггерной моделью
# прибора (:TRIG:SOUR TIM / :TRIG:COUN / :TRIG:ACQ:DEL / *WAI), Python/VISA-цикл
# синхронизацию не гарантирует (методика §6).
#
# Используемые команды — только сверенные (см. заголовок генератора SCPI).
# pyvisa импортируется ЛЕНИВО — скрипт работает и без него (симуляция/офлайн).

class InstrumentError(Exception):
    pass


class VisaInstrument:
    """Обёртка pyvisa: подключение, транзакции, журнал обмена TX/RX."""

    def __init__(self, resource: str):
        self.resource = resource
        self.rm = None
        self.inst = None
        self.idn = ""
        self.log: List[str] = []

    def _record(self, direction: str, data: str) -> None:
        self.log.append(f"{direction} {data.strip()[:200]}")
        if len(self.log) > 4000:
            del self.log[:2000]

    def connect(self) -> str:
        try:
            import pyvisa  # ленивый импорт
        except ImportError as e:
            raise InstrumentError(
                "Модуль pyvisa не установлен: pip install pyvisa pyvisa-py "
                "(+ бэкенд NI-VISA/Keysight IO Libraries)") from e
        try:
            self.rm = pyvisa.ResourceManager()
        except Exception as e:  # noqa: BLE001 — сообщение драйвера важно пользователю
            raise InstrumentError(
                "ResourceManager не открыт — установите VISA-бэкенд "
                "(NI-VISA / Keysight IO Libraries / pyvisa-py): " + str(e)) from e
        try:
            self.inst = self.rm.open_resource(self.resource)
            self.inst.timeout = 10000
            self.idn = self.query("*IDN?")
            return self.idn
        except Exception as e:  # noqa: BLE001
            raise InstrumentError(f"Не удалось открыть ресурс «{self.resource}»: {e}") from e

    @property
    def connected(self) -> bool:
        return self.inst is not None

    def write(self, cmd: str) -> None:
        if self.inst is None:
            raise InstrumentError("Прибор не подключён")
        self._record(">>", cmd)
        self.inst.write(cmd)

    def query(self, cmd: str) -> str:
        if self.inst is None:
            raise InstrumentError("Прибор не подключён")
        self._record(">>", cmd)
        resp = self.inst.query(cmd)
        self._record("<<", resp)
        return resp.strip()

    def query_values(self, cmd: str) -> List[float]:
        """Чтение массива (:FETC:ARR:CURR?) без numpy: разбор CSV-ответа."""
        resp = self.query(cmd)
        try:
            return [float(x) for x in resp.split(",") if x.strip()]
        except ValueError as e:
            raise InstrumentError(f"Нечисловый ответ на {cmd}: {resp[:120]}") from e

    def set_timeout_ms(self, ms: int) -> None:
        if self.inst is not None:
            self.inst.timeout = max(1000, int(ms))

    def close(self) -> None:
        try:
            if self.inst is not None:
                try:
                    self.inst.write(":OUTP OFF")
                except Exception:  # noqa: BLE001 — лучшее усилие при закрытии
                    pass
                self.inst.close()
        finally:
            self.inst = None
            if self.rm is not None:
                try:
                    self.rm.close()
                except Exception:  # noqa: BLE001
                    pass
                self.rm = None


class HardwareRunner:
    """Выполнение passports программы на реальном B2910BL (протоколы A и B)."""

    def __init__(self, visa: VisaInstrument, cfg: MeasurementConfig, wiring: str = "4w"):
        self.visa = visa
        self.cfg = cfg
        self.wiring = wiring

    # ------------------------------ Конфигурация ------------------------------

    def _configure(self) -> None:
        cfg = self.cfg
        validate_config(cfg)
        v = self.visa
        v.write("*RST")
        v.write("*CLS")
        v.write(":OUTP OFF")
        v.write(':SENS:FUNC "CURR"')
        v.write(":SENS:REM ON" if self.wiring == "4w" else ":SENS:REM OFF")
        v.write(f":SENS:CURR:PROT {fmt_scpi(cfg.iCompliance_A)}")
        if cfg.iRangeMode == "auto":
            v.write(":SENS:CURR:RANG:AUTO ON")
        else:
            v.write(f":SENS:CURR:RANG {fmt_scpi(cfg.iRange_A)}")
        nplc, _note = _nplc_of(cfg)
        v.write(f":SENS:CURR:NPLC {fmt_scpi(nplc)}")
        if cfg.protocol == "aperture_charge":
            v.write(":SYST:TIME:TIM:COUN:RES:AUTO ON")
        v.write(":SOUR:FUNC:MODE VOLT")
        v.write(":SOUR:VOLT:MODE FIX" if cfg.protocol == "aperture_charge" else ":SOUR:VOLT:MODE SWE")

    def _sweep_setup(self, direction: str) -> None:
        cfg = self.cfg
        v = self.visa
        if cfg.protocol == "kuhn_staircase":
            if direction == "reverse":
                v.write(f":SOUR:VOLT:STAR {fmt_scpi(cfg.vEnd_V)}")
                v.write(f":SOUR:VOLT:STOP {fmt_scpi(cfg.vStart_V)}")
                v.write(":SOUR:SWE:DIR DOWN")
            else:
                v.write(f":SOUR:VOLT:STAR {fmt_scpi(cfg.vStart_V)}")
                v.write(f":SOUR:VOLT:STOP {fmt_scpi(cfg.vEnd_V)}")
                v.write(":SOUR:SWE:DIR UP")
            v.write(f":SOUR:VOLT:STEP {fmt_scpi(cfg.vStep_V)}")
            v.write(":SOUR:SWE:SPAC LIN")
            v.write(":SOUR:SWE:RANG FIX")

    def _settle_check(self, n: int = 5, interval: float = 0.1) -> float:
        """Оценка |dI/dt| на preBias: n отсчётов с периодом interval (§7.1).
        Использует те же команды триггерной модели."""
        cfg = self.cfg
        v = self.visa
        v.write(f":SOUR:VOLT {fmt_scpi(cfg.preBias_V)}")
        v.write(":TRIG:SOUR TIM")
        v.write(f":TRIG:TIM {fmt_scpi(interval)}")
        v.write(f":TRIG:COUN {n}")
        v.write(":TRIG:ACQ:DEL 0")
        v.write(":INIT:IMM")
        v.write("*WAI")
        vals = v.query_values(":FETC:ARR:CURR?")
        if len(vals) < 2:
            return math.inf
        return max(abs(vals[k + 1] - vals[k]) / interval for k in range(len(vals) - 1))

    # ------------------------------ Протокол B ------------------------------

    def _run_step_B(self, v_prev: float, v_next: float) -> List[float]:
        cfg = self.cfg
        v = self.visa
        v.write(f":SOUR:VOLT {fmt_scpi(v_prev)}")
        v.write(f":SOUR:VOLT:TRIG {fmt_scpi(v_next)}")
        v.write(":TRIG:SOUR TIM")
        v.write(f":TRIG:TIM {fmt_scpi(cfg.trigInterval_s)}")
        v.write(f":TRIG:COUN {cfg.trigCount}")
        v.write(f":TRIG:TRAN:DEL {fmt_scpi(cfg.tranDelay_s)}")
        v.write(f":TRIG:ACQ:DEL {fmt_scpi(cfg.acqDelay_s)}")
        step_timeout = int((cfg.trigCount * cfg.trigInterval_s + 30) * 1000)
        v.set_timeout_ms(step_timeout)
        v.write(":INIT:IMM")
        v.write("*WAI")
        vals = v.query_values(":FETC:ARR:CURR?")
        if len(vals) != cfg.trigCount:
            raise InstrumentError(
                f"Ожидалось {cfg.trigCount} апертур, получено {len(vals)}")
        return vals

    # ------------------------------ Основной прогон ------------------------------

    def run(self, progress: Optional[Callable[[str], bool]] = None) -> List[RunResult]:
        cfg = self.cfg
        validate_config(cfg)
        self._configure()
        aperture = aperture_from_config(cfg)
        win_a, win_b = aperture_times(cfg)
        n_win = len(win_a)
        is_proto_b = cfg.protocol == "aperture_charge"
        eff_tail_points = min(cfg.tailPoints, n_win)
        n_steps = max(1, round(abs(cfg.vEnd_V - cfg.vStart_V) / cfg.vStep_V))
        fwd_points = [cfg.vEnd_V if i == n_steps else cfg.vStart_V + i * cfg.vStep_V
                      for i in range(n_steps + 1)]

        runs: List[RunResult] = []
        executed = 0
        stop_reason: Optional[str] = None
        tail_b_history: List[float] = []
        interrupted = False

        analysis_options = AnalyzeOptions(
            model=cfg.fitModel, gapMode=cfg.gapMode, gapCurrentBound_A=cfg.gapCurrentBound_A,
            tailPoints=eff_tail_points, tailLimit_A=cfg.tailLimit_A,
            slopeLimit_A_s=cfg.slopeLimit_A_s, kFactor=cfg.kFactor,
            uCurrentCommon_A=cfg.uCurrentCommon_A, uLeakCommon_A=cfg.uLeakCommon_A,
            uClockRel=cfg.uClockRel, uDv_V=cfg.uDv_V,
        )

        def make_run(direction: str, cycle: int) -> RunResult:
            label = "Прямой ход" if direction == "forward" else "Обратный ход"
            if cfg.cycles > 1:
                label += f", цикл {cycle}"
            return RunResult(
                id=_new_run_id(), label=label, direction=direction, cycle=cycle,
                protocol=cfg.protocol, config=replace(cfg),
                dut=DutModel(kind="hardware", label="Реальный DUT (прибор)"),
                timing=RunTiming(
                    tranDelay_s=cfg.tranDelay_s, acqDelay_s=cfg.acqDelay_s,
                    tranTime_s=cfg.tranTime_s, interval_s=cfg.trigInterval_s,
                    aperture_s=aperture, firstA_s=win_a[0], firstB_s=win_b[0],
                    lastB_s=win_b[-1],
                    initialGap_s=win_a[0] - (cfg.tranDelay_s + cfg.tranTime_s),
                    deadTime_s=cfg.trigInterval_s - aperture,
                ),
                createdAt=datetime.now().astimezone().isoformat(timespec="seconds"),
            )

        try:
            # Подготовка §7.1.
            self.visa.set_timeout_ms(max(10000, int(cfg.preBiasDuration_s * 1000 + 30000)))
            self.visa.write(":OUTP ON")
            prebias_confirmed = cfg.preBiasDuration_s > 0
            if cfg.preBiasDuration_s > 0:
                self.visa.write(f":SOUR:VOLT {fmt_scpi(cfg.preBias_V)}")
                time.sleep(cfg.preBiasDuration_s)
                if cfg.settleDIdt_A_s > 0:
                    prebias_confirmed = self._settle_check() <= cfg.settleDIdt_A_s

            for cycle in range(1, cfg.cycles + 1):
                if stop_reason or interrupted:
                    break
                for direction in cfg.directions:
                    if stop_reason or interrupted:
                        break
                    points = fwd_points if direction == "forward" else list(reversed(fwd_points))
                    run: Optional[RunResult] = None
                    i_all: List[float] = []
                    if is_proto_b:
                        first_point = points[0]
                        if abs(first_point - cfg.preBias_V) > 1e-9:
                            self.visa.write(f":SOUR:VOLT {fmt_scpi(first_point)}")
                            time.sleep(max(cfg.preBiasDuration_s, 0.1))
                    else:
                        # Протокол A: вся развёртка одним блоком — читаем до цикла шагов.
                        self._sweep_setup(direction)
                        n_points = n_steps + 1
                        self.visa.write(f":TRIG:TIM {fmt_scpi(cfg.trigInterval_s)}")
                        self.visa.write(f":TRIG:COUN {n_points}")
                        self.visa.write(f":TRIG:TRAN:DEL {fmt_scpi(cfg.tranDelay_s)}")
                        self.visa.set_timeout_ms(int((n_points * cfg.trigInterval_s + 60) * 1000))
                        self.visa.write(":OUTP ON")
                        self.visa.write(":INIT:IMM")
                        self.visa.write("*WAI")
                        i_all = self.visa.query_values(":FETC:ARR:CURR?")
                        self.visa.write(":OUTP OFF")
                        if len(i_all) < n_points:
                            raise InstrumentError(
                                f"Развёртка: ожидалось {n_points} точек, получено {len(i_all)}")
                    for k in range(n_steps):
                        if stop_reason or interrupted:
                            break
                        v_prev = points[k]
                        v_next = points[k + 1]
                        pre_settled = prebias_confirmed if executed == 0 else k > 0

                        if is_proto_b:
                            i_arr = self._run_step_B(v_prev, v_next)
                            u_arr = [cfg.uIOffset_A + cfg.uIGainRel * abs(x) for x in i_arr]
                        else:
                            # Точки развёртки уже прочитаны одним блоком (выше).
                            i_arr = [i_all[k]]
                            u_arr = [cfg.uIOffset_A + cfg.uIGainRel * abs(i_all[k])]

                        dv = v_next - v_prev
                        apertures: List[ApertureRecord] = []
                        step_flags: set = set()
                        for j in range(len(i_arr)):
                            i_meas = i_arr[j]
                            u_rec = u_arr[j]
                            _r, overload = pick_range(abs(i_meas), cfg)
                            status = "OK"
                            if overload:
                                status = "OVLD"
                                step_flags.add("RANGE_OVERLOAD")
                            if abs(i_meas) > cfg.iCompliance_A:
                                status = "COMP"
                                step_flags.add("COMPLIANCE_EXCEEDED")
                            apertures.append(ApertureRecord(
                                j + 1, win_a[j] if j < n_win else 0.0,
                                win_b[j] if j < n_win else 0.0,
                                i_meas, 0.0, u_rec, status))

                        if is_proto_b:
                            base_input = StepAnalysisInput(
                                a=win_a[:], b=win_b[:], i=list(i_arr), uI=list(u_arr),
                                leak=[0.0] * n_win, uLeakInd=[0.0] * n_win,
                                uDt=[(cfg.uClockRel * (win_b[j] - win_a[j])
                                      if cfg.uClockRel > 0 else 0.0) for j in range(n_win)],
                                preSettled=pre_settled, dv=dv,
                                vBefore=v_prev, vAfter=v_next, options=analysis_options,
                            )
                            leak = [0.0] * n_win
                            if cfg.backgroundMode in ("mean_tail", "linear_tail"):
                                pass1 = analyse_step(replace(base_input,
                                    options=replace(analysis_options, model="none")))
                                if not any(f.startswith("DATA_INVALID") for f in pass1.flags) \
                                        and math.isfinite(pass1.tailB):
                                    if cfg.backgroundMode == "mean_tail":
                                        leak = [pass1.tailB] * n_win
                                    else:
                                        t_mean = sum((win_a[j] + win_b[j]) / 2 for j in
                                                     range(n_win - eff_tail_points, n_win)) / eff_tail_points
                                        leak = [pass1.tailB + pass1.tailS *
                                                ((win_a[j] + win_b[j]) / 2 - t_mean)
                                                for j in range(n_win)]
                            for j in range(n_win):
                                apertures[j].i_background_A = leak[j]
                            analysis = analyse_step(replace(base_input, leak=leak[:]))
                        else:
                            r = dv / cfg.trigInterval_s
                            dt = win_b[0] - win_a[0]
                            q_obs = i_arr[0] * dt
                            analysis = StepAnalysisOutput(
                                qObs=q_obs, uQ=u_arr[0] * dt, qGapModel=0.0, qGapBound=0.0,
                                qCorrected=q_obs, cSec=abs(i_arr[0] / r),
                                uC=u_arr[0] / abs(r), tailB=0.0, tailS=0.0, tailUB=0.0,
                                tailUS=0.0, tailOk=True, gaps=[], gapDuration=0.0,
                                fit=None, flags=[], usableForPair=pre_settled)

                        flags = list(dict.fromkeys(list(analysis.flags) + sorted(step_flags)))
                        if not pre_settled:
                            flags.append("FIRST_STEP_DIAGNOSTIC")
                        initial_gap = win_a[0] - (cfg.tranDelay_s + cfg.tranTime_s)
                        if initial_gap > 0:
                            flags.append("MISSED_START")
                        if win_a[0] > 0:
                            flags.append("INITIAL_GAP_PRESENT")
                        flags = list(dict.fromkeys(flags))

                        step = StepResult(
                            step=k, direction=direction, vBefore_V=v_prev, vAfter_V=v_next,
                            vMid_V=(v_prev + v_next) / 2, dv_V=dv, preSettled=pre_settled,
                            apertures=apertures, gaps_s=analysis.gaps,
                            gapDuration_s=analysis.gapDuration,
                            qObs_C=analysis.qObs, uQ_C=analysis.uQ,
                            qGapModel_C=analysis.qGapModel, qGapBound_C=analysis.qGapBound,
                            qCorrected_C=analysis.qCorrected, cSec_F=analysis.cSec,
                            uC_F=analysis.uC, tailB_A=analysis.tailB, tailS_A_s=analysis.tailS,
                            tailUB_A=analysis.tailUB, tailUS_A_s=analysis.tailUS,
                            tailOk=analysis.tailOk, fit=analysis.fit,
                            wPort_J=0.0, dU_J=0.0, dissipated_J=0.0, energyResidual_J=0.0,
                            flags=flags, usableForPair=analysis.usableForPair, truth=None,
                        )
                        if not is_proto_b:
                            r = dv / cfg.trigInterval_s
                            step.kuhn = KuhnInfo(
                                iMeas_A=i_arr[0], rampRate_Vps=r,
                                cKuhn_F=abs(i_arr[0] / r),
                                cKuhnLeakCorrected_F=abs((i_arr[0] - apertures[0].i_background_A) / r),
                                apertureStart_s=win_a[0])

                        tail_b_history.append(abs(analysis.tailB))
                        stop: Optional[str] = None
                        if "RANGE_OVERLOAD" in step_flags:
                            stop = "OVERLOAD"
                        elif "COMPLIANCE_EXCEEDED" in step_flags:
                            stop = "COMPLIANCE"
                        else:
                            if abs(analysis.tailB) > 10 * cfg.tailLimit_A and len(tail_b_history) >= 3:
                                L = len(tail_b_history)
                                if tail_b_history[L - 3] < tail_b_history[L - 2] < tail_b_history[L - 1]:
                                    stop = "BREAKDOWN_OR_LEAKAGE_GROWTH"
                            if not stop and cfg.stopOnTailFail and not analysis.tailOk:
                                stop = "TAIL_NOT_SETTLED"

                        if run is None:
                            run = make_run(direction, cycle)
                            runs.append(run)
                        run.steps.append(step)
                        run.cvCurve.append(CvPoint(
                            vMid_V=step.vMid_V, cSec_F=step.cSec_F, uC_F=step.uC_F,
                            cSecTrue_F=None, direction=direction, usable=step.usableForPair))
                        executed += 1
                        if progress is not None:
                            if not progress(f"{run.label}: шаг {k + 1}/{n_steps}"):
                                interrupted = True
                                stop = "USER_STOP"
                        if stop:
                            stop_reason = stop
                            run.stopped = True
                            run.stopReason = stop
            # Площадь петли (§10) — по наблюдаемым зарядам.
            by_cycle: Dict[int, List[RunResult]] = {}
            for r_ in runs:
                by_cycle.setdefault(r_.cycle, []).append(r_)
            for rs in by_cycle.values():
                fwd = next((x for x in rs if x.direction == "forward"), None)
                rev = next((x for x in rs if x.direction == "reverse"), None)
                if fwd is None or rev is None:
                    continue
                path = fwd.steps + rev.steps
                qs: List[float] = []
                q_acc = 0.0
                for st in path:
                    qs.append(q_acc)
                    q_acc += st.qObs_C
                area = 0.0
                for k in range(len(path)):
                    q_next = qs[k + 1] if k + 1 < len(path) else 0.0
                    area += path[k].vMid_V * (q_next - qs[k])
                fwd.loopArea_J = abs(area)
                rev.loopArea_J = abs(area)
            if interrupted and runs and not stop_reason:
                runs[-1].stopped = True
                runs[-1].stopReason = "USER_STOP"
            return runs
        finally:
            try:
                self.visa.write(f":SOUR:VOLT {fmt_scpi(self.cfg.preBias_V)}")
                self.visa.write(":OUTP OFF")
            except Exception:  # noqa: BLE001 — лучшее усилие при ошибке
                pass

# ============================== Виджеты tkinter ==============================
# Собственные облегчённые компоненты: числовые поля с научной записью,
# график на Canvas (без внешних зависимостей), таблица, бейджи, диаграмма тайминга.
#
# Приглашены только стандартные библиотеки; tkinter импортируется в run_gui().

COLOR_OK = "#059669"      # изумрудный — pass
COLOR_WARN = "#D97706"    # янтарный — предупреждение
COLOR_FAIL = "#DC2626"    # красный — ошибка
COLOR_INFO = "#52525B"    # нейтральный
COLOR_REV = "#EA580C"     # оранжевый — обратный ход
COLOR_TRUE = "#71717A"    # серый — «истина» модели

FONT_MONO = ("Courier New", 9)
FONT_SMALL = ("TkDefaultFont", 9)


def _tk() -> "object":
    import tkinter as tk
    return tk


# ------------------------------ Числовое поле ------------------------------

class NumField:
    """Entry для числа в научной записи: «2e-11», «0.05», запятая → точка.
    Красный фон при недопустимом значении; get() → float | None."""

    def __init__(self, master, label: str, width: int = 12, unit: str = "",
                 on_change: Optional[Callable[[], None]] = None):
        import tkinter as tk
        from tkinter import ttk
        self.frame = ttk.Frame(master)
        self.label = label
        self.unit = unit
        self.on_change = on_change
        self.var = tk.StringVar()
        self.entry = ttk.Entry(self.frame, textvariable=self.var, width=width, font=FONT_MONO)
        self.entry.pack(side="left")
        if unit:
            ttk.Label(self.frame, text=unit, foreground=COLOR_INFO).pack(side="left", padx=(3, 0))
        self._valid_state = True
        self.var.trace_add("write", self._validate)
        self.frame.numfield = self

    def _validate(self, *_args) -> None:
        s = self.var.get().strip().replace(",", ".")
        ok = True
        if s not in ("", "-", "+", ".", "-.", "+."):
            try:
                float(s)
            except ValueError:
                ok = False
        self._valid_state = ok
        self.entry.configure(foreground=COLOR_FAIL if not ok else "black")
        if self.on_change and ok:
            self.on_change()

    def get(self) -> Optional[float]:
        if not self._valid_state:
            return None
        s = self.var.get().strip().replace(",", ".")
        if s in ("", "-", "+", ".", "-.", "+."):
            return None
        try:
            v = float(s)
            return v if math.isfinite(v) else None
        except ValueError:
            return None

    def set(self, value: float) -> None:
        self.var.set(repr(value) if isinstance(value, float) else str(value))
        self._validate()


class EngField(NumField):
    """NumField + живая подпись значения в инженерной нотации."""

    def __init__(self, master, label: str, unit_si: str, width: int = 12,
                 on_change: Optional[Callable[[], None]] = None):
        super().__init__(master, label, width, on_change=on_change)
        import tkinter as tk
        from tkinter import ttk
        self.eng_var = tk.StringVar(value="")
        self.eng_label = ttk.Label(self.frame, textvariable=self.eng_var,
                                   foreground=COLOR_INFO, font=FONT_SMALL)
        self.eng_label.pack(side="left", padx=(6, 0))
        self._unit_si = unit_si
        self._refresh()

    def _validate(self, *args) -> None:
        super()._validate(*args)
        self._refresh()

    def _refresh(self) -> None:
        v = self.get()
        self.eng_var.set(f"= {format_eng(v, self._unit_si)}" if v is not None else "")

    def set(self, value: float) -> None:
        super().set(value)
        self._refresh()


class LabeledNumRow:
    """Строка «подпись : [EngField]» в сетке grid."""

    def __init__(self, master, row: int, label: str, unit_si: str = "",
                 on_change: Optional[Callable[[], None]] = None, width: int = 12):
        from tkinter import ttk
        ttk.Label(master, text=label).grid(row=row, column=0, sticky="w", pady=2)
        self.field = EngField(master, label, unit_si, width=width, on_change=on_change)
        self.field.frame.grid(row=row, column=1, sticky="w", pady=2)


# ------------------------------ Бейджи ------------------------------

def badge(parent, text: str, kind: str = "info") -> "ttk.Label":
    """Цветной текстовый бейдж: ok | warn | fail | info | rev."""
    from tkinter import ttk
    color = {"ok": COLOR_OK, "warn": COLOR_WARN, "fail": COLOR_FAIL,
             "info": COLOR_INFO, "rev": COLOR_REV, "true": COLOR_TRUE}.get(kind, COLOR_INFO)
    lbl = ttk.Label(parent, text=text, foreground=color, font=("", 9, "bold"))
    return lbl


def flag_badges(parent, flags: List[str], wrap: bool = True) -> None:
    """Раскраска флагов шага: TAIL/GAPS/PRECONDITION — янтарным, остальное — красным."""
    from tkinter import ttk
    if not flags:
        badge(parent, "OK — проверенные допущения всё равно требуются (§9)", "ok").pack(anchor="w")
        return
    frame = ttk.Frame(parent)
    frame.pack(anchor="w", fill="x")
    amber = {"TAIL_NOT_SETTLED", "GAPS_UNACCOUNTED", "GAPS_BOUNDED_NOT_CORRECTED",
             "MODEL_DEPENDENT_GAP_CORRECTION", "PRECONDITION_NOT_CONFIRMED",
             "INITIAL_GAP_PRESENT", "BLANK_UNAVAILABLE", "BACKGROUND_UNAVAILABLE",
             "FIRST_STEP_DIAGNOSTIC", "TAIL_FIT_SINGULAR", "MISSED_START"}
    for i, f in enumerate(flags):
        kind = "warn" if f in amber else "fail"
        badge(frame, f"• {f}" + (f" — {FLAG_DESCRIPTIONS[f]}" if f in FLAG_DESCRIPTIONS else ""),
              kind).pack(anchor="w")


# ------------------------------ Таблица ------------------------------

class DataTable:
    """Treeview с прокруткой, фиксированной шапкой и раскраской строк."""

    def __init__(self, master, columns: List[Tuple[str, str, int, str]],
                 max_height: int = 16, on_select: Optional[Callable[[str], None]] = None):
        import tkinter as tk
        from tkinter import ttk
        self.frame = ttk.Frame(master)
        cols = [c[0] for c in columns]
        self.tree = ttk.Treeview(self.frame, columns=cols, show="headings",
                                 height=max_height, selectmode="browse")
        vsb = ttk.Scrollbar(self.frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        for key, title, width, anchor in columns:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=anchor, stretch=False)
        self.tree.tag_configure("ok", foreground=COLOR_OK)
        self.tree.tag_configure("warn", foreground=COLOR_WARN)
        self.tree.tag_configure("fail", foreground=COLOR_FAIL)
        self.tree.tag_configure("dim", foreground=COLOR_TRUE)
        self.on_select = on_select
        if on_select:
            self.tree.bind("<<TreeviewSelect>>", self._selected)

    def _selected(self, _event) -> None:
        sel = self.tree.selection()
        if sel and self.on_select:
            self.on_select(sel[0])

    def set_rows(self, rows: List[Tuple], tags: Optional[List[str]] = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, row in enumerate(rows):
            self.tree.insert("", "end", iid=str(i),
                             values=[str(v) for v in row],
                             tags=(tags[i],) if tags and tags[i] else ())

    def clear(self) -> None:
        self.tree.delete(*self.tree.get_children())


# ------------------------------ График на Canvas ------------------------------

class PlotCanvas:
    """Лёгкий график: линии, точки, столбцы, границы погрешности, легенда."""

    def __init__(self, master, x_label: str = "", y_label: str = "", title: str = "",
                 height: int = 260, min_width: int = 420):
        import tkinter as tk
        from tkinter import ttk
        self.frame = ttk.Frame(master)
        self.canvas = tk.Canvas(self.frame, height=height, bg="white",
                                highlightthickness=1, highlightbackground="#E4E4E7")
        self.canvas.pack(fill="both", expand=True)
        self.x_label = x_label
        self.y_label = y_label
        self.title = title
        self.series: List[Dict] = []
        self.min_width = min_width
        self.canvas.bind("<Configure>", lambda e: self.render())

    def clear(self) -> None:
        self.series = []
        self.render()

    def add_series(self, name: str, xs: Sequence[float], ys: Sequence[float],
                   color: str = COLOR_OK, style: str = "line", dash=None,
                   err: Optional[Sequence[float]] = None, legend: bool = True) -> None:
        if not xs:
            return
        self.series.append(dict(name=name, xs=list(xs), ys=list(ys), color=color,
                                style=style, dash=dash, err=list(err) if err else None,
                                legend=legend))

    def render(self) -> None:
        import tkinter as tk
        c = self.canvas
        c.delete("all")
        w = max(c.winfo_width(), self.min_width)
        h = max(c.winfo_height(), 120)
        left, right, top, bottom = 76, 18, 16, 42
        plot_w = w - left - right
        plot_h = h - top - bottom
        if plot_w < 40 or plot_h < 40 or not self.series:
            c.create_text(w // 2, h // 2, text="нет данных", fill=COLOR_TRUE)
            return

        xs_all: List[float] = []
        ys_all: List[float] = []
        for s in self.series:
            xs_all += [x for x in s["xs"] if math.isfinite(x)]
            ys_all += [y for y in s["ys"] if math.isfinite(y)]
        if not xs_all or not ys_all:
            c.create_text(w // 2, h // 2, text="нет данных", fill=COLOR_TRUE)
            return
        xmin, xmax = min(xs_all), max(xs_all)
        ymin, ymax = min(ys_all), max(ys_all)
        if xmin == xmax:
            xmin -= 1.0
            xmax += 1.0
        if ymin == ymax:
            pad = abs(ymin) * 0.1 + 1e-30
            ymin -= pad
            ymax += pad
        else:
            pad = (ymax - ymin) * 0.06
            ymin -= pad
            ymax += pad
        # Погрешности в масштаб оси Y.
        for s in self.series:
            if s["err"]:
                for y, e in zip(s["ys"], s["err"]):
                    if math.isfinite(y) and math.isfinite(e):
                        ymin = min(ymin, y - e)
                        ymax = max(ymax, y + e)

        def px(x: float) -> float:
            return left + (x - xmin) / (xmax - xmin) * plot_w

        def py(y: float) -> float:
            return top + (1 - (y - ymin) / (ymax - ymin)) * plot_h

        # Сетка и деления.
        for i in range(6):
            yy = top + plot_h * i / 5
            val = ymax - (ymax - ymin) * i / 5
            c.create_line(left, yy, left + plot_w, yy, fill="#F4F4F5")
            c.create_text(left - 6, yy, text=_tick_label(val), anchor="e",
                          fill=COLOR_INFO, font=FONT_SMALL)
        for i in range(6):
            xx = left + plot_w * i / 5
            val = xmin + (xmax - xmin) * i / 5
            c.create_line(xx, top, xx, top + plot_h, fill="#F4F4F5")
            c.create_text(xx, top + plot_h + 8, text=_tick_label(val),
                          fill=COLOR_INFO, font=FONT_SMALL)
        c.create_rectangle(left, top, left + plot_w, top + plot_h, outline="#D4D4D8")
        if ymin < 0 < ymax:
            c.create_line(left, py(0), left + plot_w, py(0), fill="#A1A1AA", dash=(2, 3))
        if self.title:
            c.create_text(left + plot_w / 2, 8, text=self.title, fill="#18181B")
        if self.x_label:
            c.create_text(left + plot_w / 2, top + plot_h + 28, text=self.x_label,
                          fill=COLOR_INFO)
        if self.y_label:
            c.create_text(12, top + plot_h / 2, text=self.y_label, fill=COLOR_INFO,
                          angle=90)

        # Ряды данных.
        for s in self.series:
            xs, ys = s["xs"], s["ys"]
            color, style = s["color"], s["style"]
            if style == "bar":
                n = len(xs)
                bar_w = max(2.0, min(0.8 * plot_w / max(n - 1, 1), 26.0))
                x0_base = px(xs[0])
                for x, y in zip(xs, ys):
                    if not (math.isfinite(x) and math.isfinite(y)):
                        continue
                    xpix = px(x)
                    ypix = py(y)
                    y0 = py(0)
                    c.create_rectangle(xpix - bar_w / 2, min(ypix, y0),
                                       xpix + bar_w / 2, max(ypix, y0),
                                       fill=color, outline=color)
                _ = x0_base
            elif style == "scatter":
                r = 3
                for x, y in zip(xs, ys):
                    if not (math.isfinite(x) and math.isfinite(y)):
                        continue
                    c.create_oval(px(x) - r, py(y) - r, px(x) + r, py(y) + r,
                                  fill=color, outline=color)
            else:
                pts = []
                for x, y in zip(xs, ys):
                    if not (math.isfinite(x) and math.isfinite(y)):
                        continue
                    pts += [px(x), py(y)]
                if len(pts) >= 4:
                    c.create_line(*pts, fill=color, dash=s["dash"] or "", width=2)
            if s["err"]:
                for x, y, e in zip(xs, ys, s["err"]):
                    if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(e) and e > 0):
                        continue
                    xpix = px(x)
                    c.create_line(xpix, py(y - e), xpix, py(y + e), fill=color)
                    c.create_line(xpix - 2, py(y - e), xpix + 2, py(y - e), fill=color)
                    c.create_line(xpix - 2, py(y + e), xpix + 2, py(y + e), fill=color)

        # Легенда.
        legends = [s for s in self.series if s["legend"]]
        if legends:
            lx = left + plot_w - 8
            ly = top + 8
            cwid = 30 + max(len(s["name"]) for s in legends) * 7
            c.create_rectangle(lx - cwid, ly, lx, ly + 16 * len(legends) + 6,
                               fill="#FFFFFF", outline="#D4D4D8")
            for i, s in enumerate(legends):
                yy = ly + 12 + 16 * i
                c.create_line(lx - cwid + 8, yy, lx - cwid + 26, yy,
                              fill=s["color"], width=2,
                              dash=s["dash"] or "" if s["style"] == "line" else "")
                c.create_text(lx - cwid + 30, yy, text=s["name"], anchor="w",
                              fill="#18181B", font=FONT_SMALL)


def _tick_label(v: float) -> str:
    if v == 0:
        return "0"
    a = abs(v)
    if 0.01 <= a < 10000:
        return f"{v:.3g}"
    return format_eng(v, "", 3).strip()


# ------------------------------ Диаграмма тайминга ------------------------------

class TimingDiagram:
    """Схема шага: профиль напряжения, фронт, окна [a_j, b_j] (§5, §6)."""

    def __init__(self, master, height: int = 150):
        import tkinter as tk
        from tkinter import ttk
        self.frame = ttk.Frame(master)
        self.canvas = tk.Canvas(self.frame, height=height, bg="white",
                                highlightthickness=1, highlightbackground="#E4E4E7")
        self.canvas.pack(fill="both", expand=True)

    def draw(self, tran_delay: float, tran_time: float, interval: float,
             aperture: float, n_windows: int, v_prev: float = 0.0, v_next: float = 1.0) -> None:
        c = self.canvas
        c.delete("all")
        w = max(c.winfo_width(), 380)
        h = max(c.winfo_height(), 100)
        end_t = max((n_windows - 1) * interval + aperture, tran_delay + tran_time) * 1.05
        if end_t <= 0:
            end_t = 1.0
        left, right, top, bottom = 56, 16, 18, 44
        pw, ph = w - left - right, h - top - bottom

        def tx(t: float) -> float:
            return left + t / end_t * pw

        def ty(v: float) -> float:
            vmin, vmax = min(v_prev, v_next), max(v_prev, v_next)
            if vmin == vmax:
                vmin, vmax = vmin - 1, vmax + 1
            return top + (1 - (v - vmin) / (vmax - vmin)) * ph

        # Профиль напряжения.
        pts = [left, ty(v_prev), tx(tran_delay), ty(v_prev),
               tx(tran_delay + tran_time), ty(v_next),
               left + pw, ty(v_next)]
        c.create_line(*pts, fill="#18181B", width=2)

        # Окна апертур.
        for j in range(n_windows):
            a = j * interval
            b = a + aperture
            c.create_rectangle(tx(a), top + 6, tx(b), top + ph - 6,
                               fill="#34D39955", outline=COLOR_OK)
        # Аннотации.
        c.create_line(left, top + ph, left, top, fill="#D4D4D8")
        c.create_text(left + pw / 2, h - 26,
                      text=f"период τ = {format_seconds(interval)}, апертура = "
                           f"{format_seconds(aperture)}, окон = {n_windows}",
                      fill=COLOR_INFO, font=FONT_SMALL)
        c.create_text(left + pw / 2, h - 10,
                      text=f"задержка источника {format_seconds(tran_delay)}, "
                           f"фронт {format_seconds(tran_time)}; a₁ = {format_seconds(0.0)}",
                      fill=COLOR_INFO, font=FONT_SMALL)
        c.create_text(6, ty(v_prev), text=f"{v_prev:g} В", anchor="w", fill=COLOR_INFO)
        c.create_text(6, ty(v_next), text=f"{v_next:g} В", anchor="w", fill=COLOR_INFO)


# ------------------------------ Журнал (текст с тегами) ------------------------------

class LogBox:
    def __init__(self, master, height: int = 12):
        import tkinter as tk
        from tkinter import ttk
        self.frame = ttk.Frame(master)
        self.text = tk.Text(self.frame, height=height, wrap="word", state="disabled",
                            font=FONT_MONO, bg="#FAFAFA")
        vsb = ttk.Scrollbar(self.frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=vsb.set)
        self.text.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        for tag, color in (("ok", COLOR_OK), ("warn", COLOR_WARN), ("fail", COLOR_FAIL),
                           ("info", "#18181B"), ("dim", COLOR_TRUE)):
            self.text.tag_configure(tag, foreground=color)

    def write(self, message: str, tag: str = "info") -> None:
        self.text.configure(state="normal")
        self.text.insert("end", message.rstrip("\n") + "\n", tag)
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

# ============================== Панели GUI: часть 1 ==============================
# Оборудование (§4), Паспорт программы (§5), фФ-консультант, Верификация (§6).


def _bind_cfg_field(container, row: int, label: str, unit: str,
                    cfg_get: Callable[[], float], cfg_set: Callable[[float], None],
                    width: int = 12) -> EngField:
    """Строка «подпись : [поле]» с записью в паспорт программы при каждом изменении."""
    from tkinter import ttk
    field = EngField(container, label, unit, width=width)
    ttk.Label(container, text=label).grid(row=row, column=0, sticky="w", pady=2)
    field.frame.grid(row=row, column=1, sticky="w", pady=2)
    field.set(cfg_get())  # начальное значение — без записи в конфигурацию

    def on_change() -> None:
        v = field.get()
        if v is not None:
            cfg_set(v)

    field.on_change = on_change
    return field


def _bind_selector(container, row: int, label: str, values: List[str],
                   get: Callable[[], str], set_: Callable[[str], None],
                   width: int = 20) -> None:
    import tkinter as tk
    from tkinter import ttk
    ttk.Label(container, text=label).grid(row=row, column=0, sticky="w", pady=2)
    var = tk.StringVar(value=get())

    def on_sel(_e=None) -> None:
        set_(var.get())

    cb = ttk.Combobox(container, textvariable=var, values=values, state="readonly",
                      width=width)
    cb.grid(row=row, column=1, sticky="w", pady=2)
    cb.bind("<<ComboboxSelected>>", on_sel)


def _bind_check(parent, label: str, get: Callable[[], bool],
                set_: Callable[[bool], None]) -> None:
    import tkinter as tk
    from tkinter import ttk
    var = tk.BooleanVar(value=get())
    ttk.Checkbutton(parent, text=label, variable=var,
                    command=lambda: set_(var.get())).pack(anchor="w")


class EquipmentTab:
    """§4: паспорт прибора, схема подключения, чек-лист, контрольные команды."""

    def __init__(self, notebook, app):
        import tkinter as tk
        from tkinter import ttk
        self.app = app
        self.state = app.state
        outer = ttk.Frame(notebook, padding=10)
        notebook.add(outer, text=" Оборудование (§4) ")
        canvas_wrap = ttk.Frame(outer)
        canvas_wrap.pack(fill="both", expand=True)

        # --- Паспорт прибора ---
        card = ttk.LabelFrame(canvas_wrap, text="Паспорт прибора", padding=8)
        card.pack(fill="x", pady=(0, 6))
        instr = self.state.instrument
        self.e_model = self._text_row(card, 0, "Модель", instr.model)
        self.e_serial = self._text_row(card, 1, "Заводской номер", instr.serial_number)
        self.e_fw = self._text_row(card, 2, "Прошивка", instr.firmware)
        self.e_cal = self._text_row(card, 3, "Дата калибровки (ISO)", instr.calibration_date)
        self.e_temp = self._text_row(card, 4, "Температура, °C", str(instr.temperature_c or ""))
        self.e_warm = self._text_row(card, 5, "Прогрев, мин", str(instr.warmup_minutes))
        flags_frame = ttk.Frame(card)
        flags_frame.grid(row=6, column=0, columnspan=2, sticky="w")
        _bind_check(flags_frame, "Self-cal (*CAL?) выполнена",
                    lambda: self.state.instrument.self_cal_done,
                    self._set_flag("self_cal_done"))
        _bind_check(flags_frame, "Self-test (*TST?) пройден",
                    lambda: self.state.instrument.self_test_done,
                    self._set_flag("self_test_done"))
        self.wiring_var = self._combo_row(card, "Схема", ["4w", "2w"], instr.wiring,
                                          self._set_wiring)
        self.e_notes = self._text_row(card, 8, "Примечания (кабели, guard, оснастка)",
                                      instr.notes, width=60)

        # --- Схема подключения ---
        wiring = ttk.LabelFrame(canvas_wrap, text="Подключение B2910BL ↔ DUT (§4)", padding=8)
        wiring.pack(fill="x", pady=6)
        self.wiring_table = DataTable(wiring, [
            ("term", "Вывод прибора", 120, "w"),
            ("dut", "DUT", 260, "w"),
            ("role", "Назначение", 300, "w"),
        ], max_height=5)
        self.wiring_table.frame.pack(fill="x")
        self.wiring_table.set_rows(WIRING_ROWS)

        # --- Чек-лист §4 ---
        chk = ttk.LabelFrame(canvas_wrap, text="Чек-лист подготовки (§4)", padding=8)
        chk.pack(fill="x", pady=6)
        self.check_vars: List = []
        for item in EQUIPMENT_CHECKLIST:
            var = tk.BooleanVar(value=False)
            ttk.Checkbutton(chk, text=item, variable=var).pack(anchor="w")
            self.check_vars.append(var)

        # --- Контрольные команды ---
        cmds = ttk.LabelFrame(canvas_wrap, text="Контрольные команды", padding=8)
        cmds.pack(fill="x", pady=6)
        ttk.Label(cmds, text="*IDN? — идентификация; *TST? — самотест (прибор занят на время теста); "
                             "*CAL? — self-cal (длительная операция; после 60 мин прогрева).",
                  foreground=COLOR_INFO, wraplength=640).pack(anchor="w")
        row = ttk.Frame(cmds)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text="Отправить *IDN?",
                   command=lambda: self._send_check("*IDN?")).pack(side="left", padx=4)
        ttk.Button(row, text="Отправить *TST? (самотест)",
                   command=lambda: self._send_check("*TST?")).pack(side="left", padx=4)
        ttk.Button(row, text="Отправить *CAL? (self-cal)",
                   command=lambda: self._send_check("*CAL?")).pack(side="left", padx=4)
        ttk.Button(row, text="Копировать :SENS:REM ON",
                   command=lambda: self.app.copy_to_clipboard(":SENS:REM ON")).pack(side="left", padx=4)

        # --- Предупреждение ---
        safety = ttk.LabelFrame(canvas_wrap, text="Безопасность", padding=8)
        safety.pack(fill="x", pady=6)
        badge(safety, SAFETY_TEXT, "fail").pack(anchor="w")
        ttk.Label(safety, text="Self-cal не заменяет сервисную (поверочную) калибровку.",
                  foreground=COLOR_INFO).pack(anchor="w")

    # ------------------------------ вспомогательные ------------------------------

    def _text_row(self, parent, row, label, value, width=30):
        import tkinter as tk
        from tkinter import ttk
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        var = tk.StringVar(value=value)
        ttk.Entry(parent, textvariable=var, width=width).grid(row=row, column=1,
                                                              sticky="w", pady=2)
        return var

    def _combo_row(self, parent, label, values, current, setter):
        import tkinter as tk
        from tkinter import ttk
        ttk.Label(parent, text=label).grid(row=6, column=0, sticky="w", pady=2)
        var = tk.StringVar(value=current)
        cb = ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=10)
        cb.grid(row=6, column=1, sticky="w", pady=2)
        cb.bind("<<ComboboxSelected>>", lambda e: setter(var.get()))
        return var

    def _set_flag(self, name):
        def setter(value: bool) -> None:
            setattr(self.state.instrument, name, value)
        return setter

    def _set_wiring(self, value: str) -> None:
        self.state.instrument.wiring = value

    def _send_check(self, cmd: str) -> None:
        visa: VisaInstrument = self.state.visa
        if not visa.connected:
            self.app.status(f"{cmd}: прибор не подключён — команда скопирована в буфер",
                            "warn")
            self.app.copy_to_clipboard(cmd)
            return
        try:
            self.app.status(f"Отправка {cmd} …", "info")
            if cmd == "*CAL?":
                visa.set_timeout_ms(600000)
            elif cmd == "*TST?":
                visa.set_timeout_ms(120000)
            resp = visa.query(cmd)
            if cmd == "*IDN?":
                parts = resp.split(",")
                if len(parts) >= 4:
                    self.state.instrument.model = parts[1].strip()
                    self.state.instrument.serial_number = parts[2].strip()
                    self.state.instrument.firmware = parts[3].strip()
            self.app.status(f"{cmd} → {resp}", "ok")
        except Exception as e:  # noqa: BLE001
            self.app.status(f"{cmd} → ошибка: {e}", "fail")

    def refresh(self) -> None:
        pass


class PassportTab:
    """§5: паспорт программы — все параметры протокола + модель DUT + пресеты."""

    def __init__(self, notebook, app):
        from tkinter import ttk
        self.app = app
        self.state = app.state
        outer = ttk.Frame(notebook, padding=10)
        notebook.add(outer, text=" Паспорт программы (§5) ")
        self._master_ref = outer
        self.body = ttk.Frame(outer)
        self.body.pack(fill="both", expand=True)
        self._build(self.body)
        self._warnings = ttk.LabelFrame(outer, text="Проверка значений", padding=6)
        self._warnings.pack(fill="x", pady=(6, 0))
        self._warn_text = ttk.Label(self._warnings, text="", foreground=COLOR_WARN,
                                    wraplength=900, justify="left")
        self._warn_text.pack(anchor="w")
        self.refresh_warnings()

    def _cfg(self) -> MeasurementConfig:
        return self.state.config

    def _set(self, name: str):
        def setter(v) -> None:
            setattr(self.state.config, name, v)
            self.refresh_warnings()
        return setter

    def _build(self, parent) -> None:
        from tkinter import ttk
        cfg = self._cfg()

        # ----- Напряжение и последовательность -----
        v_card = ttk.LabelFrame(parent, text="Напряжение и последовательность (§5, §7.3)", padding=8)
        v_card.grid(row=0, column=0, sticky="nw", padx=(0, 6))
        _bind_cfg_field(v_card, 0, "vStart, В", "В", lambda: cfg.vStart_V, self._set("vStart_V"))
        _bind_cfg_field(v_card, 1, "vEnd, В", "В", lambda: cfg.vEnd_V, self._set("vEnd_V"))
        _bind_cfg_field(v_card, 2, "Шаг vStep, В", "В", lambda: cfg.vStep_V, self._set("vStep_V"))
        _bind_cfg_field(v_card, 3, "Циклов", "", lambda: float(cfg.cycles),
                        self._set_int("cycles"))
        dirf = ttk.Frame(v_card)
        dirf.grid(row=4, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(dirf, text="Направления:").pack(side="left")
        self.fwd_var = self._dir_check(dirf, "прямой ход", "forward")
        self.rev_var = self._dir_check(dirf, "обратный ход", "reverse")
        _bind_selector(v_card, 5, "Протокол",
                       ["aperture_charge (B — зарядовый)", "kuhn_staircase (A — Кун)"],
                       self._proto_get, self._proto_set, width=28)

        # ----- Ток -----
        i_card = ttk.LabelFrame(parent, text="Ток (§5)", padding=8)
        i_card.grid(row=0, column=1, sticky="nw", padx=6)
        _bind_cfg_field(i_card, 0, "Compliance, А", "А", lambda: cfg.iCompliance_A,
                        self._set("iCompliance_A"))
        _bind_selector(i_card, 1, "Диапазон", ["auto", "fixed"],
                       lambda: cfg.iRangeMode, self._set("iRangeMode"))
        _bind_cfg_field(i_card, 2, "Фикс. диапазон, А", "А", lambda: cfg.iRange_A,
                        self._set("iRange_A"))
        ttk.Label(i_card, text="Диапазоны B2900B/BL: 10 нА … 100 мА (табл. 2-7);\n"
                               "10 нА — разрешение 10 фА (B2910BL).",
                  foreground=COLOR_INFO, justify="left").grid(row=3, column=0,
                                                              columnspan=2, sticky="w")

        # ----- Интегрирование -----
        ap_card = ttk.LabelFrame(parent, text="Интегрирование (§5)", padding=8)
        ap_card.grid(row=1, column=0, sticky="nw", padx=(0, 6), pady=6)
        _bind_selector(ap_card, 0, "Скорость (User's Guide)",
                       ["auto", "short", "medium", "normal", "long", "manual"],
                       lambda: cfg.speedMode, self._set_speed)
        _bind_selector(ap_card, 1, "Режим апертуры", ["seconds", "nplc"],
                       lambda: cfg.apertureMode, self._set("apertureMode"))
        _bind_cfg_field(ap_card, 2, "NPLC", "", lambda: cfg.nplc, self._set("nplc"))
        _bind_cfg_field(ap_card, 3, "Апертура, с", "с", lambda: cfg.aperture_s,
                        self._set("aperture_s"))
        _bind_cfg_field(ap_card, 4, "Частота сети, Гц", "Гц", lambda: cfg.lineFreq_Hz,
                        self._set("lineFreq_Hz"))
        self.ap_label = ttk.Label(ap_card, text="", foreground=COLOR_OK)
        self.ap_label.grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # ----- Тайминг -----
        t_card = ttk.LabelFrame(parent, text="Тайминг — триггерная модель B2900 (§5, §6)", padding=8)
        t_card.grid(row=1, column=1, sticky="nw", padx=6, pady=6)
        _bind_cfg_field(t_card, 0, "Интервал триггера, с", "с", lambda: cfg.trigInterval_s,
                        self._set("trigInterval_s"))
        _bind_cfg_field(t_card, 1, "Апертур на шаг", "", lambda: float(cfg.trigCount),
                        self._set_int("trigCount"))
        _bind_cfg_field(t_card, 2, "Задержка источника, с", "с", lambda: cfg.tranDelay_s,
                        self._set("tranDelay_s"))
        _bind_cfg_field(t_card, 3, "Задержка измерения, с", "с", lambda: cfg.acqDelay_s,
                        self._set("acqDelay_s"))
        _bind_cfg_field(t_card, 4, "Длительность фронта, с", "с", lambda: cfg.tranTime_s,
                        self._set("tranTime_s"))
        self.diagram = TimingDiagram(t_card)
        self.diagram.frame.grid(row=5, column=0, columnspan=2, sticky="we", pady=(4, 0))

        # ----- Подготовка и фон -----
        p_card = ttk.LabelFrame(parent, text="Подготовка (§7.1) и фон (§5)", padding=8)
        p_card.grid(row=2, column=0, sticky="nw", padx=(0, 6), pady=6)
        _bind_cfg_field(p_card, 0, "Начальный bias, В", "В", lambda: cfg.preBias_V,
                        self._set("preBias_V"))
        _bind_cfg_field(p_card, 1, "Выдержка, с", "с", lambda: cfg.preBiasDuration_s,
                        self._set("preBiasDuration_s"))
        _bind_cfg_field(p_card, 2, "Критерий |dI/dt|, А/с", "А/с", lambda: cfg.settleDIdt_A_s,
                        self._set("settleDIdt_A_s"))
        _bind_selector(p_card, 3, "Фон",
                       ["none", "blank", "mean_tail", "linear_tail"],
                       lambda: cfg.backgroundMode, self._set("backgroundMode"), width=14)

        # ----- Допуски и пропуски -----
        tol_card = ttk.LabelFrame(parent, text="Допуски (§9), пропуски, модель", padding=8)
        tol_card.grid(row=2, column=1, sticky="nw", padx=6, pady=6)
        _bind_cfg_field(tol_card, 0, "Окон в хвосте", "", lambda: float(cfg.tailPoints),
                        self._set_int("tailPoints"))
        _bind_cfg_field(tol_card, 1, "I_limit хвоста, А", "А", lambda: cfg.tailLimit_A,
                        self._set("tailLimit_A"))
        _bind_cfg_field(tol_card, 2, "S_limit наклона, А/с", "А/с", lambda: cfg.slopeLimit_A_s,
                        self._set("slopeLimit_A_s"))
        _bind_cfg_field(tol_card, 3, "k (доверительный)", "", lambda: cfg.kFactor,
                        self._set("kFactor"))
        _bind_cfg_field(tol_card, 4, "Сходимость, %", "%", lambda: cfg.convergencePct,
                        self._set("convergencePct"))
        _bind_cfg_field(tol_card, 5, "Граница тока пропусков, А", "А",
                        lambda: cfg.gapCurrentBound_A, self._set("gapCurrentBound_A"))
        _bind_selector(tol_card, 6, "Пропуски", ["reject", "bound", "model"],
                       lambda: cfg.gapMode, self._set("gapMode"))
        _bind_selector(tol_card, 7, "Модель подгонки", ["none", "exp1", "exp2"],
                       lambda: cfg.fitModel, self._set("fitModel"))
        stopf = ttk.Frame(tol_card)
        stopf.grid(row=8, column=0, columnspan=2, sticky="w")
        _bind_check(stopf, "Останавливать при непрошедшем хвосте",
                    lambda: cfg.stopOnTailFail, self._set("stopOnTailFail"))

        # ----- Неопределённость и шум -----
        u_card = ttk.LabelFrame(parent, text="Неопределённость (§12) и шумовая модель (симуляция)", padding=8)
        u_card.grid(row=3, column=0, sticky="nw", padx=(0, 6), pady=6)
        _bind_cfg_field(u_card, 0, "u(ток) общий, А", "А", lambda: cfg.uCurrentCommon_A,
                        self._set("uCurrentCommon_A"))
        _bind_cfg_field(u_card, 1, "u(фон) общий, А", "А", lambda: cfg.uLeakCommon_A,
                        self._set("uLeakCommon_A"))
        _bind_cfg_field(u_card, 2, "u(часы) отн.", "", lambda: cfg.uClockRel,
                        self._set("uClockRel"))
        _bind_cfg_field(u_card, 3, "u(ΔV), В", "В", lambda: cfg.uDv_V, self._set("uDv_V"))
        _bind_cfg_field(u_card, 4, "u_i смещение, А", "А", lambda: cfg.uIOffset_A,
                        self._set("uIOffset_A"))
        _bind_cfg_field(u_card, 5, "u_i относительная", "", lambda: cfg.uIGainRel,
                        self._set("uIGainRel"))
        _bind_cfg_field(u_card, 6, "Offset прибора, А", "А", lambda: cfg.iOffset_A,
                        self._set("iOffset_A"))
        ttk.Label(u_card, text="u_i и offset — параметры шумовой МОДЕЛИ для симуляции;\n"
                               "для реальных измерений задаются из калибровки/фона (§12).",
                  foreground=COLOR_INFO, justify="left").grid(row=7, column=0,
                                                              columnspan=2, sticky="w")
        house = ttk.Frame(u_card)
        house.grid(row=8, column=0, columnspan=2, sticky="w")
        _bind_check(house, "Autozero", lambda: cfg.autozero,
                    lambda v: setattr(cfg, "autozero", v))
        _bind_check(house, "Выходной фильтр", lambda: cfg.outputFilter,
                    lambda v: setattr(cfg, "outputFilter", v))
        ttk.Label(u_card, text="(сверять по SCPI Command Reference)", foreground=COLOR_TRUE).grid(
            row=9, column=0, columnspan=2, sticky="w")

        # ----- DUT -----
        d_card = ttk.LabelFrame(parent, text="Модель DUT (симуляция/верификация)", padding=8)
        d_card.grid(row=3, column=1, sticky="nw", padx=6, pady=6)
        import tkinter as tk
        self.dut_var = tk.StringVar(value=self.state.dut_label)
        cb = ttk.Combobox(d_card, textvariable=self.dut_var,
                          values=list(DUT_PRESETS.keys()), state="readonly", width=36)
        cb.grid(row=0, column=0, sticky="w")
        cb.bind("<<ComboboxSelected>>", lambda e: self._select_dut(self.dut_var.get()))
        self.dut_info = ttk.Label(d_card, text="", foreground=COLOR_INFO,
                                  wraplength=380, justify="left")
        self.dut_info.grid(row=1, column=0, sticky="w", pady=4)
        self._render_dut_info()

        # ----- Пресеты -----
        preset_card = ttk.LabelFrame(parent, text="Пресеты конфигурации "
                                                  "(текущие значения будут заменены)", padding=8)
        preset_card.grid(row=4, column=0, columnspan=2, sticky="nw", pady=6)
        rowf = ttk.Frame(preset_card)
        rowf.pack(anchor="w")
        ttk.Button(rowf, text="Протокол B — демо qscv_final",
                   command=self._preset_demo_b).pack(side="left", padx=3)
        ttk.Button(rowf, text="Протокол A — линейная развёртка (Кун)",
                   command=self._preset_proto_a).pack(side="left", padx=3)
        ttk.Button(rowf, text="MOS-демо (протокол B)",
                   command=self._preset_mos).pack(side="left", padx=3)
        for key, title, _sub, _cfg in FF_CONFIG_PRESETS:
            ttk.Button(rowf, text=f"фФ: {title}",
                       command=lambda k=key: self._preset_ff(k)).pack(side="left", padx=3)

    # ------------------------------ обработчики ------------------------------

    def _dir_check(self, parent, text, value):
        import tkinter as tk
        from tkinter import ttk
        var = tk.BooleanVar(value=value in self.state.config.directions)

        def toggle() -> None:
            dirs = [d for d in ("forward", "reverse")
                    if (self.fwd_var.get() and d == "forward") or
                       (self.rev_var.get() and d == "reverse")]
            if not dirs:
                dirs = [value]
                var.set(True)
            self.state.config.directions = dirs
            self.refresh_warnings()

        box = ttk.Checkbutton(parent, text=text, variable=var, command=toggle)
        box.pack(side="left", padx=6)
        return var

    def _proto_get(self) -> str:
        return ("aperture_charge (B — зарядовый)"
                if self.state.config.protocol == "aperture_charge"
                else "kuhn_staircase (A — Кун)")

    def _proto_set(self, value: str) -> None:
        self.state.config.protocol = ("aperture_charge"
                                      if value.startswith("aperture") else "kuhn_staircase")
        self.refresh_warnings()

    def _set_speed(self, value: str) -> None:
        self.state.config.speedMode = value
        self.refresh_warnings()

    def _set_int(self, name: str):
        def setter(v: float) -> None:
            if v is not None:
                setattr(self.state.config, name, max(1, int(round(v))))
                self.refresh_warnings()
        return setter

    def _select_dut(self, key: str) -> None:
        self.state.dut_label = key
        self._render_dut_info()

    def _render_dut_info(self) -> None:
        dut = self.state.current_dut()
        parts = [dut.label]
        if dut.c0_F:
            parts.append(f"C0 = {format_farads(dut.c0_F)}")
        if dut.g_S:
            parts.append(f"G = {format_eng(dut.g_S, 'См')}")
        for b in dut.branches:
            parts.append(f"ветвь {format_farads(b.c_F)}, τ = {format_seconds(b.tau_s)} "
                         f"(R = {format_ohms(b.tau_s / b.c_F)})")
        if dut.kind == "mos_demo":
            parts.append(f"cOx = {format_farads(dut.cOx_F)}, cMin = {format_farads(dut.cMin_F)}, "
                         f"Vfb = {dut.vfb_V:g} В, ширина = {dut.width_V:g} В")
        self.dut_info.configure(text="\n".join(parts))

    def _preset_demo_b(self) -> None:
        self.state.config = MeasurementConfig()
        self.state.dut_label = "demo_qscv"
        self._reload("Загружен пресет: протокол B — демо qscv_final")

    def _preset_proto_a(self) -> None:
        cfg = MeasurementConfig(
            protocol="kuhn_staircase", vStart_V=-2, vEnd_V=2, vStep_V=0.01,
            directions=["forward", "reverse"], cycles=1, speedMode="manual",
            apertureMode="nplc",
            # Апертура должна накрывать период ступени: NPLC 5 @ 50 Гц = 100 мс = T,
            # иначе C = |I/(ΔV/T)| завышается в T/T_ап раз (методика §3).
            nplc=5, trigInterval_s=0.1, trigCount=1)
        self.state.config = cfg
        self.state.dut_label = "demo_qscv"
        self._reload("Загружен пресет: протокол A — линейная развёртка (r = 0.1 В/с)")

    def _preset_mos(self) -> None:
        cfg = MeasurementConfig(
            vStart_V=-0.2, vEnd_V=0.2, vStep_V=0.01,
            trigCount=60, trigInterval_s=0.001, aperture_s=0.0008,
            backgroundMode="none")
        self.state.config = cfg
        self.state.dut_label = "mos_demo"
        self._reload("Загружен пресет: MOS-демо (протокол B)")

    def _preset_ff(self, key: str) -> None:
        for _k, title, _sub, cfg in FF_CONFIG_PRESETS:
            if _k == key:
                self.state.config = replace(cfg)
                self.state.dut_label = FF_DUT_OF_PRESET.get(key, self.state.dut_label)
                self._reload(f"Загружен пресет: {title}")
                return

    def _reload(self, message: str) -> None:
        from tkinter import ttk
        self.body.destroy()
        self.body = ttk.Frame(self._master_ref)
        self.body.pack(fill="both", expand=True)
        self._build(self.body)
        self.refresh_warnings()
        self.app.status(message, "ok")

    # ------------------------------ проверка значений ------------------------------

    def refresh_warnings(self) -> None:
        if not hasattr(self, "_warn_text") or not hasattr(self, "ap_label"):
            return
        cfg = self.state.config
        msgs: List[str] = []
        try:
            aperture = aperture_from_config(cfg)
            self.ap_label.configure(
                text=f"Апертура: {format_seconds(aperture)}"
                     + (f" (NPLC {aperture * cfg.lineFreq_Hz:.4g})"
                        if cfg.lineFreq_Hz > 0 else ""))
        except Exception as e:  # noqa: BLE001
            aperture = None
            msgs.append(str(e))
        try:
            validate_config(cfg)
        except ValueError as e:
            msgs.append(str(e))
        if aperture is not None:
            if cfg.protocol == "aperture_charge" and aperture > 0.9 * cfg.trigInterval_s:
                msgs.append(f"Апертура {format_seconds(aperture)} ≥ 90 % интервала "
                            f"{format_seconds(cfg.trigInterval_s)} — запас на overhead мал.")
            if (cfg.protocol == "aperture_charge" and
                    cfg.trigCount < cfg.tailPoints + 2):
                msgs.append("trigCount < tailPoints + 2: анализ хвоста захватит почти все окна.")
            if (cfg.protocol == "kuhn_staircase" and cfg.trigCount != 1):
                msgs.append("Протокол A: одиночное измерение на ступень (trigCount = 1).")
        if cfg.vEnd_V == cfg.vStart_V:
            msgs.append("vEnd совпадает с vStart.")
        if abs(cfg.vEnd_V) > SMU_V_MAX or abs(cfg.vStart_V) > SMU_V_MAX:
            msgs.append(f"Напряжение за пределами ±{SMU_V_MAX:g} В (B2910BL).")
        if cfg.iRangeMode == "auto" and cfg.iRange_A <= 10e-9 and cfg.uIOffset_A <= 20e-15:
            pass  # подсказка о 10 нА — в фФ-консультанте
        self._warn_text.configure(
            text="\n".join(f"⚠ {m}" for m in msgs) if msgs else "✓ Паспорт программы согласован.")
        try:
            self.diagram.draw(cfg.tranDelay_s, cfg.tranTime_s, cfg.trigInterval_s,
                              aperture_from_config(cfg),
                              1 if cfg.protocol == "kuhn_staircase" else cfg.trigCount,
                              v_prev=0.0, v_next=cfg.vStep_V)
        except Exception:  # noqa: BLE001
            pass

    def refresh(self) -> None:
        self.refresh_warnings()


class FfTab:
    """Фемтофарадный консультант: осуществимость, авто-тайминг, пресеты, верификация."""

    def __init__(self, notebook, app):
        from tkinter import ttk
        self.app = app
        self.state = app.state
        outer = ttk.Frame(notebook, padding=10)
        notebook.add(outer, text=" Фемтофарады ")
        self._outer = outer

        head = ttk.Label(outer, text=(
            "Диапазон расширен до фемтофарад (1 фФ = 1e-15 Ф). Оценка осуществимости — по "
            "документированным параметрам B2910BL: минимальный диапазон измерения 10 нА с "
            "разрешением 10 фА (User's Guide, табл. 2-7), максимум ±210 В. Шум u_i — вход "
            "модели (по умолчанию 10 фА = разрешению диапазона); фактическое значение — из "
            "калибровки/фона (§12)."), foreground=COLOR_INFO, wraplength=920, justify="left")
        head.pack(anchor="w", pady=(0, 6))

        top = ttk.Frame(outer)
        top.pack(fill="x")
        left = ttk.LabelFrame(top, text="Исходные данные", padding=8)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        right = ttk.LabelFrame(top, text="Оценка (пересчитывается при изменении)", padding=8)
        right.pack(side="left", fill="both", expand=True)

        # ----- Ввод -----
        import tkinter as tk
        self.c_var = tk.StringVar(value="100")
        self.c_unit = tk.StringVar(value="фФ (1e-15 Ф)")
        ttk.Label(left, text="Ожидаемая ёмкость").grid(row=0, column=0, sticky="w")
        cf = ttk.Frame(left)
        cf.grid(row=0, column=1, sticky="w", pady=2)
        ttk.Entry(cf, textvariable=self.c_var, width=10, font=FONT_MONO).pack(side="left")
        ccb = ttk.Combobox(cf, textvariable=self.c_unit, width=12, state="readonly",
                           values=["фФ (1e-15 Ф)", "пФ (1e-12 Ф)", "нФ (1e-9 Ф)",
                                   "мкФ (1e-6 Ф)"])
        ccb.pack(side="left", padx=4)
        self.f_dv = self._eng(left, 1, "Шаг напряжения ΔV", "В", 1.0)
        self.f_tstep = self._eng(left, 2, "Длительность шага T (≥ релаксации DUT)", "с", 2.7)
        self.f_ap = self._eng(left, 3, "Апертура τ (10 PLC @ 50 Гц = 0.2 с)", "с", 0.2)
        self.f_duty = self._eng(left, 4, "Скважность апертур (τ/период)", "", 0.9)
        self.f_ui = self._eng(left, 5, "Шум на апертуру u_i", "А", 10e-15)
        self.f_snr = self._eng(left, 6, "Целевое SNR по заряду", "", 10.0)
        self.f_leak = self._eng(left, 7, "Утечка DUT G", "См", 0.0)
        for w in (self.f_dv, self.f_tstep, self.f_ap, self.f_duty, self.f_ui,
                  self.f_snr, self.f_leak):
            w.on_change = self._recompute
        self.c_var.trace_add("write", lambda *_: self._recompute())
        ccb.bind("<<ComboboxSelected>>", lambda e: self._recompute())

        ttk.Label(left, text=(
            "Формулы: ΔQ = C·ΔV;  N = ⌊T/период⌋;  I_typ = ΔQ/T;\n"
            "u_Q = u_i·τ·√N (независимые окна);  SNR = ΔQ/u_Q;\n"
            "q_lsb = u_i·τ;  ΔV_req(SNR) = SNR·u_Q/C."), foreground=COLOR_TRUE,
            justify="left").grid(row=8, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # ----- Результат -----
        self.v_verdict = ttk.Label(right, text="", font=("", 10, "bold"))
        self.v_verdict.grid(row=0, column=0, columnspan=2, sticky="w")
        self.v_stats = ttk.Label(right, text="", justify="left", font=FONT_MONO)
        self.v_stats.grid(row=1, column=0, columnspan=2, sticky="w", pady=4)
        self.v_notes = ttk.Label(right, text="", foreground=COLOR_WARN,
                                 wraplength=430, justify="left")
        self.v_notes.grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(right, text=(
            "Физическое ограничение метода: заряд шага фиксирован (C·ΔV), а шум заряда "
            "растёт как √T — удлинение шага не улучшает SNR; улучшают ΔV (до ±210 В) и "
            "снижение фона. Долговременная стабильность нуля, трибоэлектрика кабелей и "
            "утечка доминируют на шагах длиннее ~10 с — сверяйте с фактическим blank-фоном."),
            foreground=COLOR_INFO, wraplength=430, justify="left").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        actions = ttk.Frame(right)
        actions.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(actions, text="Применить рекомендованный тайминг к паспорту",
                   command=self._apply).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Пересчитать").pack(side="left")

        # ----- Быстрая верификация на фФ-эталонах -----
        ver = ttk.LabelFrame(outer, text="Быстрая верификация §6 на фемтофарадных эталонах "
                                         "(симуляция с шумом u_i = 10 фА)", padding=8)
        ver.pack(fill="x", pady=8)
        vf = ttk.Frame(ver)
        vf.pack(anchor="w")
        ttk.Button(vf, text="100 фФ @ 1 В (допуск 30 %)",
                   command=lambda: self._verify(100e-15, 1.0, 30)).pack(side="left", padx=3)
        ttk.Button(vf, text="10 фФ @ 5 В (допуск 30 %)",
                   command=lambda: self._verify(10e-15, 5.0, 30)).pack(side="left", padx=3)
        ttk.Button(vf, text="1 фФ @ 20 В (допуск 50 %)",
                   command=lambda: self._verify(1e-15, 20.0, 50)).pack(side="left", padx=3)
        self.ver_result = ttk.Label(ver, text="", justify="left", font=FONT_MONO)
        self.ver_result.pack(anchor="w", pady=(4, 0))
        self._recompute()

    def _eng(self, parent, row, label, unit, value):
        f = EngField(parent, label, unit, width=12)
        from tkinter import ttk
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        f.frame.grid(row=row, column=1, sticky="w", pady=2)
        f.set(value)
        return f

    def _c_value(self) -> Optional[float]:
        try:
            v = float(self.c_var.get().strip().replace(",", "."))
        except ValueError:
            return None
        unit = self.c_unit.get()
        factor = {"фФ (1e-15 Ф)": 1e-15, "пФ (1e-12 Ф)": 1e-12,
                  "нФ (1e-9 Ф)": 1e-9, "мкФ (1e-6 Ф)": 1e-6}.get(unit, 1e-15)
        return v * factor if v > 0 else None

    def _collect(self) -> Optional[FFInput]:
        c = self._c_value()
        dv = self.f_dv.get()
        tstep = self.f_tstep.get()
        ap = self.f_ap.get()
        duty = self.f_duty.get()
        ui = self.f_ui.get()
        snr = self.f_snr.get()
        leak = self.f_leak.get()
        if None in (c, dv, tstep, ap, duty, ui, snr, leak):
            return None
        return FFInput(c_F=c, dv_V=dv, t_step_s=tstep, aperture_s=ap,
                       duty=duty, u_i_A=ui, target_snr=snr, leak_S=leak or 0.0)

    def _recompute(self) -> None:
        inp = self._collect()
        if inp is None:
            self.v_verdict.configure(text="— заполните поля —", foreground=COLOR_INFO)
            self.v_stats.configure(text="")
            self.v_notes.configure(text="")
            return
        try:
            res = ff_evaluate(inp)
        except ValueError as e:
            self.v_verdict.configure(text=str(e), foreground=COLOR_FAIL)
            return
        color = {"OK": COLOR_OK, "MARGINAL": COLOR_WARN,
                 "BELOW_FLOOR": COLOR_FAIL}[res.verdict]
        self.v_verdict.configure(
            text=f"{res.verdict_ru}   (SNR = {res.snr:.4g})", foreground=color)
        self.v_stats.configure(text=(
            f"ΔQ = C·ΔV          = {format_coulombs(res.dq_C)}\n"
            f"окон на шаг N       = {res.n_windows}  (период {format_seconds(res.interval_s)})\n"
            f"типичный ток I_typ  = {format_amps(res.i_typ_A)}\n"
            f"u_Q = u_i·τ·√N      = {format_coulombs(res.u_q_C)}\n"
            f"квант q_lsb = u_i·τ = {format_coulombs(res.q_lsb_C)}\n"
            f"заряд утечки за шаг = {format_coulombs(res.q_leak_C)}\n"
            f"ΔV для SNR {inp.target_snr:g}      = {format_volts(res.dv_required_V)}"
            + ("" if res.feasible else "  ← превышает ±210 В")))
        self.v_notes.configure(text="\n".join(f"⚠ {n}" for n in res.notes))

    def _apply(self) -> None:
        inp = self._collect()
        if inp is None:
            self.app.status("фФ: заполните поля консультанта", "warn")
            return
        res = ff_evaluate(inp)
        self.state.config = ff_recommend_config(self.state.config, res, inp)
        self.state.dut_label = self.state.dut_label  # DUT не меняем
        self.app.status("фФ: рекомендованный тайминг применён к паспорту программы "
                        "(FIXED 10 нА, апертура LONG, допуски масштабированы)", "ok")
        if self.app.passport_tab is not None:
            self.app.passport_tab._reload("фФ: паспорт программы обновлён")

    def _verify(self, c_F: float, dv_V: float, tol_pct: float) -> None:
        req = ff_verification_request(c_F, dv_V, tol_pct)
        try:
            res = execute_verification(req)
        except ValueError as e:
            self.ver_result.configure(text=f"Ошибка: {e}", foreground=COLOR_FAIL)
            return
        color = COLOR_OK if res.pass_ else COLOR_FAIL
        mark = "ПРОЙДЕНА" if res.pass_ else "НЕ ПРОЙДЕНА"
        self.ver_result.configure(
            text=(f"{format_farads(c_F)} @ {dv_V:g} В: {mark};  "
                  f"Q_ожид = {format_coulombs(res.qExpected_C)},  "
                  f"Q_восст = {format_coulombs(res.qRecovered_C)},  "
                  f"ошибка = {res.errorRelPct:+.1f} % (допуск ±{tol_pct:g} %),  "
                  f"истина в пропусках = {format_coulombs(res.qInGaps_C)}"),
            foreground=color)
        self.state.verification = res
        self.app.status(f"Верификация фФ {format_farads(c_F)}: {mark} "
                        f"({res.errorRelPct:+.1f} %)", "ok" if res.pass_ else "fail")

    def refresh(self) -> None:
        self._recompute()


class VerificationTab:
    """§6: проверка восстановления известного заряда C·ΔV на эталоне."""

    def __init__(self, notebook, app):
        from tkinter import ttk
        self.app = app
        self.state = app.state
        outer = ttk.Frame(notebook, padding=10)
        notebook.add(outer, text=" Верификация (§6) ")
        top = ttk.Frame(outer)
        top.pack(fill="x")
        left = ttk.LabelFrame(top, text="Эталон и тайминг", padding=8)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        right = ttk.LabelFrame(top, text="Результат", padding=8)
        right.pack(side="left", fill="both", expand=True)

        req = self.state.verification_request
        import tkinter as tk
        self.type_var = tk.StringVar(value=req.refType)

        def on_type(_e=None):
            self.state.verification_request.refType = self.type_var.get()
        ttk.Label(left, text="Тип эталона").grid(row=0, column=0, sticky="w")
        tcb = ttk.Combobox(left, textvariable=self.type_var, values=["c", "rc"],
                           state="readonly", width=8)
        tcb.grid(row=0, column=1, sticky="w", pady=2)
        tcb.bind("<<ComboboxSelected>>", on_type)

        self.f_cref = self._row(left, 1, "Эталонная ёмкость, Ф", req.cRef_F,
                                lambda v: setattr(req, "cRef_F", v))
        self.f_rref = self._row(left, 2, "Эталонный R, Ом (для rc)", req.rRef_ohm,
                                lambda v: setattr(req, "rRef_ohm", v))
        self.f_dv = self._row(left, 3, "Переход ΔV, В", req.dv_V,
                              lambda v: setattr(req, "dv_V", v))
        self.f_int = self._row(left, 4, "Интервал, с", req.timing.interval_s,
                               lambda v: setattr(req.timing, "interval_s", v))
        self.f_ap = self._row(left, 5, "Апертура, с", req.timing.aperture_s,
                              lambda v: setattr(req.timing, "aperture_s", v))
        self.f_td = self._row(left, 6, "Задержка источника, с", req.timing.tranDelay_s,
                              lambda v: setattr(req.timing, "tranDelay_s", v))
        self.f_ta = self._row(left, 7, "Задержка измерения, с", req.timing.acqDelay_s,
                              lambda v: setattr(req.timing, "acqDelay_s", v))
        self.f_tt = self._row(left, 8, "Фронт, с", req.timing.tranTime_s,
                              lambda v: setattr(req.timing, "tranTime_s", v))
        self.f_n = self._row(left, 9, "Апертур", float(req.timing.count),
                             lambda v: setattr(req.timing, "count", max(1, int(round(v)))))
        self.f_tol = self._row(left, 10, "Допуск, %", req.tolerancePct,
                               lambda v: setattr(req, "tolerancePct", v))
        self.f_noise = self._row(left, 11, "Шум, А", req.noise_A,
                                 lambda v: setattr(req, "noise_A", v))
        self.tau_label = ttk.Label(left, text="", foreground=COLOR_INFO)
        self.tau_label.grid(row=12, column=0, columnspan=2, sticky="w", pady=(4, 0))

        btns = ttk.Frame(left)
        btns.grid(row=13, column=0, columnspan=2, sticky="w", pady=6)
        ttk.Button(btns, text="Выполнить проверку", command=self._run).pack(side="left",
                                                                            padx=(0, 6))
        ttk.Button(btns, text="Пресет «Начало пропущено» (acqDelay = 5·фронт + интервал)",
                   command=self._preset_missed_start).pack(side="left")
        ttk.Label(left, text=(
            "До DUT: опыт на известных C и RC обязателен (§6). Python/VISA-цикл не "
            "гарантирует микросекундную синхронизацию — окна задаёт триггерная модель прибора."),
            foreground=COLOR_INFO, wraplength=420, justify="left").grid(
            row=14, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.res_verdict = ttk.Label(right, text="— проверка не выполнялась —",
                                     foreground=COLOR_INFO, font=("", 10, "bold"))
        self.res_verdict.pack(anchor="w")
        self.res_stats = ttk.Label(right, text="", justify="left", font=FONT_MONO)
        self.res_stats.pack(anchor="w", pady=4)
        self.checks_table = DataTable(right, [
            ("label", "Проверка", 250, "w"),
            ("value", "Значение", 160, "w"),
            ("ok", "Итог", 90, "w"),
        ], max_height=8)
        self.checks_table.frame.pack(fill="x", pady=(4, 0))
        self.ap_table = DataTable(right, [
            ("j", "j", 40, "e"),
            ("a", "a_s, с", 100, "e"),
            ("b", "b_s, с", 100, "e"),
            ("i", "Ī_j, А", 130, "e"),
            ("u", "u_i, А", 120, "e"),
            ("st", "статус", 70, "w"),
        ], max_height=10)
        self.ap_table.frame.pack(fill="x", pady=(6, 0))
        self._refresh_tau()

    def _row(self, parent, row, label, value, setter):
        f = EngField(parent, label, "", width=12)

        def on_change():
            v = f.get()
            if v is not None:
                setter(v)
                self._refresh_tau()

        from tkinter import ttk
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        f.frame.grid(row=row, column=1, sticky="w", pady=2)
        f.set(value)
        f.on_change = on_change
        return f

    def _refresh_tau(self) -> None:
        if not hasattr(self, "tau_label"):
            return
        req = self.state.verification_request
        if req.refType == "rc" and req.rRef_ohm > 0:
            self.tau_label.configure(
                text=f"τ = R·C = {format_seconds(req.rRef_ohm * req.cRef_F)}")
        else:
            self.tau_label.configure(text="τ = 0 (идеальный конденсатор)")

    def _preset_missed_start(self) -> None:
        req = self.state.verification_request
        req.timing.acqDelay_s = 5 * req.timing.tranTime_s + req.timing.interval_s
        self.f_ta.set(req.timing.acqDelay_s)
        self.app.status("Пресет «Начало пропущено» загружен: первый импульс пройдёт "
                        "до первой апертуры (§6)", "warn")

    def _run(self) -> None:
        try:
            res = execute_verification(self.state.verification_request)
        except ValueError as e:
            self.app.status(f"Верификация: {e}", "fail")
            return
        self.state.verification = res
        mark = "ПРОЙДЕНА" if res.pass_ else "НЕ ПРОЙДЕНА"
        self.res_verdict.configure(
            text=f"{mark}: заряд восстановлен с ошибкой {res.errorRelPct:+.3f} % "
                 f"(допуск ±{res.tolerancePct:g} %)",
            foreground=COLOR_OK if res.pass_ else COLOR_FAIL)
        self.res_stats.configure(text=(
            f"Q_ожид = C·ΔV        = {format_coulombs(res.qExpected_C)}\n"
            f"Q_восст = Σ Ī_j·Δt_j = {format_coulombs(res.qRecovered_C)}\n"
            f"истина в окнах       = {format_coulombs(res.qRegisteredTrue_C)}\n"
            f"истина в пропусках   = {format_coulombs(res.qInGaps_C)}"))
        rows = []
        tags = []
        for ch in res.checks:
            rows.append((ch.label, ch.value + (f"  ({ch.note})" if ch.note else ""),
                         "—" if ch.ok is None else ("OK" if ch.ok else "ОШИБКА")))
            tags.append(None if ch.ok is None else ("ok" if ch.ok else "fail"))
        self.checks_table.set_rows(rows, tags)
        self.ap_table.set_rows(
            [(ap.j, f"{ap.a_s:.6g}", f"{ap.b_s:.6g}", format_sci(ap.i_avg_A),
              format_sci(ap.u_i_A), ap.status) for ap in res.apertures])
        self.app.status(f"Верификация {mark} ({res.errorRelPct:+.1f} %)",
                        "ok" if res.pass_ else "fail")

    def refresh(self) -> None:
        if self.state.verification is not None:
            res = self.state.verification
            mark = "ПРОЙДЕНА" if res.pass_ else "НЕ ПРОЙДЕНА"
            self.res_verdict.configure(
                text=f"{mark}: ошибка {res.errorRelPct:+.3f} % (допуск ±{res.tolerancePct:g} %)",
                foreground=COLOR_OK if res.pass_ else COLOR_FAIL)

# ============================== Панели GUI: часть 2 ==============================
# Измерение (§7), Результаты (§8–12), SCPI, Анализ (порт qscv_final),
# Журнал (§13), Справочник (§2–3, §8–11, §14).

Q_E = 1.602e-19   # Кл
M_E = 9.109e-31   # кг


class MeasurementTab:
    """§7: запуск последовательности (симуляция или прибор), прогресс, воспроизведение."""

    def __init__(self, notebook, app):
        import tkinter as tk
        from tkinter import ttk
        self.app = app
        self.state = app.state
        outer = ttk.Frame(notebook, padding=10)
        notebook.add(outer, text=" Измерение (§7) ")
        self.notebook = notebook

        # --- Режим и подключение ---
        mode = ttk.LabelFrame(outer, text="Режим измерения", padding=8)
        mode.pack(fill="x")
        self.mode_var = tk.StringVar(value="sim")
        ttk.Radiobutton(mode, text="Симуляция (офлайн-модель DUT)",
                        value="sim", variable=self.mode_var).pack(side="left", padx=6)
        ttk.Radiobutton(mode, text="Прибор B2910BL (PyVISA, экспериментально)",
                        value="hw", variable=self.mode_var,
                        command=self._mode_changed).pack(side="left", padx=6)
        self.hw_frame = ttk.Frame(mode)
        self.hw_frame.pack(side="left", padx=12)
        self.res_var = tk.StringVar(value="GPIB0::17::INSTR")
        ttk.Label(self.hw_frame, text="VISA-ресурс:").pack(side="left")
        ttk.Entry(self.hw_frame, textvariable=self.res_var, width=24).pack(side="left", padx=4)
        ttk.Button(self.hw_frame, text="Подключить",
                   command=self._connect).pack(side="left", padx=2)
        ttk.Button(self.hw_frame, text="Отключить",
                   command=self._disconnect).pack(side="left", padx=2)
        self.idn_label = ttk.Label(self.hw_frame, text="не подключён", foreground=COLOR_WARN)
        self.idn_label.pack(side="left", padx=6)

        # --- Сводка ---
        summ = ttk.LabelFrame(outer, text="Сводка паспорта программы", padding=8)
        summ.pack(fill="x", pady=6)
        self.summary_label = ttk.Label(summ, text="", justify="left", font=FONT_MONO)
        self.summary_label.pack(anchor="w")

        # --- Управление ---
        run_frame = ttk.Frame(outer)
        run_frame.pack(fill="x", pady=4)
        self.run_button = ttk.Button(run_frame, text="▶ Выполнить последовательность",
                                     command=self._start)
        self.run_button.pack(side="left")
        self.stop_button = ttk.Button(run_frame, text="■ Стоп", command=self._stop,
                                      state="disabled")
        self.stop_button.pack(side="left", padx=6)
        self.progress = ttk.Progressbar(run_frame, length=320, mode="determinate")
        self.progress.pack(side="left", padx=8)
        self.progress_label = ttk.Label(run_frame, text="")
        self.progress_label.pack(side="left")
        ttk.Button(run_frame, text="Открыть результаты →",
                   command=lambda: self.app.select_tab("results")).pack(side="right")

        # --- Живой вид шага ---
        step_card = ttk.LabelFrame(outer, text="Шаг (воспроизведение)", padding=8)
        step_card.pack(fill="both", expand=True, pady=6)
        self.step_info = ttk.Label(step_card, text="— прогон не выполнялся —",
                                   justify="left", font=FONT_MONO)
        self.step_info.pack(anchor="w")
        self.step_plot = PlotCanvas(step_card, x_label="t от триггера, с",
                                    y_label="Ī_j − L_j, А", height=220)
        self.step_plot.frame.pack(fill="both", expand=True, pady=4)
        pb = ttk.Frame(step_card)
        pb.pack(fill="x")
        self.play_button = ttk.Button(pb, text="▶ Проиграть", command=self._toggle_play,
                                      state="disabled")
        self.play_button.pack(side="left")
        self.speed_var = tk.StringVar(value="×4")
        ttk.Combobox(pb, textvariable=self.speed_var, values=["×1", "×4", "×16"],
                     state="readonly", width=5).pack(side="left", padx=6)
        self.slider_var = tk.IntVar(value=0)
        self.slider = ttk.Scale(pb, from_=0, to=1, orient="horizontal",
                                variable=self.slider_var, command=self._on_slide)
        self.slider.pack(side="left", fill="x", expand=True)
        self.slider.configure(state="disabled")

        # --- Останов ---
        self.stop_label = ttk.Label(outer, text="", foreground=COLOR_FAIL,
                                    font=("", 10, "bold"))
        self.stop_label.pack(anchor="w")

        # Служебное.
        self.queue: "queue.Queue" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.all_steps: List[StepResult] = []
        self.playing = False
        self.play_after_id = None

        ttk.Label(outer, text=(
            "§7: до и после развёртки — безопасное состояние (bias 0 В, :OUTP OFF, разряд DUT). "
            "Первый шаг ветви без отдельной подготовки — диагностический (§7.1). "
            "При сработавшем критерии остановки остальные шаги не выполняются (§7.3)."),
            foreground=COLOR_INFO, wraplength=900, justify="left").pack(anchor="w", pady=(6, 0))
        self.refresh()

    # ------------------------------ режим ------------------------------

    def _mode_changed(self) -> None:
        if self.mode_var.get() == "hw" and not self.state.visa.connected:
            self.app.status("Режим прибора: нажмите «Подключить» и проверьте *IDN?", "warn")

    def _connect(self) -> None:
        visa = self.state.visa
        if visa.connected:
            return
        visa.resource = self.res_var.get().strip()
        try:
            idn = visa.connect()
            self.idn_label.configure(text=idn, foreground=COLOR_OK)
            self.app.status(f"Подключено: {idn}", "ok")
        except InstrumentError as e:
            self.idn_label.configure(text="ошибка подключения", foreground=COLOR_FAIL)
            self.app.status(str(e), "fail")

    def _disconnect(self) -> None:
        self.state.visa.close()
        self.idn_label.configure(text="не подключён", foreground=COLOR_WARN)
        self.app.status("Прибор отключён", "info")

    # ------------------------------ запуск ------------------------------

    def refresh(self) -> None:
        cfg = self.state.config
        n_steps = max(1, round(abs(cfg.vEnd_V - cfg.vStart_V) / cfg.vStep_V))
        try:
            aperture = aperture_from_config(cfg)
            ap_text = format_seconds(aperture)
        except Exception:  # noqa: BLE001
            ap_text = "?"
        proto = ("B — зарядовый (апертуры)" if cfg.protocol == "aperture_charge"
                 else "A — линейная развёртка (Кун)")
        self.summary_label.configure(text=(
            f"Протокол: {proto} ·  {cfg.vStart_V:g} → {cfg.vEnd_V:g} В, шаг {cfg.vStep_V:g} В, "
            f"переходов за развёртку: {n_steps}\n"
            f"Направления: {' + '.join(cfg.directions)} · циклов: {cfg.cycles} · "
            f"апертур на шаг: {cfg.trigCount} · интервал {format_seconds(cfg.trigInterval_s)} · "
            f"апертура {ap_text}\n"
            f"Compliance {format_amps(cfg.iCompliance_A)} · диапазон "
            f"{'AUTO' if cfg.iRangeMode == 'auto' else format_amps(cfg.iRange_A)} · "
            f"DUT (симуляция): {self.state.current_dut().label}"))

    def _start(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            return
        cfg = self.state.config
        self.stop_event.clear()
        self.queue = queue.Queue()
        self.run_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress.configure(value=0)
        mode = self.mode_var.get()
        if mode == "hw" and not self.state.visa.connected:
            self.app.status("Сначала подключите прибор (вкладка «Измерение»)", "fail")
            self.run_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            return

        total = (max(1, round(abs(cfg.vEnd_V - cfg.vStart_V) / cfg.vStep_V)) *
                 len(cfg.directions) * cfg.cycles)
        self._total_steps = total
        self._progress_count = 0

        def progress_cb(msg: str) -> bool:
            self.queue.put(("progress", msg))
            return not self.stop_event.is_set()

        def worker() -> None:
            try:
                if mode == "hw":
                    runner = HardwareRunner(self.state.visa, cfg,
                                            self.state.instrument.wiring)
                    runs = runner.run(progress_cb)
                else:
                    runs = execute_measurement(cfg, self.state.current_dut(),
                                               seed=31, progress=progress_cb)
                self.queue.put(("done", runs))
            except Exception as e:  # noqa: BLE001 — ошибка в поток попадает в UI
                self.queue.put(("error", f"{type(e).__name__}: {e}"))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()
        self._poll()
        self.app.status("Измерение запущено (" +
                        ("прибор" if mode == "hw" else "симуляция") + ")", "info")

    def _stop(self) -> None:
        self.stop_event.set()
        self.app.status("Остановка по кнопке «Стоп»…", "warn")

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "progress":
                    done = getattr(self, "_progress_count", 0) + 1
                    self._progress_count = done
                    self.progress.configure(
                        value=100 * min(done, self._total_steps) / max(1, self._total_steps))
                    self.progress_label.configure(text=payload)
                elif kind == "done":
                    self._finish(payload)
                    return
                elif kind == "error":
                    self._finish_error(payload)
                    return
        except queue.Empty:
            pass
        self.app.after(80, self._poll)

    def _finish(self, runs: List[RunResult]) -> None:
        self.run_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.progress.configure(value=100)
        self.state.runs = runs
        self.all_steps = [st for r in runs for st in r.steps]
        self.step_runs = [r for r in runs for _st in r.steps]
        n = len(self.all_steps)
        self.progress_label.configure(text=f"Готово: {n} шагов")
        self._progress_count = 0
        stopped = [r for r in runs if r.stopped]
        if stopped:
            reason = stopped[-1].stopReason or "?"
            self.stop_label.configure(
                text=f"⛔ Останов: {STOP_REASON_RU.get(reason, reason)} ({reason})")
        else:
            self.stop_label.configure(text="")
        # Воспроизведение.
        self.slider.configure(state="normal", from_=0, to=max(0, n - 1))
        self.slider_var.set(0)
        self.play_button.configure(state="normal" if n else "disabled")
        self._show_step(0)
        self.app.results_tab.refresh()
        self.app.status(f"Измерение завершено: {n} шагов, прогонов {len(runs)}", "ok")

    def _finish_error(self, message: str) -> None:
        self.run_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.progress_label.configure(text="ошибка")
        self.app.status(f"Ошибка измерения: {message}", "fail")

    # ------------------------------ воспроизведение ------------------------------

    def _show_step(self, index: int) -> None:
        if not self.all_steps:
            return
        index = max(0, min(index, len(self.all_steps) - 1))
        st = self.all_steps[index]
        aps = st.apertures
        run_cfg = (self.step_runs[index].config if index < len(self.step_runs)
                   else self.state.config)
        xs = [(ap.a_s + ap.b_s) / 2 for ap in aps]
        ys = [ap.i_avg_A - ap.i_background_A for ap in aps]
        self.step_plot.clear()
        self.step_plot.add_series("Ī_j − L_j", xs, ys, color=COLOR_OK, style="bar")
        # Линия подгонки хвоста.
        n_tail = min(run_cfg.tailPoints, len(aps))
        if n_tail >= 2:
            t0 = xs[-n_tail]
            t1 = xs[-1]
            # t̄ по последним n_tail окнам.
            t_mean = sum(xs[-n_tail:]) / n_tail
            y0 = st.tailB_A + st.tailS_A_s * (t0 - t_mean)
            y1 = st.tailB_A + st.tailS_A_s * (t1 - t_mean)
            self.step_plot.add_series("хвост B + S·(t − t̄)", [t0, t1], [y0, y1],
                                      color=COLOR_WARN, dash=(4, 3))
        self.step_plot.render()
        cum_q = 0.0
        for k in range(index):
            cum_q += self.all_steps[k].qObs_C
        self.step_info.configure(text=(
            f"Шаг {index + 1}/{len(self.all_steps)}: {st.vBefore_V:g} В → {st.vAfter_V:g} В "
            f"(ΔV = {st.dv_V:g} В, середина {st.vMid_V:g} В)\n"
            f"ΔQ_obs = {format_coulombs(st.qObs_C)} ± {format_coulombs(st.uQ_C)} · "
            f"C_sec = {format_farads(st.cSec_F)} ± {format_farads(st.uC_F)} · "
            f"хвост {'OK' if st.tailOk else 'НЕ установлен'}\n"
            f"Σ ΔQ по прогонам = {format_coulombs(cum_q)} · "
            f"флаги: {'; '.join(st.flags) if st.flags else 'OK'}"))

    def _on_slide(self, _value) -> None:
        if not self.playing:
            self._show_step(int(self.slider_var.get()))

    def _toggle_play(self) -> None:
        self.playing = not self.playing
        self.play_button.configure(text="⏸ Пауза" if self.playing else "▶ Проиграть")
        if self.playing:
            self._tick_play()

    def _tick_play(self) -> None:
        if not self.playing:
            return
        idx = int(self.slider_var.get()) + 1
        if idx >= len(self.all_steps):
            self.playing = False
            self.play_button.configure(text="▶ Проиграть")
            return
        self.slider_var.set(idx)
        self._show_step(idx)
        speed = {"×1": 600, "×4": 150, "×16": 40}.get(self.speed_var.get(), 150)
        self.play_after_id = self.app.after(speed, self._tick_play)


class ResultsTab:
    """§8–12: C–V кривая, таблица шагов, деталь шага, энергия, неопределённость."""

    def __init__(self, notebook, app):
        from tkinter import ttk
        self.app = app
        self.state = app.state
        outer = ttk.Frame(notebook, padding=10)
        notebook.add(outer, text=" Результаты (§8–12) ")
        self.outer = outer

        top = ttk.Frame(outer)
        top.pack(fill="x")
        import tkinter as tk
        self.run_var = tk.StringVar()
        self.run_cb = ttk.Combobox(top, textvariable=self.run_var, state="readonly",
                                   width=40, values=[])
        self.run_cb.pack(side="left")
        self.run_cb.bind("<<ComboboxSelected>>", lambda e: self._render_run())
        ttk.Button(top, text="Экспорт: шаги CSV",
                   command=self._export_steps).pack(side="left", padx=10)
        self.show_true = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="показывать «истину» модели (симуляция)",
                        variable=self.show_true,
                        command=self._render_run).pack(side="left")
        self.show_unusable = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="показывать непригодные шаги",
                        variable=self.show_unusable,
                        command=self._render_run).pack(side="left")

        self.cv_plot = PlotCanvas(outer, x_label="V середины шага, В",
                                  y_label="C_sec, Ф", height=280,
                                  title="Квазистатическая C–V (секущая ΔQ/ΔV)")
        self.cv_plot.frame.pack(fill="x", pady=6)

        mid = ttk.Frame(outer)
        mid.pack(fill="x")
        table_card = ttk.LabelFrame(mid, text="Шаги", padding=6)
        table_card.pack(side="left", fill="both", expand=True)
        self.steps_table = DataTable(table_card, [
            ("step", "шаг", 40, "e"),
            ("dir", "ход", 70, "w"),
            ("vmid", "V серед.", 80, "e"),
            ("dv", "ΔV, В", 70, "e"),
            ("csec", "C_sec, Ф", 110, "e"),
            ("uc", "u(C), Ф", 100, "e"),
            ("tail", "хвост", 60, "w"),
            ("flags", "флаги", 130, "w"),
        ], max_height=12, on_select=self._on_step_select)
        self.steps_table.frame.pack(fill="x")

        # --- Деталь шага ---
        detail = ttk.LabelFrame(outer, text="Деталь шага (выберите строку таблицы)",
                                padding=8)
        detail.pack(fill="both", expand=True, pady=6)
        dtop = ttk.Frame(detail)
        dtop.pack(fill="x")
        self.ap_plot = PlotCanvas(dtop, x_label="t от триггера, с", y_label="ток, А",
                                  height=200, title="Апертуры шага")
        self.ap_plot.frame.pack(side="left", fill="both", expand=True)
        info = ttk.Frame(dtop)
        info.pack(side="left", fill="y", padx=(8, 0))
        self.detail_label = ttk.Label(info, text="—", justify="left", font=FONT_MONO,
                                      wraplength=420)
        self.detail_label.pack(anchor="nw")
        self.flags_frame = ttk.Frame(info)
        self.flags_frame.pack(anchor="nw", fill="x", pady=(6, 0))

        dbottom = ttk.Frame(detail)
        dbottom.pack(fill="x", pady=(6, 0))
        fit_card = ttk.LabelFrame(dbottom, text="Компоненты подгонки (§9)", padding=6)
        fit_card.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.fit_table = DataTable(fit_card, [
            ("tau", "τ, с", 100, "e"),
            ("a", "A, А", 110, "e"),
            ("q", "A·τ, Кл", 110, "e"),
        ], max_height=5)
        self.fit_table.frame.pack(fill="x")
        self.fit_stats = ttk.Label(fit_card, text="", foreground=COLOR_INFO,
                                   font=FONT_MONO, justify="left")
        self.fit_stats.pack(anchor="w", pady=(4, 0))
        energy_card = ttk.LabelFrame(dbottom, text="Энергия (§10)", padding=6)
        energy_card.pack(side="left", fill="x", expand=True)
        self.energy_label = ttk.Label(energy_card, text="—", justify="left", font=FONT_MONO)
        self.energy_label.pack(anchor="w")
        self.refresh()

    # ------------------------------ рендер ------------------------------

    def _runs(self) -> List[RunResult]:
        return self.state.runs

    def refresh(self) -> None:
        runs = self._runs()
        self.run_cb.configure(values=[f"{r.label} — {len(r.steps)} шагов" for r in runs])
        if runs:
            if not self.run_var.get():
                self.run_var.set(f"{runs[0].label} — {len(runs[0].steps)} шагов")
            self._render_run()
        else:
            self.cv_plot.clear()
            self.steps_table.clear()
            self.detail_label.configure(text="— прогонов нет —")
            self.fit_table.clear()
            self.energy_label.configure(text="—")
            for w in self.flags_frame.winfo_children():
                w.destroy()

    def _selected_run(self) -> Optional[RunResult]:
        runs = self._runs()
        if not runs:
            return None
        sel = self.run_var.get()
        for i, r in enumerate(runs):
            if sel == f"{r.label} — {len(r.steps)} шагов":
                return r
        return runs[0]

    def _render_run(self) -> None:
        run = self._selected_run()
        if run is None:
            return
        self.cv_plot.clear()
        for direction, color in (("forward", COLOR_OK), ("reverse", COLOR_REV)):
            pts = [p for p in run.cvCurve if p.direction == direction and
                   (p.usable or self.show_unusable.get())]
            self.cv_plot.add_series(
                f"{'прямой' if direction == 'forward' else 'обратный'} ход",
                [p.vMid_V for p in pts], [p.cSec_F for p in pts],
                color=color, style="scatter", err=[p.uC_F for p in pts])
        if self.show_true.get():
            tr = [p for p in run.cvCurve if p.cSecTrue_F is not None]
            if tr:
                self.cv_plot.add_series("истина модели",
                                        [p.vMid_V for p in tr],
                                        [p.cSecTrue_F for p in tr],
                                        color=COLOR_TRUE, dash=(5, 4), legend=True)
        self.cv_plot.add_series("", [], [], legend=False)
        self.cv_plot.render()
        rows = []
        tags = []
        for st in run.steps:
            rows.append((st.step, "прямой" if st.direction == "forward" else "обратный",
                         f"{st.vMid_V:.4g}", f"{st.dv_V:.4g}",
                         format_sci(st.cSec_F), format_sci(st.uC_F),
                         "OK" if st.tailOk else "не уст.",
                         ";".join(st.flags) if st.flags else "OK"))
            if not st.usableForPair:
                tags.append("warn" if st.tailOk else "fail")
            else:
                tags.append("ok")
        self.steps_table.set_rows(rows, tags)
        if run.steps:
            self._show_detail(run.steps[0])

    def _on_step_select(self, iid: str) -> None:
        run = self._selected_run()
        if run is None:
            return
        try:
            st = run.steps[int(iid)]
        except (ValueError, IndexError):
            return
        self._show_detail(st)

    def _show_detail(self, st: StepResult) -> None:
        self.ap_plot.clear()
        xs = [(ap.a_s + ap.b_s) / 2 for ap in st.apertures]
        ys = [ap.i_avg_A - ap.i_background_A for ap in st.apertures]
        self.ap_plot.add_series("Ī_j − L_j", xs, ys, color=COLOR_OK, style="bar")
        run_cfg = self._selected_run()
        n_tail = min(run_cfg.config.tailPoints, len(st.apertures)) if run_cfg else 0
        if n_tail >= 2 and st.apertures:
            t_mean = sum(xs[-n_tail:]) / n_tail
            t0, t1 = xs[0], xs[-1]
            self.ap_plot.add_series(
                "хвост B + S·(t−t̄)",
                [t0, t1], [st.tailB_A + st.tailS_A_s * (t0 - t_mean),
                           st.tailB_A + st.tailS_A_s * (t1 - t_mean)],
                color=COLOR_WARN, dash=(4, 3))
        if st.fit is not None and st.apertures:
            fit_ys = []
            for ap in st.apertures:
                y = st.fit.baseline_A
                for comp in st.fit.components:
                    y += comp.a_A * mean_exp(ap.a_s, ap.b_s, comp.tau_s)
                fit_ys.append(y)
            self.ap_plot.add_series("модель exp", xs, fit_ys, color=COLOR_REV,
                                    dash=(2, 2))
        self.ap_plot.render()

        lines = [
            f"шаг {st.step} ({'прямой' if st.direction == 'forward' else 'обратный'}): "
            f"{st.vBefore_V:g} → {st.vAfter_V:g} В",
            f"ΔQ_obs = {format_coulombs(st.qObs_C)} ± {format_coulombs(st.uQ_C)}",
            f"коррекция пропусков (модель) = {format_coulombs(st.qGapModel_C)} "
            f"(граница {format_coulombs(st.qGapBound_C)})",
            f"ΔQ_испр = {format_coulombs(st.qCorrected_C)}",
            f"C_sec = ΔQ/ΔV = {format_farads(st.cSec_F)} ± {format_farads(st.uC_F)}",
            f"хвост: B = {format_amps(st.tailB_A)} ± {format_amps(st.tailUB_A)}, "
            f"S = {format_eng(st.tailS_A_s, 'А/с')} ± {format_eng(st.tailUS_A_s, 'А/с')}",
            f"пригоден для пары: {'да' if st.usableForPair else 'НЕТ'}",
        ]
        if st.kuhn is not None:
            lines.append(f"Кун: |Ī/r| = {format_farads(st.kuhn.cKuhn_F)} "
                         f"(r = {st.kuhn.rampRate_Vps:.4g} В/с)")
        if st.truth is not None:
            t = st.truth
            lines.append(
                f"истина: Q_полный = {format_coulombs(t.qTotal_C)} (быстрая "
                f"{format_coulombs(t.qFast_C)}, ветви {[format_coulombs(q) for q in t.qBranch_C]}), "
                f"в окнах {format_coulombs(t.qRegistered_C)}, в пропусках "
                f"{format_coulombs(t.qInGaps_C)}; утечка {format_coulombs(t.qLeak_C)}")
        self.detail_label.configure(text="\n".join(lines))
        for w in self.flags_frame.winfo_children():
            w.destroy()
        flag_badges(self.flags_frame, st.flags)
        # Подгонка.
        if st.fit is not None:
            self.fit_table.set_rows(
                [(format_seconds(c.tau_s), format_sci(c.a_A), format_sci(c.q_C))
                 for c in st.fit.components])
            self.fit_stats.configure(text=(
                f"модель {st.fit.model} · χ²_red = {st.fit.reducedChi2:.3g} · "
                f"cond = {st.fit.condition:.3g} · успех: {'да' if st.fit.success else 'нет'}\n"
                f"оставшийся хвост Σ A·τ·e^(−b_N/τ) = {format_coulombs(st.fit.remainingTail_C)} "
                "(записан, НЕ прибавляется)"))
        else:
            self.fit_table.clear()
            self.fit_stats.configure(text="подгонка не выполнялась (fitModel = none)")
        # Энергия.
        run = self._selected_run()
        run_txt = ""
        if run is not None:
            run_txt = (f"\nпрогон: W_port = {format_joules(run.wPortTotal_J)}, "
                       f"ΔU = {format_joules(run.dUTotal_J)}, "
                       f"диссипация = {format_joules(run.dissipatedTotal_J)}, "
                       f"петля ∮V dQ = {format_joules(run.loopArea_J)}")
        self.energy_label.configure(text=(
            f"шаг: W_port = {format_joules(st.wPort_J)}, ΔU = {format_joules(st.dU_J)}, "
            f"диссипация = {format_joules(st.dissipated_J)}, "
            f"баланс (остаток) = {format_joules(st.energyResidual_J)}" + run_txt))

    def _export_steps(self) -> None:
        from tkinter import filedialog, messagebox
        if not self.state.runs:
            messagebox.showinfo("Экспорт", "Нет прогонов для экспорта")
            return
        fname = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile="qscv_steps.csv",
            filetypes=[("CSV", "*.csv")])
        if not fname:
            return
        Path(fname).write_text(steps_csv(self.state.runs), encoding="utf-8-sig")
        self.app.status(f"Экспортировано: {fname}", "ok")


class ScpiTab:
    """Генератор SCPI-скрипта (порт ScpiPanel)."""

    def __init__(self, notebook, app):
        import tkinter as tk
        from tkinter import ttk
        self.app = app
        self.state = app.state
        outer = ttk.Frame(notebook, padding=10)
        notebook.add(outer, text=" SCPI ")
        top = ttk.Frame(outer)
        top.pack(fill="x")
        ttk.Label(top, text="Протокол:").pack(side="left")
        self.proto_var = tk.StringVar(value=self._proto_label())
        pcb = ttk.Combobox(top, textvariable=self.proto_var, state="readonly", width=30,
                           values=["B — зарядовый (апертуры)", "A — линейная развёртка (Кун)"])
        pcb.pack(side="left", padx=4)
        ttk.Label(top, text="Направление:").pack(side="left", padx=(10, 0))
        self.dir_var = tk.StringVar(value="forward")
        ttk.Combobox(top, textvariable=self.dir_var, state="readonly", width=14,
                     values=["forward", "reverse"]).pack(side="left", padx=4)
        self.rev_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="добавить обратный ход", variable=self.rev_var).pack(
            side="left", padx=8)
        ttk.Button(top, text="Сгенерировать", command=self._generate).pack(side="left",
                                                                           padx=8)
        ttk.Button(top, text="Копировать", command=self._copy).pack(side="left")
        ttk.Button(top, text="Сохранить .scpi", command=self._save).pack(side="left", padx=4)

        mid = ttk.Frame(outer)
        mid.pack(fill="both", expand=True, pady=6)
        script_card = ttk.LabelFrame(mid, text="Скрипт", padding=4)
        script_card.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.script_text = tk.Text(script_card, wrap="none", font=FONT_MONO, height=28)
        sbar = ttk.Scrollbar(script_card, orient="vertical", command=self.script_text.yview)
        self.script_text.configure(yscrollcommand=sbar.set)
        self.script_text.pack(side="left", fill="both", expand=True)
        sbar.pack(side="right", fill="y")
        cmds_card = ttk.LabelFrame(mid, text="Справочник команд", padding=4)
        cmds_card.pack(side="left", fill="y")
        self.cmd_table = DataTable(cmds_card, [
            ("cmd", "Команда", 260, "w"),
            ("comment", "Назначение", 220, "w"),
        ], max_height=24)
        self.cmd_table.frame.pack(fill="both", expand=True)
        self.notes_label = ttk.Label(outer, text="", foreground=COLOR_WARN,
                                     wraplength=920, justify="left")
        self.notes_label.pack(anchor="w")
        self._generate()

    def _proto_label(self) -> str:
        return ("B — зарядовый (апертуры)"
                if self.state.config.protocol == "aperture_charge"
                else "A — линейная развёртка (Кун)")

    def _generate(self) -> None:
        cfg = self.state.config
        proto = "aperture_charge" if self.proto_var.get().startswith("B") else "kuhn_staircase"
        script, commands, notes = generate_scpi(cfg, protocol=proto,
                                                direction=self.dir_var.get(),
                                                include_reverse=self.rev_var.get())
        self.script_text.delete("1.0", "end")
        self.script_text.insert("1.0", script)
        self.cmd_table.set_rows([(c.cmd, c.comment) for c in commands])
        self.notes_label.configure(text="\n".join(f"⚠ {n}" for n in notes))

    def _copy(self) -> None:
        self.app.copy_to_clipboard(self.script_text.get("1.0", "end"))

    def _save(self) -> None:
        from tkinter import filedialog
        fname = filedialog.asksaveasfilename(defaultextension=".scpi",
                                             initialfile="qscv_b2910bl.scpi",
                                             filetypes=[("SCPI", "*.scpi"), ("Все файлы", "*.*")])
        if fname:
            Path(fname).write_text(self.script_text.get("1.0", "end"), encoding="utf-8")
            self.app.status(f"Скрипт сохранён: {fname}", "ok")

    def refresh(self) -> None:
        self.proto_var.set(self._proto_label())
        self._generate()


class AnalysisTab:
    """Офлайн-анализатор (порт qscv_final): acquisition.csv → шаги, пары, флаги."""

    def __init__(self, notebook, app):
        import tkinter as tk
        from tkinter import ttk
        self.app = app
        self.state = app.state
        outer = ttk.Frame(notebook, padding=10)
        notebook.add(outer, text=" Анализ данных (qscv_final) ")
        self.outer = outer

        # --- Источник данных ---
        src = ttk.LabelFrame(outer, text="Источник acquisition.csv", padding=8)
        src.pack(fill="x")
        self.source_var = tk.StringVar(value="session")
        ttk.Radiobutton(src, text="Прогоны текущей сессии",
                        value="session", variable=self.source_var).pack(side="left", padx=6)
        ttk.Radiobutton(src, text="Файл CSV", value="file",
                        variable=self.source_var).pack(side="left", padx=6)
        ttk.Radiobutton(src, text="Вставить текст", value="paste",
                        variable=self.source_var).pack(side="left", padx=6)
        ttk.Button(src, text="Выбрать файл…", command=self._pick_file).pack(side="left",
                                                                            padx=6)
        self.file_label = ttk.Label(src, text="файл не выбран", foreground=COLOR_INFO)
        self.file_label.pack(side="left", padx=4)
        self.paste_text = tk.Text(outer, height=5, font=FONT_MONO, wrap="none")
        header_lbl = ttk.Label(outer, text="Формат qscv_final: " + HEADER_QSCV_FINAL,
                               foreground=COLOR_TRUE, font=FONT_MONO)
        header_lbl.pack(anchor="w")
        self.paste_text.pack(fill="x")

        # --- Опции ---
        opts = ttk.LabelFrame(outer, text="Опции анализа", padding=8)
        opts.pack(fill="x", pady=6)
        self.opt_model = self._sel(opts, 0, "Модель", ["none", "exp1", "exp2"], "exp2")
        self.opt_gap = self._sel(opts, 1, "Пропуски", ["reject", "bound", "model"], "reject")
        self.opt_bound = self._num(opts, 2, "Граница тока пропусков, А", 1e-11)
        self.opt_tail = self._num(opts, 3, "Окон в хвосте", 10)
        self.opt_ilim = self._num(opts, 4, "I_limit хвоста, А", 1e-12)
        self.opt_slim = self._num(opts, 5, "S_limit, А/с", 1e-10)
        self.opt_k = self._num(opts, 6, "k", 1.96)
        self.opt_ucur = self._num(opts, 7, "u(ток) общий, А", 0.0)
        self.opt_uleak = self._num(opts, 8, "u(фон) общий, А", 0.0)
        self.opt_uclock = self._num(opts, 9, "u(часы) отн.", 0.0)
        self.opt_udv = self._num(opts, 10, "u(ΔV), В", 0.0)
        ttk.Button(opts, text="Анализировать", command=self._analyze).grid(
            row=11, column=0, pady=6)
        ttk.Button(opts, text="Экспорт: шаги CSV", command=self._export_steps).grid(
            row=11, column=1, sticky="w", pady=6)
        ttk.Button(opts, text="Экспорт: пары CSV", command=self._export_pairs).grid(
            row=11, column=2, sticky="w", pady=6)
        self.err_label = ttk.Label(opts, text="", foreground=COLOR_FAIL, wraplength=700,
                                   justify="left")
        self.err_label.grid(row=12, column=0, columnspan=3, sticky="w")

        # --- Результаты ---
        res = ttk.Frame(outer)
        res.pack(fill="both", expand=True)
        steps_card = ttk.LabelFrame(res, text="Шаги", padding=4)
        steps_card.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.steps_table = DataTable(steps_card, [
            ("run", "прогон", 130, "w"),
            ("dir", "ход", 70, "w"),
            ("vmid", "V серед.", 70, "e"),
            ("qobs", "Q_obs, Кл", 100, "e"),
            ("qcorr", "Q_испр, Кл", 100, "e"),
            ("csec", "C_sec, Ф", 100, "e"),
            ("uc", "u(C), Ф", 90, "e"),
            ("tail", "хвост", 60, "w"),
            ("usable", "пара", 50, "w"),
            ("flags", "флаги", 120, "w"),
        ], max_height=14)
        self.steps_table.frame.pack(fill="both", expand=True)
        pairs_card = ttk.LabelFrame(res, text="Пары прямой/обратный ход "
                                              "(интерполяция обратного к vMid прямого)", padding=4)
        pairs_card.pack(side="left", fill="both", expand=True)
        self.pairs_table = DataTable(pairs_card, [
            ("run", "прогон", 130, "w"),
            ("vmid", "V серед.", 70, "e"),
            ("cf", "C_прямой, Ф", 100, "e"),
            ("cr", "C_обратный, Ф", 100, "e"),
            ("dc", "ΔC, Ф", 100, "e"),
        ], max_height=14)
        self.pairs_table.frame.pack(fill="both", expand=True)
        ttk.Label(outer, text=(
            "Постоянная составляющая B подгонки НЕ интегрируется в хвосте до бесконечности; "
            "оставшийся хвост записывается, но не прибавляется. Прогоны с непрошедшими "
            "допусками в пары не включаются."),
            foreground=COLOR_WARN, wraplength=920, justify="left").pack(anchor="w",
                                                                        pady=(6, 0))

    def _sel(self, parent, row, label, values, current):
        import tkinter as tk
        from tkinter import ttk
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=1)
        var = tk.StringVar(value=current)
        cb = ttk.Combobox(parent, textvariable=var, values=values, state="readonly",
                          width=10)
        cb.grid(row=row, column=1, sticky="w")
        return var

    def _num(self, parent, row, label, value):
        from tkinter import ttk
        f = EngField(parent, label, "", width=12)
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=1)
        f.frame.grid(row=row, column=1, sticky="w")
        f.set(value)
        return f

    def _options(self) -> Optional[AnalyzeOptions]:
        vals = {}
        for name, field in (("tailPoints", self.opt_tail), ("tailLimit_A", self.opt_ilim),
                            ("slopeLimit_A_s", self.opt_slim), ("kFactor", self.opt_k),
                            ("gapCurrentBound_A", self.opt_bound),
                            ("uCurrentCommon_A", self.opt_ucur),
                            ("uLeakCommon_A", self.opt_uleak),
                            ("uClockRel", self.opt_uclock), ("uDv_V", self.opt_udv)):
            v = field.get()
            if v is None:
                self.app.status(f"Анализ: заполните поле «{name}»", "warn")
                return None
            vals[name] = v
        return AnalyzeOptions(
            model=self.opt_model.get(), gapMode=self.opt_gap.get(),
            gapCurrentBound_A=vals["gapCurrentBound_A"],
            tailPoints=max(3, int(round(vals["tailPoints"]))),
            tailLimit_A=vals["tailLimit_A"], slopeLimit_A_s=vals["slopeLimit_A_s"],
            kFactor=vals["kFactor"], uCurrentCommon_A=vals["uCurrentCommon_A"],
            uLeakCommon_A=vals["uLeakCommon_A"], uClockRel=vals["uClockRel"],
            uDv_V=vals["uDv_V"])

    def _pick_file(self) -> None:
        from tkinter import filedialog
        fname = filedialog.askopenfilename(filetypes=[("CSV", "*.csv"), ("Все файлы", "*.*")])
        if fname:
            self.state.imported_csv_text = Path(fname).read_text(encoding="utf-8-sig")
            self.file_label.configure(text=fname)

    def _rows_from_source(self) -> Tuple[List[AcquisitionRow], List[str]]:
        mode = self.source_var.get()
        if mode == "session":
            rows: List[AcquisitionRow] = []
            for run in self.state.runs:
                for st in run.steps:
                    for ap in st.apertures:
                        rows.append(AcquisitionRow(
                            run=run.label, direction=st.direction, step=st.step,
                            v_before_V=st.vBefore_V, v_after_V=st.vAfter_V,
                            a_s=ap.a_s, b_s=ap.b_s, i_A=ap.i_avg_A, u_i_A=ap.u_i_A,
                            leak_A=ap.i_background_A, u_leak_ind_A=0.0, u_dt_s=0.0,
                            pre_settled=st.preSettled))
            return rows, []
        if mode == "file":
            text = getattr(self.state, "imported_csv_text", "")
            if not text:
                return [], ["Файл не выбран"]
            return parse_acquisition_csv(text)
        text = self.paste_text.get("1.0", "end")
        return parse_acquisition_csv(text)

    def _analyze(self) -> None:
        options = self._options()
        if options is None:
            return
        rows, errors = self._rows_from_source()
        if errors:
            self.err_label.configure(text="\n".join(errors[:12]))
        else:
            self.err_label.configure(text="")
        if not rows:
            self.app.status("Анализ: нет строк данных", "warn")
            return
        res = analyze_acquisition(rows, options)
        self.state.analysis = res
        tags = []
        table_rows = []
        for r in res.steps:
            table_rows.append((r.run, r.direction, f"{r.vMid_V:.4g}",
                               format_sci(r.qObserved_C), format_sci(r.qCorrected_C),
                               format_sci(r.cSec_F), format_sci(r.uC_F),
                               "OK" if r.tailSettled else "не уст.",
                               "да" if r.usableForPair else "нет",
                               ";".join(r.flags) if r.flags else "OK"))
            tags.append("ok" if r.usableForPair else ("warn" if r.tailSettled else "fail"))
        self.steps_table.set_rows(table_rows, tags)
        self.pairs_table.set_rows(
            [(p.run, f"{p.vMid_V:.4g}", format_sci(p.cForward_F),
              format_sci(p.cReverseInterpolated_F), format_sci(p.deltaC_F))
             for p in res.pairs])
        if res.errors:
            self.err_label.configure(text=(self.err_label.cget("text") + "\n" if
                                           self.err_label.cget("text") else "") +
                                     "\n".join(res.errors[:12]))
        self.app.status(f"Анализ: {len(res.steps)} шагов, {len(res.pairs)} пар", "ok")

    def _export_steps(self) -> None:
        from tkinter import filedialog, messagebox
        if not self.state.analysis:
            messagebox.showinfo("Экспорт", "Сначала выполните анализ")
            return
        fname = filedialog.asksaveasfilename(defaultextension=".csv",
                                             initialfile="analysis_steps.csv",
                                             filetypes=[("CSV", "*.csv")])
        if fname:
            Path(fname).write_text(analysis_steps_csv(self.state.analysis.steps),
                                   encoding="utf-8-sig")
            self.app.status(f"Экспортировано: {fname}", "ok")

    def _export_pairs(self) -> None:
        from tkinter import filedialog, messagebox
        if not self.state.analysis:
            messagebox.showinfo("Экспорт", "Сначала выполните анализ")
            return
        fname = filedialog.asksaveasfilename(defaultextension=".csv",
                                             initialfile="analysis_pairs.csv",
                                             filetypes=[("CSV", "*.csv")])
        if fname:
            Path(fname).write_text(pairs_csv(self.state.analysis.pairs),
                                   encoding="utf-8-sig")
            self.app.status(f"Экспортировано: {fname}", "ok")

    def refresh(self) -> None:
        pass


class JournalTab:
    """§13: журнал CSV (две схемы), метаданные JSON, импорт."""

    def __init__(self, notebook, app):
        import tkinter as tk
        from tkinter import ttk
        self.app = app
        self.state = app.state
        outer = ttk.Frame(notebook, padding=10)
        notebook.add(outer, text=" Журнал (§13) ")
        top = ttk.Frame(outer)
        top.pack(fill="x")
        ttk.Label(top, text="Прогон:").pack(side="left")
        self.run_var = tk.StringVar()
        self.run_cb = ttk.Combobox(top, textvariable=self.run_var, state="readonly",
                                   width=40, values=[])
        self.run_cb.pack(side="left", padx=4)
        self.run_cb.bind("<<ComboboxSelected>>", lambda e: self._preview())
        ttk.Label(top, text="Формат:").pack(side="left", padx=(12, 0))
        self.fmt_var = tk.StringVar(value="methodology")
        ttk.Combobox(top, textvariable=self.fmt_var, state="readonly", width=36,
                     values=["methodology", "qscv_final"]).pack(side="left", padx=4)
        self.fmt_var.trace_add("write", lambda *_: self._preview())
        ttk.Button(top, text="Предпросмотр", command=self._preview).pack(side="left",
                                                                         padx=6)
        ttk.Button(top, text="Сохранить CSV (BOM)", command=self._save_csv).pack(side="left",
                                                                                 padx=4)
        ttk.Button(top, text="Сохранить метаданные JSON",
                   command=self._save_meta).pack(side="left", padx=4)
        ttk.Button(top, text="Импорт acquisition.csv → «Анализ»",
                   command=self._import).pack(side="left", padx=4)

        self.header_label = ttk.Label(outer, text=HEADER_METHODOLOGY, font=FONT_MONO,
                                      foreground=COLOR_TRUE, wraplength=920)
        self.header_label.pack(anchor="w", pady=(6, 2))
        self.preview = LogBox(outer, height=20)
        self.preview.frame.pack(fill="both", expand=True)
        ttk.Label(outer, text=(
            "Одна строка = одна апертура. Настройки (диапазоны, NPLC/апертура, таймеры, "
            "autozero/фильтры, подготовка) сохраняются отдельно (метаданные JSON)."),
            foreground=COLOR_INFO, wraplength=920).pack(anchor="w", pady=(4, 0))
        self.refresh()

    def _selected_run(self) -> Optional[RunResult]:
        runs = self.state.runs
        if not runs:
            return None
        sel = self.run_var.get()
        for r in runs:
            if sel == f"{r.label} — {len(r.steps)} шагов":
                return r
        return runs[0]

    def refresh(self) -> None:
        runs = self.state.runs
        self.run_cb.configure(values=[f"{r.label} — {len(r.steps)} шагов" for r in runs])
        if runs:
            self.run_var.set(f"{runs[0].label} — {len(runs[0].steps)} шагов")
        self._preview()

    def _preview(self) -> None:
        run = self._selected_run()
        self.preview.clear()
        fmt = self.fmt_var.get()
        self.header_label.configure(
            text=HEADER_METHODOLOGY if fmt == "methodology" else HEADER_QSCV_FINAL)
        if run is None:
            self.preview.write("— прогонов нет —", "dim")
            return
        text = journal_csv(run, fmt)
        lines = text.splitlines()
        self.preview.write(lines[0], "dim")
        for line in lines[1:]:
            self.preview.write(line)
        self.preview.write(f"… всего строк данных: {len(lines) - 1}", "dim")

    def _save_csv(self) -> None:
        from tkinter import filedialog, messagebox
        run = self._selected_run()
        if run is None:
            messagebox.showinfo("Журнал", "Нет прогонов")
            return
        fmt = self.fmt_var.get()
        fname = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=f"qscv_journal_{fmt}_{run.id}.csv",
            filetypes=[("CSV", "*.csv")])
        if not fname:
            return
        Path(fname).write_text(journal_csv(run, fmt), encoding="utf-8-sig")
        self.app.status(f"Журнал сохранён: {fname}", "ok")

    def _save_meta(self) -> None:
        from tkinter import filedialog, messagebox
        run = self._selected_run()
        if run is None:
            messagebox.showinfo("Метаданные", "Нет прогонов")
            return
        fname = filedialog.asksaveasfilename(
            defaultextension=".json", initialfile=f"qscv_meta_{run.id}.json",
            filetypes=[("JSON", "*.json")])
        if not fname:
            return
        Path(fname).write_text(journal_metadata(run), encoding="utf-8")
        self.app.status(f"Метаданные сохранены: {fname}", "ok")

    def _import(self) -> None:
        from tkinter import filedialog, messagebox
        fname = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")])
        if not fname:
            return
        text = Path(fname).read_text(encoding="utf-8-sig")
        rows, errors = parse_acquisition_csv(text)
        if errors and not rows:
            messagebox.showerror("Импорт", "\n".join(errors[:8]))
            return
        self.state.imported_csv_text = text
        self.app.analysis_tab.source_var.set("file")
        self.app.analysis_tab.file_label.configure(text=fname + f" ({len(rows)} строк)")
        self.app.status(f"Импортировано {len(rows)} строк — вкладка «Анализ данных»", "ok")
        if errors:
            self.app.status("\n".join(errors[:6]), "warn")
        self.app.select_tab("analysis")

    def refresh_if_needed(self) -> None:
        self.refresh()


class ReferenceTab:
    """§2–3, §8–11, §14: термины, формулы, интерактивные примеры, библиография."""

    BIBLIOGRAPHY = [
        ("F. E. Terman. An investigation of surface states at a silicon/silicon oxide "
         "interface employing metal-oxide-silicon diodes. Solid-State Electronics, 5 (1962).",
         "DOI: 10.1016/0038-1101(62)90111-9"),
        ("C. N. Berglund. Surface states at steam-grown silicon–silicon dioxide interfaces. "
         "IEEE Transactions on Electron Devices, ED-13 (1966) 701–705.",
         "DOI: 10.1109/T-ED.1966.15827"),
        ("M. Kuhn. A quasi-static technique for MOS C–V and surface state measurements. "
         "Solid-State Electronics, 13 (1970) 873–885.",
         "DOI: 10.1016/0038-1101(70)90073-0"),
        ("M. Kuhn, E. H. Nicollian. Fundamental limitations in the use of the quasi-static "
         "technique for MOS C–V measurements. J. Electrochem. Soc., 118 (1971).",
         "DOI: 10.1149/1.2408053"),
        ("J. Schmitz et al. Leakage current correction in quasi-static C–V measurements. "
         "ICMTS 2004.", "конференция ICMTS"),
        ("Keysight. B2900B/BL Series Precision Source/Measure Unit — Programming Guide.",
         "keysight.com"),
        ("Keysight. B2900B/BL Series Precision Source/Measure Unit — SCPI Command Reference.",
         "keysight.com"),
        ("Keysight. B2900B/BL Series Precision Source/Measure Unit — User's Guide.",
         "keysight.com"),
        ("Keysight. Quasi-Static C–V Measurement (Application Note 5992-0436).",
         "keysight.com"),
        ("OpenStax. University Physics Vol. 2, §9.1 (электрический ток, скорость дрейфа).",
         "openstax.org"),
    ]

    def __init__(self, notebook, app):
        from tkinter import ttk
        self.app = app
        outer = ttk.Frame(notebook, padding=10)
        notebook.add(outer, text=" Справочник ")
        canvas = ttk.Frame(outer)
        canvas.pack(fill="both", expand=True)

        # --- §2 ---
        s2 = ttk.LabelFrame(canvas, text="§2. Термины и энергия", padding=8)
        s2.pack(fill="x", pady=3)
        ttk.Label(s2, text="I = dQ/dt [А = Кл/с]     V = dW/dQ [В = Дж/Кл]\n"
                           "P(t) = V·I               W_port = ∫ V·I dt",
                  font=FONT_MONO, justify="left").pack(anchor="w")
        analogy = ttk.Frame(s2)
        analogy.pack(anchor="w", pady=4)
        self._analogy_table = DataTable(analogy, [
            ("el", "Электрическая величина", 200, "w"),
            ("mech", "Механическая аналогия", 240, "w"),
        ], max_height=7)
        self._analogy_table.frame.pack(fill="x")
        self._analogy_table.set_rows([
            ("Заряд Q", "обобщённое перемещение"),
            ("Ток dQ/dt", "скорость"),
            ("Напряжение V", "обобщённая сила"),
            ("Ёмкость dQ/dV", "податливость"),
            ("Обратная ёмкость dV/dQ", "дифференциальная жёсткость"),
            ("V·I", "мощность"),
            ("Интеграл V dQ", "работа"),
        ])
        ttk.Label(s2, text="Аналогия верна как язык обобщённых координат, а не как "
                           "утверждение о буквальной механической пружине. CV²/2 — энергия "
                           "идеальной линейной ёмкости, не кинетическая энергия носителей.",
                  foreground=COLOR_INFO, wraplength=900, justify="left").pack(anchor="w")

        # --- §3 ---
        s3 = ttk.LabelFrame(canvas, text="§3. Теория квазистатической C–V", padding=8)
        s3.pack(fill="x", pady=3)
        ttk.Label(s3, text=(
            "C_qs(V) = dQ_eq/dV — производная равновесного заряда; измерение должно быть "
            "достаточно медленным, чтобы состояние успевало следовать за смещением.\n"
            "Метод Куна: C = I/(dV/dt) — при непрерывном линейном ramp.\n"
            "Ступенчатый sweep: C_sec,k = ΔQ_k/ΔV_DUT,k — секущая на середине интервала; "
            "зависит от длительности окна наблюдения и подготовки."),
                  wraplength=900, justify="left").pack(anchor="w")
        badge(s3, "Ступенчатый sweep SMU требует регистрировать заряд перехода, а не делить "
                  "одиночный ток на ΔV/dwell.", "warn").pack(anchor="w", pady=2)

        # --- §8 интерактивный ---
        s8 = ttk.LabelFrame(canvas, text="§8. Численный пример (интерактивный)", padding=8)
        s8.pack(fill="x", pady=3)
        import tkinter as tk
        s8f = ttk.Frame(s8)
        s8f.pack(anchor="w")
        self.s8_dv = self._ref_num(s8f, "ΔV, В", 0.01)
        self.s8_w = self._ref_num(s8f, "Длительность апертуры w, с", 0.001)
        self.s8_text = tk.Text(s8, height=5, width=44, font=FONT_MONO)
        self.s8_text.pack(anchor="w", pady=4)
        self.s8_text.insert("1.0", "5e-9\n3e-9\n1.5e-9\n0.4e-9\n1e-10")
        s8b = ttk.Frame(s8)
        s8b.pack(anchor="w")
        ttk.Button(s8b, text="Пересчитать", command=self._s8_calc).pack(side="left")
        ttk.Button(s8b, text="Проверить пример §8", command=self._s8_check).pack(
            side="left", padx=6)
        self.s8_result = ttk.Label(s8, text="", font=FONT_MONO, justify="left")
        self.s8_result.pack(anchor="w", pady=2)
        ttk.Label(s8, text=(
            "ΔV=0.01 В. Пять последовательных апертур по 1 мс дали после коррекции токи "
            "5, 3, 1.5, 0.4 и 0.1 нА. Зарегистрированный заряд: 10 пКл. Секущая: 1 нФ.\n"
            "Это полная ёмкость только если отсутствуют существенные пропущенное начало и хвост."),
            foreground=COLOR_INFO, wraplength=900, justify="left").pack(anchor="w")

        # --- §10 интерактивный ---
        s10 = ttk.LabelFrame(canvas, text="§10. Энергия (интерактивный расчёт)", padding=8)
        s10.pack(fill="x", pady=3)
        s10f = ttk.Frame(s10)
        s10f.pack(anchor="w")
        self.s10_c = self._ref_num(s10f, "C, нФ", 1.0)
        self.s10_v1 = self._ref_num(s10f, "V₁, В", 0.0)
        self.s10_v2 = self._ref_num(s10f, "V₂, В", 1.0)
        ttk.Button(s10f, text="Пересчитать", command=self._s10_calc).pack(side="left",
                                                                          padx=6)
        ttk.Button(s10f, text="Пресет: C=1 нФ, V1=0, V2=1 В → 0.5 нДж",
                   command=self._s10_preset).pack(side="left")
        self.s10_result = ttk.Label(s10, text="", font=FONT_MONO)
        self.s10_result.pack(anchor="w", pady=2)
        ttk.Label(s10, text=(
            "W_port = ∫ V·I dt — работа порта за переход.  U = ½·C·V² — энергия идеальной "
            "линейной ёмкости.  ΔU = ∫ V dQ — для нелинейной обратимой Q(V).\n"
            "Баланс: W_port = ΔU + Q_heat,out — при утечке, trapping и гистерезисе работа "
            "порта не равна автоматически изменению запасённой энергии.\n"
            "Произведение отдельных средних V и I в общем случае не равно среднему V·I."),
            foreground=COLOR_INFO, wraplength=900, justify="left").pack(anchor="w")

        # --- §11 интерактивный ---
        s11 = ttk.LabelFrame(canvas, text="§11. Кинетическая энергия носителей "
                                          "(интерактивный расчёт)", padding=8)
        s11.pack(fill="x", pady=3)
        s11f = ttk.Frame(s11)
        s11f.pack(anchor="w")
        self.s11_i = self._ref_num(s11f, "Ток I, А", 1e-9)
        self.s11_n = self._ref_num(s11f, "n, см⁻³", 1e16)
        self.s11_a = self._ref_num(s11f, "A, см²", 1e-4)
        self.s11_l = self._ref_num(s11f, "L, см", 1e-4)
        self.s11_m = self._ref_num(s11f, "m*/m_e", 1.0)
        ttk.Button(s11f, text="Пересчитать", command=self._s11_calc).pack(side="left",
                                                                          padx=6)
        self.s11_result = ttk.Label(s11, text="", font=FONT_MONO, justify="left")
        self.s11_result.pack(anchor="w", pady=2)
        ttk.Label(s11, text="Константы: q = 1.602·10⁻¹⁹ Кл; m_e = 9.109·10⁻³¹ кг. "
                            "Единицы ввода — см/см²/см⁻³, перевод в СИ автоматически.",
                  foreground=COLOR_TRUE).pack(anchor="w")
        badge(s11, "Ограничения модели (§11): B2910BL непосредственно не измеряет скорость "
                   "носителей. Подставлять весь ток SMU в эту формулу нельзя, если он "
                   "включает ток смещения, несколько типов носителей, неоднородные каналы и "
                   "обмен зарядом с ловушками. Ток зарядки MOS не доказывает проводниковый "
                   "перенос через оксид. Модельная энергия направленного движения — не полная "
                   "тепловая энергия, не энергия поля и не V·I.", "fail").pack(
            anchor="w", pady=2)

        # --- §9/§12 ---
        s912 = ttk.LabelFrame(canvas, text="§9. Хвост и §12. Неопределённость", padding=8)
        s912.pack(fill="x", pady=3)
        ttk.Label(s912, text=(
            "Критерий установления хвоста (§9): |B| + k·u(B) ≤ I_lim,  |S| + k·u(S) ≤ S_lim\n"
            "Аппроксимация последних N окон: i = B + S(t − t̄). При k=1.96 нормальная "
            "интерпретация лишь приближённая; это не доказательство исчезновения всех "
            "медленных процессов.\n"
            "Неопределённость заряда (§12): u²(Q_obs) ≈ Σ [w_j²·u²(I_j−L_j) + "
            "(I_j−L_j)²·u²(w_j)],  w_j = b_j − a_j.\n"
            "Малый ток может иметь большой интеграл на длинном времени. Общие offset/gain-"
            "члены не усредняются как независимый шум."),
            wraplength=900, justify="left").pack(anchor="w")

        # --- §14 ---
        s14 = ttk.LabelFrame(canvas, text="§14. Библиография", padding=8)
        s14.pack(fill="x", pady=3)
        for i, (text, ref) in enumerate(self.BIBLIOGRAPHY, 1):
            ttk.Label(s14, text=f"{i}. {text}  [{ref}]", wraplength=900,
                      justify="left", foreground=COLOR_INFO).pack(anchor="w")

        # --- Границы применимости ---
        lim = ttk.LabelFrame(canvas, text="Границы применимости — что модель и анализ "
                                          "НЕ доказывают", padding=8)
        lim.pack(fill="x", pady=3)
        for line in (
            "Пассивная линейная модель — структурная причинность выбранного класса моделей, "
            "не доказательство, что реальный DUT относится к нему.",
            "Fast/slow ≠ автоматически capacitance/traps: медленный ток дают RC тракта, "
            "генерация, абсорбция, контакты, drift.",
            "Для идентификации ловушек нужны эталонные RC, вариации подготовки/температуры "
            "и независимая электростатика.",
        ):
            ttk.Label(lim, text="• " + line, wraplength=900, justify="left").pack(anchor="w")

        self._s8_calc()
        self._s10_calc()
        self._s11_calc()

    def _ref_num(self, parent, label, value):
        from tkinter import ttk
        f = EngField(parent, label, "", width=11)
        box = ttk.Frame(parent)
        box.pack(side="left", padx=6)
        ttk.Label(box, text=label).pack(side="left")
        f.frame.pack(side="left")
        f.set(value)
        return f

    def _s8_calc(self) -> None:
        dv = self.s8_dv.get() or 0.0
        w = self.s8_w.get() or 0.0
        currents = []
        for line in self.s8_text.get("1.0", "end").splitlines():
            try:
                v = float(line.strip().replace(",", "."))
                if math.isfinite(v):
                    currents.append(v)
            except ValueError:
                continue
        q_obs = sum(i * w for i in currents)
        c_sec = q_obs / dv if dv else math.nan
        self._s8_q, self._s8_c = q_obs, c_sec
        self.s8_result.configure(text=(
            f"ΔQ_obs = Σ Ī_j·w_j ({len(currents)} окон) = {format_coulombs(q_obs)}\n"
            f"C_sec = ΔQ/ΔV = {format_farads(c_sec)}"))

    def _s8_check(self) -> None:
        self._s8_calc()
        q_ok = abs(self._s8_q - 10e-12) <= 0.1e-12
        c_ok = abs(self._s8_c - 1e-9) <= 0.01e-9
        if q_ok and c_ok:
            self.app.status("Пример §8: PASS — ΔQ ≈ 10 пКл, C_sec ≈ 1 нФ", "ok")
        else:
            self.app.status("Пример §8: FAIL — значения не соответствуют примеру", "fail")

    def _s10_calc(self) -> None:
        c = (self.s10_c.get() or 0.0) * 1e-9
        v1 = self.s10_v1.get() or 0.0
        v2 = self.s10_v2.get() or 0.0
        du = 0.5 * c * (v2 * v2 - v1 * v1)
        self.s10_result.configure(text=f"ΔU = ½·C·(V₂² − V₁²) = {format_joules(du)}")

    def _s10_preset(self) -> None:
        self.s10_c.set(1.0)
        self.s10_v1.set(0.0)
        self.s10_v2.set(1.0)
        self._s10_calc()

    def _s11_calc(self) -> None:
        i_a = self.s11_i.get() or 0.0
        n_cm3 = self.s11_n.get() or 0.0
        a_cm2 = self.s11_a.get() or 0.0
        l_cm = self.s11_l.get() or 0.0
        m_star = self.s11_m.get() or 1.0
        n_m3 = n_cm3 * 1e6
        a_m2 = a_cm2 * 1e-4
        m_kg = m_star * M_E
        vd = i_a / (n_m3 * Q_E * a_m2) if n_m3 > 0 and a_m2 > 0 else math.nan
        e_drift = 0.5 * m_kg * vd * vd
        n_carriers = n_cm3 * a_cm2 * l_cm
        e_total = 0.5 * n_carriers * m_kg * vd * vd
        self.s11_result.configure(text=(
            f"v_d = I/(n·q·A) = {format_eng(vd, 'м/с')}\n"
            f"E_drift = ½·m*·v_d² = {format_joules(e_drift)} ({format_eng(e_drift / Q_E, 'эВ')})\n"
            f"число носителей n·A·L = {n_carriers:.3e}\n"
            f"E_total = ½·n·A·L·m*·v_d² = {format_joules(e_total)}"))

    def refresh(self) -> None:
        pass

# ============================== Приложение и точка входа ==============================

CONFIG_FILENAME = "qscv_b2910bl_config.json"


class AppState:
    """Общее состояние: паспорта, конфигурация, результаты, подключение VISA."""

    def __init__(self, config_path: Optional[Path] = None):
        self.instrument = InstrumentPassport()
        self.config = MeasurementConfig()
        self.dut_label = "demo_qscv"
        self.runs: List[RunResult] = []
        self.verification: Optional[VerificationResult] = None
        self.analysis: Optional[AnalyzeResult] = None
        self.verification_request = VerificationRequest()
        self.imported_csv_text = ""
        self.visa = VisaInstrument("GPIB0::17::INSTR")
        self.config_path = config_path or (Path(__file__).parent / CONFIG_FILENAME)

    def current_dut(self) -> DutModel:
        return DUT_PRESETS.get(self.dut_label, DUT_PRESETS["demo_qscv"])

    # ------------------------------ сохранение/загрузка ------------------------------

    def save(self) -> None:
        payload = {
            "version": APP_VERSION,
            "instrument": self.instrument.__dict__,
            "config": _config_to_dict(self.config),
            "dut_label": self.dut_label,
            "verification_request": {
                "refType": self.verification_request.refType,
                "cRef_F": self.verification_request.cRef_F,
                "rRef_ohm": self.verification_request.rRef_ohm,
                "dv_V": self.verification_request.dv_V,
                "tolerancePct": self.verification_request.tolerancePct,
                "noise_A": self.verification_request.noise_A,
                "timing": self.verification_request.timing.__dict__,
            },
        }
        try:
            self.config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
        except OSError:
            pass

    def load(self) -> bool:
        if not self.config_path.exists():
            return False
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        try:
            instr = data.get("instrument", {})
            for k, v in instr.items():
                if hasattr(self.instrument, k):
                    setattr(self.instrument, k, v)
            cfg = MeasurementConfig()
            for k, v in data.get("config", {}).items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
            self.config = cfg
            if data.get("dut_label") in DUT_PRESETS:
                self.dut_label = data["dut_label"]
            vr = data.get("verification_request", {})
            if vr:
                self.verification_request.refType = vr.get("refType", "rc")
                self.verification_request.cRef_F = float(vr.get("cRef_F", 1e-9))
                self.verification_request.rRef_ohm = float(vr.get("rRef_ohm", 1e6))
                self.verification_request.dv_V = float(vr.get("dv_V", 0.02))
                self.verification_request.tolerancePct = float(vr.get("tolerancePct", 2))
                self.verification_request.noise_A = float(vr.get("noise_A", 2e-13))
                t = vr.get("timing", {})
                for k in ("interval_s", "aperture_s", "tranDelay_s", "acqDelay_s",
                          "tranTime_s", "count"):
                    if k in t:
                        setattr(self.verification_request.timing, k, t[k])
            return True
        except (TypeError, ValueError, KeyError):
            return False


class App:
    """Главное окно: Notebook с вкладками, строка состояния, меню."""

    TAB_ORDER = ["equipment", "passport", "ff", "verification", "measurement",
                 "results", "scpi", "analysis", "journal", "reference"]

    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk, messagebox
        self.root = root
        self.state = AppState()
        self.state.load()
        root.title(APP_TITLE)
        root.geometry("1180x780")

        # Меню.
        menubar = tk.Menu(root)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="Сохранить конфигурацию", command=self._save_config)
        filemenu.add_command(label="Сбросить к заводским значениям",
                             command=self._reset_config)
        filemenu.add_separator()
        filemenu.add_command(label="Выход", command=root.destroy)
        menubar.add_cascade(label="Файл", menu=filemenu)
        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="О программе", command=self._about)
        menubar.add_cascade(label="Справка", menu=helpmenu)
        root.config(menu=menubar)

        # Строка состояния.
        self.status_var = tk.StringVar(value="Готово. Режим по умолчанию — симуляция.")
        self.status_label = ttk.Label(root, textvariable=self.status_var,
                                      relief="sunken", anchor="w", padding=4)
        self.status_label.pack(side="bottom", fill="x")

        # Вкладки.
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True)
        self.tabs: Dict[str, object] = {}
        self.equipment_tab = EquipmentTab(self.notebook, self)
        self.passport_tab = PassportTab(self.notebook, self)
        self.ff_tab = FfTab(self.notebook, self)
        self.verification_tab = VerificationTab(self.notebook, self)
        self.measurement_tab = MeasurementTab(self.notebook, self)
        self.results_tab = ResultsTab(self.notebook, self)
        self.scpi_tab = ScpiTab(self.notebook, self)
        self.analysis_tab = AnalysisTab(self.notebook, self)
        self.journal_tab = JournalTab(self.notebook, self)
        self.reference_tab = ReferenceTab(self.notebook, self)
        self.tabs = {
            "equipment": self.equipment_tab, "passport": self.passport_tab,
            "ff": self.ff_tab, "verification": self.verification_tab,
            "measurement": self.measurement_tab, "results": self.results_tab,
            "scpi": self.scpi_tab, "analysis": self.analysis_tab,
            "journal": self.journal_tab, "reference": self.reference_tab,
        }
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------ сервис ------------------------------

    def status(self, message: str, kind: str = "info") -> None:
        self.status_var.set(message)
        color = {"ok": COLOR_OK, "warn": COLOR_WARN, "fail": COLOR_FAIL,
                 "info": "#18181B"}.get(kind, "#18181B")
        try:
            self.status_label.configure(foreground=color)
        except Exception:  # noqa: BLE001
            pass

    def copy_to_clipboard(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status("Скопировано в буфер обмена", "ok")

    def select_tab(self, key: str) -> None:
        """Выбор вкладки по ключу — вкладки добавлены в порядке TAB_ORDER."""
        try:
            self.notebook.select(self.TAB_ORDER.index(key))
        except ValueError:
            pass

    def _tab_changed(self, _event) -> None:
        try:
            idx = self.notebook.index(self.notebook.select())
            key = self.TAB_ORDER[idx]
            tab = self.tabs.get(key)
            if tab is not None:
                tab.refresh()
        except Exception:  # noqa: BLE001
            pass

    def _save_config(self) -> None:
        self.state.save()
        self.status(f"Конфигурация сохранена: {self.state.config_path}", "ok")

    def _reset_config(self) -> None:
        self.state.config = MeasurementConfig()
        self.state.dut_label = "demo_qscv"
        self.passport_tab._reload("Конфигурация сброшена к значениям по умолчанию")

    def _about(self) -> None:
        from tkinter import messagebox
        messagebox.showinfo("О программе", (
            f"{APP_TITLE}\nВерсия {APP_VERSION}\n\n"
            "Автономный порт веб-приложения QSCV B2910BL с расширением диапазона "
            "ёмкостей до фемтофарад.\n\n"
            "Основания: методика квазистатических измерений на Keysight B2910BL; "
            "physical_observer.py (модель DUT); qscv_final.py (офлайн-анализатор); "
            "B2900B/BL Programming Guide и User's Guide (сверенные SCPI-команды "
            "и диапазоны; табл. 2-1/2-7: 10 нА — 10 фА, ±210 В).\n\n"
            "SCPI-команды сверены с официальными документами; autozero и измерительные "
            "фильтры — по SCPI Command Reference. Параметры шумовой модели — входные "
            "данные, не паспортные значения."))

    def _on_close(self) -> None:
        self.state.save()
        if self.state.visa.connected:
            self.state.visa.close()
        self.root.destroy()

    def after(self, ms: int, func) -> None:
        self.root.after(ms, func)


def run_gui() -> int:
    import tkinter as tk
    root = tk.Tk()
    _app = App(root)
    root.mainloop()
    return 0


# ============================== CLI: офлайн-демонстрация ==============================

def run_demo(output: Path, dut_label: str = "demo_qscv",
             cfg: Optional[MeasurementConfig] = None) -> int:
    """Полный офлайн-прогон: измерение → верификация → журнал/экспорт/SCPI.

    Проверяет весь конвейер без GUI (для тестирования и пакетной обработки)."""
    output.mkdir(parents=True, exist_ok=True)
    state = AppState(output / "demo_config.json")
    if cfg is None:
        cfg = state.config
    dut = DUT_PRESETS.get(dut_label, DUT_PRESETS["demo_qscv"])

    print(f"QSCV B2910BL — офлайн-прогон (симуляция), DUT: {dut.label}")
    print(f"Протокол: {cfg.protocol}, {cfg.vStart_V:g}…{cfg.vEnd_V:g} В, шаг {cfg.vStep_V:g} В, "
          f"направления {'+'.join(cfg.directions)}, циклов {cfg.cycles}")
    try:
        aperture = aperture_from_config(cfg)
        print(f"Апертура: {format_seconds(aperture)}, интервал: "
              f"{format_seconds(cfg.trigInterval_s)}, окон на шаг: {cfg.trigCount}")
    except ValueError as e:
        print(f"ОШИБКА конфигурации: {e}")
        return 1

    # --- Верификация §6 (эталон RC по умолчанию) ---
    ver = execute_verification(state.verification_request)
    print(f"Верификация §6 ({state.verification_request.refType.upper()}, "
          f"{format_farads(state.verification_request.cRef_F)}): "
          f"{'PASS' if ver.pass_ else 'FAIL'}, ошибка {ver.errorRelPct:+.3f} %")

    # --- Измерение ---
    t0 = time.time()
    runs = execute_measurement(cfg, dut, seed=31)
    dt = time.time() - t0
    n_steps = sum(len(r.steps) for r in runs)
    print(f"Измерение: {len(runs)} прогонов, {n_steps} шагов за {dt:.2f} с")

    # --- Вывод CSV/JSON/SCPI ---
    for run in runs:
        safe = run.label.replace(", ", "_").replace(" ", "_")
        (output / f"journal_methodology_{safe}.csv").write_text(
            journal_csv(run, "methodology"), encoding="utf-8")
        (output / f"journal_qscv_final_{safe}.csv").write_text(
            journal_csv(run, "qscv_final"), encoding="utf-8")
        (output / f"metadata_{run.id}.json").write_text(journal_metadata(run),
                                                        encoding="utf-8")
    (output / "steps.csv").write_text(steps_csv(runs), encoding="utf-8")
    script, _cmds, _notes = generate_scpi(cfg, protocol=cfg.protocol,
                                          direction="forward", include_reverse=True)
    (output / "qscv_b2910bl.scpi").write_text(script, encoding="utf-8")

    # --- Офлайн-анализ (кругозам: журнал → acquisition → шаги/пары) ---
    rows: List[AcquisitionRow] = []
    for run in runs:
        for st in run.steps:
            for ap in st.apertures:
                rows.append(AcquisitionRow(
                    run=run.label, direction=st.direction, step=st.step,
                    v_before_V=st.vBefore_V, v_after_V=st.vAfter_V,
                    a_s=ap.a_s, b_s=ap.b_s, i_A=ap.i_avg_A, u_i_A=ap.u_i_A,
                    leak_A=ap.i_background_A, u_leak_ind_A=0.0, u_dt_s=0.0,
                    pre_settled=st.preSettled))
    analysis = analyze_acquisition(rows, AnalyzeOptions(
        model=cfg.fitModel, gapMode=cfg.gapMode, gapCurrentBound_A=cfg.gapCurrentBound_A,
        tailPoints=min(cfg.tailPoints, cfg.trigCount), tailLimit_A=cfg.tailLimit_A,
        slopeLimit_A_s=cfg.slopeLimit_A_s, kFactor=cfg.kFactor,
        uCurrentCommon_A=cfg.uCurrentCommon_A, uLeakCommon_A=cfg.uLeakCommon_A,
        uClockRel=cfg.uClockRel, uDv_V=cfg.uDv_V))
    (output / "analysis_steps.csv").write_text(analysis_steps_csv(analysis.steps),
                                               encoding="utf-8")
    (output / "analysis_pairs.csv").write_text(pairs_csv(analysis.pairs),
                                               encoding="utf-8")

    # --- Сводка ---
    print("\nСводка по шагам (C_sec на серединах интервалов):")
    for run in runs:
        usable = sum(1 for s in run.steps if s.usableForPair)
        stopped = f" ⛔ {STOP_REASON_RU.get(run.stopReason or '', run.stopReason)}" if run.stopped else ""
        print(f"  {run.label}: {len(run.steps)} шагов, пригодно {usable}{stopped}")
        for st in run.steps[:4]:
            truth = f", истина {format_farads(st.truth.cSecTrue_F)}" if st.truth else ""
            print(f"    шаг {st.step}: {st.vBefore_V:g}→{st.vAfter_V:g} В, "
                  f"C_sec = {format_farads(st.cSec_F)} ± {format_farads(st.uC_F)}{truth}, "
                  f"флаги: {';'.join(st.flags) if st.flags else 'OK'}")
        if len(run.steps) > 4:
            print(f"    … ещё {len(run.steps) - 4} шагов")
    print(f"\nПары прямой/обратный: {len(analysis.pairs)}")
    if analysis.errors:
        print("Замечания анализа:")
        for e in analysis.errors[:10]:
            print(f"  ⚠ {e}")

    # --- фФ-консультант ---
    print("\nфФ-консультант (примеры):")
    for c, dv in ((100e-15, 1.0), (10e-15, 5.0), (1e-15, 20.0), (1e-12, 0.05)):
        res = ff_evaluate(FFInput(c_F=c, dv_V=dv, t_step_s=2.7, aperture_s=0.2,
                                  duty=0.9, u_i_A=10e-15, target_snr=10))
        print(f"  C = {format_farads(c):>9}, ΔV = {dv:>4g} В: SNR = {res.snr:6.2f} — "
              f"{res.verdict}")

    print(f"\nФайлы записаны в: {output.resolve()}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qscv_b2910bl",
        description="QSCV B2910BL — квазистатическая C-V (GUI / офлайн-демо). "
                    "Автономный скрипт: стандартная библиотека Python; pyvisa — "
                    "опционально для реального прибора.")
    parser.add_argument("--demo", metavar="DIR", nargs="?", const="qscv_demo",
                        help="офлайн-прогон без GUI: записать CSV/JSON/SCPI в DIR")
    parser.add_argument("--dut", default="demo_qscv",
                        choices=list(DUT_PRESETS.keys()),
                        help="модель DUT для --demo (по умолчанию demo_qscv)")
    parser.add_argument("--preset", default=None,
                        choices=[k for k, *_ in FF_CONFIG_PRESETS],
                        help="фемтофарадный пресет для --demo")
    parser.add_argument("--no-gui", action="store_true",
                        help="не запускать GUI (совместно с --demo)")
    args = parser.parse_args(argv)

    if args.demo is not None:
        cfg = None
        dut = args.dut
        if args.preset:
            for key, _title, _sub, preset_cfg in FF_CONFIG_PRESETS:
                if key == args.preset:
                    cfg = preset_cfg
                    dut = FF_DUT_OF_PRESET.get(key, dut)
                    break
        return run_demo(Path(args.demo), dut_label=dut, cfg=cfg)
    if args.no_gui:
        parser.print_help()
        return 0
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
