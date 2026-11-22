#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E712_GUI.py
===========
Управление измерителем иммитанса Е7-12 по КОП (ГОСТ 26.003-80 / IEEE-488)
через адаптер UsbGpib (xyphro, github.com/xyphro/UsbGpib), USBTMC / NI-VISA.

МОДЕЛЬ ПРИБОРА: КАЛЬКУЛЯТОР С ДВУМЯ ВЫЧИСЛИТЕЛЯМИ
-------------------------------------------------
Е7-12, как и его архитектурный прототип HP 4271A (1974), не содержит
процессора. Прибор — связка двух вычислителей:
  аналоговый  — четырёхпарный мост Cutkosky: ОУТ и ОУН удерживают объект
                встречными опорными напряжениями «а»/«б», логометр выделяет
                отношение Vx/V0 синхронным детектированием;
  цифровой    — жёсткая программа ПЗУ генератора тактов: 32 такта цикла,
                двухтактное интегрирование выполняет ДЕЛЕНИЕ длительностями,
                счётчик переводит длительность в код.
Следствия для драйвера:
  - байт данных = нажатие кнопки: старший ниббл выбирает регистр (ROM МС9);
    регистры write-only, состояние прибора нечитаемо, истина о состоянии —
    теневая копия отправленного;
  - ОЗУ Мс15/Мс16 = табло: чтение возвращает содержимое, включая прошлый
    отсчёт; свежесть обеспечивают только ЗАП и знание времени цикла;
  - интерфейсные команды табл. 6 — многолинейные сообщения IEEE-488 с линией
    УП (ATN): ЗАП = GET, СБУ = DCL, опрос = SPE/SPD; передаются только
    VISA-операциями, как данные — никогда;
  - синхронизация — сигналами шины (СД/ГП/ДП, КП = EOI); time.sleep в обмене
    отсутствует, таймаут VISA — граница ошибки, не средство ожидания.

ЧИСЛА НЕ СЛУЧАЙНЫ
-----------------
  - Кварц 1 МГц: период 1 мкс, счёт периодов даёт время в микросекундах
    целыми числами.
  - Такт 16 длится 15915 периодов = 10^7/(2 пи) мкс: деление на омегу = 2 пи f
    выполнено длительностью интегрирования — геометрия зашита во время,
    аппаратного умножителя нет.
  - HP 4271A: заряд интегратора 10000 счётов = 20 мс, OFFSET1 = 30 мс.
    Е7-12: такт 11 (подстройка нуля) = 30000 мкс, такт 13 (фаза) = 30000 мкс.
  - Пульт смещения 2.390.175 и HP 16023A: три галетника 0..39.9, BCD 8-4-2-1,
    одинаковый диапазон 39.9 В / 39.9 мА.
  - Табло: знак, 5 цифр, приставка, единица — формат общий.
Сходство не внешнее, а расчётное: обе машины считают одну формулу одними и
теми же длительностями.

УЯЗВИМОСТЬ АРХИТЕКТУРЫ: СМЕЩЕНИЕ
--------------------------------
Документирована ещё HP (Operating Manual 4271A, п. 3-56..3-57):
  "USE EXTREME CAUTION WHEN WORKING WITH CHARGED CAPACITORS, FLOATING OR
   UNGROUNDED CIRCUITRY";
  "The bias current superposed in an inductor being measured ... must not
   flow into the instrument";
  "DC BIAS VTG SWITCH ON REAR PANEL MUST BE IN OFF POSITION".
Класс отказа общий для архитектуры: источник смещения включён в измерительный
тракт постоянно, вид смещения (U/I) выбирается вслепую, прибор объект не
проверяет. Ошибочная установка — U-смещение в низкоомный объект, I-смещение
в разомкнутый DC-путь, включение при заземлённой линии — гонит ток через
тракт и цепи возврата (в Е7-12: Пр9 0.25 А и звезда экранов I, U, I', U').
Меры этого файла:
  - белый список байтов табл. 5: посторонний байт не пишет чужой регистр;
  - смещение 0 при подключении, отключении и в finally любой развёртки;
  - bias guard: предизмерение без смещения, допуск по закону Ома с учётом
    того, что измерение — 1 МГц, а смещение — постоянный ток ([BIAS_GUARD]);
  - scan guard: авто-стоп C-V по перегибу D (предвестник лавины варикапа),
    L-I по спаду L (насыщение) и росту R (потери, нагрев) ([SCAN_GUARD]).
Не покрывается: замыкание самой линии смещения на корпус — линия за фильтром
Др1, Др2, С32 генератора, тракту 1 МГц не видна; её защищает только плавкая
вставка.

СОСТАВ
------
Протокол табл. 5/6 и декодер табло; VISA-драйвер; контроллер; GUI (вкладки:
однократное, C-V, L-I, трёхполюсник, подготовка, сортировка, журнал); сырой
CSV сессии (меню Файл). Вывод прибора: ASCII [+/-]N.NNNN E [+/-]NN[единица],
11 байт, конец = КП (EOI). История изменений — README.

Зависимости:
    pip install pyvisa pyvisa-py pyusb pyserial
    pip install matplotlib pandas   # опционально для графиков
    # + NI-VISA (Windows: https://www.ni.com/visa/)

Запуск:
    python E712_GUI.py
"""

from __future__ import annotations

import argparse
import configparser
import csv
import logging
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional, Union

# pyvisa — опциональный импорт (для возможности запуска GUI без железа)
try:
    import pyvisa
except ImportError:
    pyvisa = None

# matplotlib/pandas — опционально для графиков
PLOT_AVAILABLE = False
try:
    import matplotlib
    matplotlib.use('TkAgg')
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    import pandas as pd
    PLOT_AVAILABLE = True
except ImportError:
    pass


# =============================================================================
# КОНСТАНТЫ
# =============================================================================

DEFAULT_GPIB_ADDR = 1
MAX_AVERAGES = 100
CONFIG_FILE = "e712_config.ini"

__version__ = '1.5.0'
__date__ = '2026-07-29'


# =============================================================================
# ПРОТОКОЛ Е7-12 (из e712_protocol.py + коды смещения из Таблицы 5)
# =============================================================================

# --- Коды режимов (Таблица 5, Приложение A) ---
E712_MODE_CODES = {
    'CG': 0x03,   # Ёмкость, параллельная схема (C‖G)
    'CD': 0x01,   # Проводимость, параллельная схема (C-D)
    'LR': 0x02,   # Индуктивность, последовательная схема (L-R)
    'LD': 0x00,   # Сопротивление, параллельная схема (L‖D)
}

E712_LEVEL_CODES = {
    'x1':   0x14,   # Высокий уровень (1.0 В)
    'x0.1': 0x10,   # Низкий уровень (0.1 В)
}

E712_RANGE_CODES = {    # пределы измерения, группа 0x2_ (табл. 5, стр. 46-47)
    'AUTO': 0x20,
    1:      0x2E,
    2:      0x2D,
    3:      0x2C,
    4:      0x2B,
    5:      0x2A,
}

# --- Коды смещения (Таблица 5, группа 0x4_) ---
# Прежние 0x24/0x20 были ошибкой: 0x20 = ПРЕДЕЛ АВТ, 0x24 не определён.
E712_BIAS_CODES = {
    'V':  0x48,   # Установка смещения U (напряжение)
    'mA': 0x40,   # Установка смещения I (ток)
}

# --- Коды разрядов значения смещения (табл. 5; подтверждено e712_kop.py) ---
# Формат значения Д Е.д: десятки 0..3, единицы 0..9, десятые 0..9 (0.0..39.9,
# шаг 0.1 — как у HP 16023A DC Bias Controller для 4271A).
# Порядок передачи: вид (U/I), 3-й, 2-й, 1-й разряд — одним пакетом.
BIAS_DIGIT1_BASE = 0x50    # 1-й разряд (десятые), 0x50+d
BIAS_DIGIT2_BASE = 0x60    # 2-й разряд (единицы), 0x60+d
BIAS_DIGIT3_BASE = 0x70    # 3-й разряд (десятки), 0x70+d, d=0..3
BIAS_ENABLE_VALUE_CMD = True   # отправлять ли разряды значения смещения

E712_BIAS_DIGIT1 = {d: BIAS_DIGIT1_BASE + d for d in range(10)}
E712_BIAS_DIGIT2 = {d: BIAS_DIGIT2_BASE + d for d in range(10)}
E712_BIAS_DIGIT3 = {d: BIAS_DIGIT3_BASE + d for d in range(4)}

# Экспериментальная передача байтов вне белого списка табл. 5 — только осознанно:
#   [CODES] allow_experimental = 1
ALLOW_EXPERIMENTAL_TX = False

# --- Предохранитель смещения (bias guard) ---
BIAS_GUARD_ENABLE = True
BIAS_GUARD_I_LIMIT_MA = 39.9     # аппаратный предел канала тока (= HP 16023A)
BIAS_GUARD_MARGIN = 0.5          # доля предела, разрешённая допуском
BIAS_GUARD_L_FACTOR = 5.0        # запас на R_DC < R(1 МГц) для LR/LD
BIAS_GUARD_U_COMPL = 39.9        # потолок напряжения источника тока, В

# --- Авто-стоп развёрток по предвестникам отказа ---
SCAN_GUARD_ENABLE = True
SCAN_GUARD_D_RATIO = 3.0     # C-V: стоп при D > D_min * ratio (перегиб D)
SCAN_GUARD_L_SAT_DROP = 0.30 # L-I: стоп при спаде L на эту долю от L0
SCAN_GUARD_R_RATIO = 2.0     # L-I: стоп при росте R в это число раз от R0


def apply_config_codes(config):
    """Применяет коды команд из INI-файла (секция [CODES]).

    Вызывается после load_config(). Переопределяет глобальные словари кодов,
    если соответствующие ключи найдены в INI.

    Поддерживаемые ключи в [CODES]:
      mode_CG, mode_CD, mode_LR, mode_LD       — коды режимов
      level_x1, level_x01                       — коды уровня сигнала
      range_auto, range_1..range_5              — коды пределов
      bias_V, bias_mA                           — коды типа смещения
      output_PC, output_AC                      — коды вывода
      bias_digit1_base, bias_digit2_base, bias_digit3_base
      bias_enable_value_cmd                     — разряды смещения
      allow_experimental                        — разрешить байты вне табл. 5
    Интерфейсные команды (табл. 6) из INI НЕ переопределяются: их коды
    фиксированы ГОСТ 26.003-80 и передаются VISA-операциями с ATN.
    """
    global E712_MODE_CODES, E712_LEVEL_CODES, E712_RANGE_CODES
    global E712_BIAS_CODES, E712_OUTPUT_CODES, ALLOW_EXPERIMENTAL_TX
    global E712_BIAS_DIGIT1, E712_BIAS_DIGIT2, E712_BIAS_DIGIT3
    global BIAS_ENABLE_VALUE_CMD

    if 'CODES' not in config:
        return

    codes = config['CODES']

    def get_code(key, default):
        """Читает код из INI, обрезая inline-комментарии (# ...)."""
        raw = codes.get(key, '')
        # Убираем inline-комментарии: всё после # — комментарий
        val = raw.split('#')[0].strip()
        if not val:
            return default
        try:
            if val.lower().startswith('0x'):
                return int(val, 16)
            return int(val)
        except ValueError:
            logging.warning(f'Неверный код в INI: {key}={val!r} (raw={raw!r}), '
                          f'использую default={default:#x}')
            return default

    # Коды режимов
    E712_MODE_CODES = {
        'CG': get_code('mode_CG', 0x03),
        'CD': get_code('mode_CD', 0x01),
        'LR': get_code('mode_LR', 0x02),
        'LD': get_code('mode_LD', 0x00),
    }

    # Коды уровня
    E712_LEVEL_CODES = {
        'x1':   get_code('level_x1', 0x14),
        'x0.1': get_code('level_x01', 0x10),
    }

    # Коды пределов
    E712_RANGE_CODES = {
        'AUTO': get_code('range_auto', 0x20),
        1:      get_code('range_1', 0x2E),
        2:      get_code('range_2', 0x2D),
        3:      get_code('range_3', 0x2C),
        4:      get_code('range_4', 0x2B),
        5:      get_code('range_5', 0x2A),
    }

    # Коды смещения (тип) — группа 0x4_ табл. 5
    E712_BIAS_CODES = {
        'V':  get_code('bias_V', 0x48),
        'mA': get_code('bias_mA', 0x40),
    }

    # Коды вывода
    E712_OUTPUT_CODES = {
        'PC': get_code('output_PC', 0x38),
        'AC': get_code('output_AC', 0x30),
    }

    # Разряды смещения (табл. 5): три разных базы. Прежний единый диапазон
    # 0x28+d был ошибкой: 0x2A..0x2E = коды ПЕРЕКЛЮЧЕНИЯ ПРЕДЕЛОВ.
    b1 = get_code('bias_digit1_base', 0x50)
    b2 = get_code('bias_digit2_base', 0x60)
    b3 = get_code('bias_digit3_base', 0x70)
    E712_BIAS_DIGIT1 = {d: b1 + d for d in range(10)}
    E712_BIAS_DIGIT2 = {d: b2 + d for d in range(10)}
    E712_BIAS_DIGIT3 = {d: b3 + d for d in range(4)}

    exp_raw = codes.get('allow_experimental', '0').split('#')[0].strip()
    ALLOW_EXPERIMENTAL_TX = exp_raw in ('1', 'true', 'yes', 'on')

    global BIAS_GUARD_ENABLE, BIAS_GUARD_I_LIMIT_MA, BIAS_GUARD_MARGIN
    global BIAS_GUARD_L_FACTOR, BIAS_GUARD_U_COMPL
    if 'BIAS_GUARD' in config:
        bg = config['BIAS_GUARD']
        def _f(key, default):
            try:
                return float(bg.get(key, '').split('#')[0].strip() or default)
            except ValueError:
                return default
        BIAS_GUARD_ENABLE = bg.get('enable', '1').split('#')[0].strip() not in (
            '0', 'false', 'no', 'off')
        BIAS_GUARD_I_LIMIT_MA = _f('i_limit_ma', BIAS_GUARD_I_LIMIT_MA)
        BIAS_GUARD_MARGIN = _f('margin', BIAS_GUARD_MARGIN)
        BIAS_GUARD_L_FACTOR = _f('l_factor', BIAS_GUARD_L_FACTOR)
        BIAS_GUARD_U_COMPL = _f('u_compliance', BIAS_GUARD_U_COMPL)

    global SCAN_GUARD_ENABLE, SCAN_GUARD_D_RATIO
    global SCAN_GUARD_L_SAT_DROP, SCAN_GUARD_R_RATIO
    if 'SCAN_GUARD' in config:
        sg = config['SCAN_GUARD']
        def _g(key, default):
            try:
                return float(sg.get(key, '').split('#')[0].strip() or default)
            except ValueError:
                return default
        SCAN_GUARD_ENABLE = sg.get('enable', '1').split('#')[0].strip() not in (
            '0', 'false', 'no', 'off')
        SCAN_GUARD_D_RATIO = _g('d_ratio', SCAN_GUARD_D_RATIO)
        SCAN_GUARD_L_SAT_DROP = _g('l_sat_drop', SCAN_GUARD_L_SAT_DROP)
        SCAN_GUARD_R_RATIO = _g('r_ratio', SCAN_GUARD_R_RATIO)

    # Включение/выключение отправки разрядов значения (с обрезкой комментариев)
    bias_enable_raw = codes.get('bias_enable_value_cmd', '1')
    bias_enable_str = bias_enable_raw.split('#')[0].strip()
    BIAS_ENABLE_VALUE_CMD = bias_enable_str not in ('0', 'false', 'no', 'off', '')

    logging.info('Коды команд загружены из INI:')
    logging.info(f'  MODE: {E712_MODE_CODES}')
    logging.info(f'  LEVEL: {E712_LEVEL_CODES}')
    logging.info(f'  RANGE: {E712_RANGE_CODES}')
    logging.info(f'  BIAS: {E712_BIAS_CODES}')
    logging.info(f'  OUTPUT: {E712_OUTPUT_CODES}')
    logging.info(f'  BIAS_DIGIT bases: 0x{b1:02X}/0x{b2:02X}/0x{b3:02X} (табл. 5: 0x50/0x60/0x70)')
    logging.info(f'  ALLOW_EXPERIMENTAL_TX={ALLOW_EXPERIMENTAL_TX}')
    logging.info(f'  BIAS_ENABLE_VALUE_CMD={BIAS_ENABLE_VALUE_CMD}')

# --- Коды вывода результата ---
E712_OUTPUT_CODES = {
    'PC': 0x38,   # Вывод РС (реактивная: C/L)
    'AC': 0x30,   # Вывод АС (активная: G/R/D)
}

# --- Интерфейсные команды (Таблица 6) — СПРАВОЧНАЯ ТАБЛИЦА ---
# Это стандартные многолинейные сообщения IEEE-488, передаются С ЛИНИЕЙ УП (ATN).
# Байт без ATN дешифруется прибором как запись регистра, поэтому посылать их
# write_raw() НЕЛЬЗЯ. Отправка — только VISA-операциями (см. send_interface_command).
E712_IFACE_COMMANDS = {
    'ЗАП': 0x08,   # GET -> instr.assert_trigger()
    'СБА': 0x04,   # SDC -> instr.clear() (адресованный сброс)
    'СБУ': 0x14,   # DCL -> instr.clear()
    'ЭПО': 0x18,   # SPE -> внутри instr.read_stb()
    'ЗПО': 0x19,   # SPD -> внутри instr.read_stb()
    'НИД': 0x5F,   # UNT -> адаптер сам при адресации
    'НПМ': 0x3F,   # UNL -> адаптер сам при адресации
}


def allowed_data_bytes() -> set:
    """Белый список байтов ДАННЫХ по табл. 5. Байт вне списка пишет чужой
    регистр (дешифрация старшего ниббла ROM МС9) и блокируется драйвером."""
    s = set()
    s.update(E712_MODE_CODES.values())
    s.update(E712_LEVEL_CODES.values())
    s.update(E712_RANGE_CODES.values())
    s.update(E712_BIAS_CODES.values())
    s.update(E712_BIAS_DIGIT1.values())
    s.update(E712_BIAS_DIGIT2.values())
    s.update(E712_BIAS_DIGIT3.values())
    s.update(E712_OUTPUT_CODES.values())
    return s

# --- Коды символов выходной информации (Таблица 9) ---
# Совпадают с ASCII: '+' = 0x2B, '0' = 0x30, 'E' = 0x45, 'R' = 0x52.
TYPE_SIGN      = 0x20   # 0b0010_xxxx — знак числа
TYPE_DIGIT     = 0x30   # 0b0011_xxxx — цифра 0..9
TYPE_UNIT      = 0x40   # 0b0100_xxxx — единица измерения (C, L, G, D, E)
TYPE_UNIT_ALT  = 0x50   # 0b0101_xxxx — альтернативный код (R = 0x52)

SIGN_PLUS  = 0x0B   # '+'
SIGN_MINUS = 0x0D   # '-'

UNIT_CODES = {
    0x03: 'C',   # ёмкость, Ф
    0x0C: 'L',   # индуктивность, Гн
    0x07: 'G',   # проводимость, См
    0x02: 'R',   # сопротивление, Ом
    0x04: 'D',   # тангенс угла потерь
    0x05: 'E',   # разделитель информации (Exponent)
}

# --- Соответствие режим → измеряемая величина → единица СИ ---
MODE_INFO = {
    'CG': {'param': 'C', 'unit_si': 'F',  'desc': 'Ёмкость, параллельная схема (C‖G)'},
    'CD': {'param': 'C', 'unit_si': 'F',  'desc': 'Ёмкость, последовательная схема (C-D)'},
    'LR': {'param': 'L', 'unit_si': 'H',  'desc': 'Индуктивность, последовательная схема (L-R)'},
    'LD': {'param': 'L', 'unit_si': 'H',  'desc': 'Индуктивность, параллельная схема (L‖D)'},
}


# =============================================================================
# ДЕКОДЕР ОТВЕТА Е7-12
# =============================================================================

# Префиксы СИ-2019
SI_PREFIXES = [
    (1e24, 'Y'), (1e21, 'Z'), (1e18, 'E'), (1e15, 'P'),
    (1e12, 'T'), (1e9, 'G'), (1e6, 'M'), (1e3, 'k'),
    (1e0, ''), (1e-3, 'm'), (1e-6, 'μ'), (1e-9, 'n'),
    (1e-12, 'p'), (1e-15, 'f'), (1e-18, 'a'), (1e-21, 'z'),
]


# Формат цифрового табло Е7-12 — табл. 1 ТО (стр. 8): позиция запятой и
# приставка жёстко привязаны к пределу. (образец, целых цифр, приставка):
PANEL_TABLE1 = {
    'C': {1: (2, 'p'), 2: (3, 'p'), 3: (4, 'p'), 4: (2, 'n'), 5: (3, 'n')},
    'G': {1: (3, 'μ'), 2: (1, 'm'), 3: (2, 'm'), 4: (3, 'm'), 5: (1, '')},
    'L': {1: (1, 'm'), 2: (3, 'μ'), 3: (2, 'μ'), 4: (1, 'μ'), 5: (3, 'n')},
    'R': {1: (2, 'k'), 2: (1, 'k'), 3: (3, ''), 4: (2, ''), 5: (1, '')},
    'D': {r: (1, '') for r in range(1, 6)},
}
_PREFIX_FACTOR = {'p': 1e-12, 'n': 1e-9, 'μ': 1e-6, 'm': 1e-3, '': 1.0, 'k': 1e3}


def format_panel(value: float, unit: str = '', rng=None, param: str = '') -> str:
    """Представление в точности как на цифровом табло Е7-12 (табл. 1 ТО,
    стр. 8): знак явный (+/-), решётка из 5 цифр с запятой, позиция запятой
    и приставка жёстко привязаны к пределу; ведущие нули отображаются.
    D безразмерен: без приставки и единицы, решётка 1.0000.
    При неизвестном пределе (АВТ) запятая ставится нормализацией к мантиссе
    1..1000 с шагом приставки 10^3."""
    if not math.isfinite(value):
        return ('+' if value > 0 else '-') + '#####'
    if param == 'D':
        int_digits, prefix, scaled, unit_ch = 1, '', value, ''
    else:
        unit_ch = unit
        entry = PANEL_TABLE1.get(param, {}).get(rng) if rng else None
        if entry:
            int_digits, prefix = entry
            scaled = value / _PREFIX_FACTOR[prefix]
            if abs(scaled) >= 10 ** int_digits * 2 + 0.5:   # вне 100% перекрытия
                entry = None                                 # предел не тот
        if not entry:
            prefix, scaled = '', value
            if value != 0:
                for pfx in ('p', 'n', 'μ', 'm', '', 'k'):
                    s = value / _PREFIX_FACTOR[pfx]
                    if 1.0 <= abs(s) < 1000.0:
                        prefix, scaled = pfx, s
                        break
                else:
                    return f"{value:+.4e} {unit_ch}".rstrip()
            a = abs(scaled)
            int_digits = 1 if a < 10 else 2 if a < 100 else 3 if a < 1000 else 4
    dec = max(0, 5 - int_digits)
    width = int_digits + dec + (1 if dec else 0)   # с учётом запятой
    body = f"{abs(scaled):0{width}.{dec}f}"
    sign = '-' if (scaled < 0 or (scaled == 0 and value < 0)) else '+'
    tail = f"{prefix}{unit_ch}"
    return f"{sign}{body} {tail}".rstrip()


def format_value(value: float, unit: str = '', fmt: str = 'SCI',
                 rng=None, param: str = '') -> str:
    """Форматирует числовое значение в заданном формате."""
    if fmt == 'PANEL':
        return format_panel(value, unit, rng=rng, param=param)
    if value == 0:
        return f"0 {unit}"
    if not math.isfinite(value):
        return f"∞ {unit}" if value > 0 else f"-∞ {unit}"
    if fmt == 'SCI':
        return f"{value:.4e} {unit}"
    elif fmt == 'ENG':
        exp = math.floor(math.log10(abs(value)))
        eng_exp = (exp // 3) * 3
        mantissa = value / (10 ** eng_exp)
        return f"{mantissa:.4f}e{eng_exp:+d} {unit}"
    elif fmt == 'SI2019':
        abs_val = abs(value)
        for threshold, prefix in SI_PREFIXES:
            if abs_val >= threshold * 0.999:
                scaled = value / threshold
                return f"{scaled:.4g} {prefix}{unit}"
        return f"{value:.4e} {unit}"
    return f"{value:.4e} {unit}"


class E712Measurement:
    """Результат измерения, декодированный из байтовой посылки Е7-12."""

    def __init__(self):
        self.sign = '+'
        self.mantissa_str = '00000'
        self.mantissa_int = 0
        self.exp_sign = '+'
        self.exponent = 0
        self.unit_code = ''
        self.unit_si = ''
        self.value = 0.0
        self.raw_bytes = b''

    def __repr__(self):
        return (f"E712Measurement(value={self.value!r} {self.unit_si}, "
                f"sign={self.sign}, mantissa={self.mantissa_str}, "
                f"exp={self.exp_sign}{self.exponent}, unit={self.unit_code})")

    def formatted(self) -> str:
        return f"{self.sign}{abs(self.value):g} {self.unit_si}"


def decode_measurement(data: bytes) -> E712Measurement:
    """Декодирует одну посылку Е7-12 (11 байт ASCII)."""
    m = E712Measurement()
    m.raw_bytes = bytes(data)

    digits = []
    exp_digits = []
    saw_separator = False

    for byte in data:
        if not isinstance(byte, int):
            byte = ord(byte)
        upper = byte & 0xF0
        lower = byte & 0x0F

        if upper == TYPE_SIGN:
            if lower == SIGN_PLUS:
                if not saw_separator:
                    m.sign = '+'
                else:
                    m.exp_sign = '+'
            elif lower == SIGN_MINUS:
                if not saw_separator:
                    m.sign = '-'
                else:
                    m.exp_sign = '-'

        elif upper == TYPE_DIGIT:
            if lower <= 9:
                if not saw_separator:
                    digits.append(str(lower))
                else:
                    exp_digits.append(str(lower))

        elif upper == TYPE_UNIT or upper == TYPE_UNIT_ALT:
            unit_char = UNIT_CODES.get(lower, '?')
            if unit_char == 'E':
                saw_separator = True
            else:
                m.unit_code = unit_char
                si_map = {'C': 'F', 'L': 'H', 'G': 'S', 'R': 'Ω', 'D': ''}
                m.unit_si = si_map.get(unit_char, unit_char)

    m.mantissa_str = ''.join(digits).rjust(5, '0') if digits else '00000'
    m.mantissa_int = int(m.mantissa_str)
    m.exponent = int(''.join(exp_digits)) if exp_digits else 0
    exp_signed = -m.exponent if m.exp_sign == '-' else m.exponent
    m.value = m.mantissa_int * (10 ** exp_signed)
    if m.sign == '-':
        m.value = -m.value
    return m


def split_measurements(data: bytes) -> list[bytes]:
    """Разбивает поток на отдельные 11-байтные посылки измерений."""
    measurements = []
    i = 0
    n = len(data)
    while i < n:
        # Ищем начало посылки: знак '+' или '-'
        if data[i] not in (0x2B, 0x2D):
            i += 1
            continue
        start = i
        j = i + 1
        # 5 цифр мантиссы
        digits_count = 0
        while j < n and digits_count < 5:
            if 0x30 <= data[j] <= 0x39:
                digits_count += 1
                j += 1
            else:
                break
        if digits_count != 5:
            i += 1
            continue
        # Разделитель 'E'
        if j >= n or data[j] != 0x45:
            i += 1
            continue
        j += 1
        # Знак порядка
        if j >= n or data[j] not in (0x2B, 0x2D):
            i += 1
            continue
        j += 1
        # 1-2 цифры порядка
        exp_count = 0
        while j < n and exp_count < 2:
            if 0x30 <= data[j] <= 0x39:
                exp_count += 1
                j += 1
            else:
                break
        if exp_count == 0:
            i += 1
            continue
        # Единица измерения
        if j >= n:
            i += 1
            continue
        unit_byte = data[j]
        upper = unit_byte & 0xF0
        lower = unit_byte & 0x0F
        if upper not in (TYPE_UNIT, TYPE_UNIT_ALT) or lower not in UNIT_CODES:
            i += 1
            continue
        j += 1
        measurements.append(data[start:j])
        i = j
    return measurements


def safe_first_measurement(raw: bytes) -> E712Measurement:
    """Безопасное получение первого измерения из сырых данных.
    
    Если данных нет или они пустые — возвращает пустой E712Measurement.
    Не вызывает list index out of range при пустом ответе прибора.
    """
    chunks = split_measurements(raw)
    if chunks:
        return decode_measurement(chunks[0])
    return E712Measurement()


def decode_stream(data: bytes) -> list[E712Measurement]:
    """Декодирует поток байт в список измерений."""
    return [decode_measurement(chunk) for chunk in split_measurements(data)]


def hex_dump(data: bytes, prefix: str = '  ') -> str:
    """Форматированный hex-дамп байтовой последовательности."""
    if not data:
        return prefix + '<пусто>'
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hex_part = ' '.join(f'{b:02X}' for b in chunk)
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        lines.append(f'{prefix}{i:04X}  {hex_part:<48}  {ascii_part}')
    return '\n'.join(lines)


# =============================================================================
# VISA-ДРАЙВЕР
# =============================================================================

USB_GPIB_V2_VID = 0x03EB
USB_GPIB_V2_PID = 0x2065
USBTMC_PULSE_INDICATOR_REQUEST_TYPE = 0xA1   # IN, vendor, interface
USBTMC_PULSE_INDICATOR_REQUEST = 0x40


class E712VisaDriver:
    """VISA-драйвер Е7-12 через адаптер UsbGpib V2."""

    def __init__(
        self,
        resource_string: Optional[str] = None,
        gpib_addr: int = 1,
        backend: Optional[str] = None,
        timeout_ms: int = 5000,
        read_termination: Optional[str] = None,
    ):
        self.resource_string = resource_string
        self.gpib_addr = gpib_addr
        self.backend = backend
        self.timeout_ms = timeout_ms
        self.read_termination = read_termination
        self.rm = None
        self.instr = None
        self._connected = False
        self._last_raw_data = b''
        self._last_measurements = []

    def connect(self) -> None:
        """Открывает VISA-сессию с подробным логированием."""
        log = logging.getLogger('E712_RAW')
        if self._connected:
            log.warning('connect(): уже подключено')
            return
        if pyvisa is None:
            raise RuntimeError('pyvisa не установлен. Установите: pip install pyvisa')

        # 1. Создаём ResourceManager
        log.info('VISA: создание ResourceManager (backend=%s)...', self.backend or 'auto')
        try:
            if self.backend:
                self.rm = pyvisa.ResourceManager(self.backend)
            else:
                self.rm = pyvisa.ResourceManager()
        except Exception as e:
            log.error('VISA: ошибка ResourceManager: %s', e)
            raise RuntimeError(f'Не удалось создать ResourceManager: {e}') from e
        log.info('VISA: ResourceManager создан. Версия pyvisa=%s', pyvisa.__version__)

        # 2. Поиск ресурса
        all_resources = list(self.rm.list_resources())
        log.info('VISA: всего ресурсов в системе: %d', len(all_resources))
        for r in all_resources:
            log.info('  %s', r)

        if not self.resource_string:
            self.resource_string = self._find_usbgpib_resource()
            if not self.resource_string:
                log.error('VISA: адаптер UsbGpib V2 (VID=0x%04X, PID=0x%04X) не найден',
                          USB_GPIB_V2_VID, USB_GPIB_V2_PID)
                raise RuntimeError(
                    f'Адаптер UsbGpib V2 не найден. Доступные: {all_resources}'
                )
        log.info('VISA: выбран ресурс: %s', self.resource_string)

        # 3. Открытие ресурса
        log.info('VISA: открытие ресурса (timeout=%d ms)...', self.timeout_ms)
        self.instr = self.rm.open_resource(
            self.resource_string, open_timeout=int(self.timeout_ms)
        )
        self.instr.timeout = self.timeout_ms
        if self.read_termination:
            self.instr.read_termination = self.read_termination
        self.instr.write_termination = '\n'
        log.info('VISA: ресурс открыт. read_termination=%s, timeout=%d ms',
                 self.read_termination or 'None (до EOI)', self.timeout_ms)

        # 4. Настройка GPIB-адреса
        log.info('VISA: установка GPIB-адреса = %d', self.gpib_addr)
        try:
            self._set_gpib_addr(self.gpib_addr)
            log.info('VISA: GPIB-адрес установлен')
        except Exception as e:
            log.warning('VISA: не удалось установить GPIB-адрес (некритично): %s', e)

        # AutoID OFF: иначе адаптер при подключении шлёт '*IDN?\n' КАК ДАННЫЕ,
        # и прибор молча получает «предел 5 + LR» (чек-лист e712_kop.py, п. 1).
        try:
            self._write_adapter_cmd('!autoid off')
            log.info('VISA: адаптеру отправлено !autoid off')
        except Exception as e:
            log.warning('VISA: !autoid off не отправлена: %s', e)

        self._connected = True

        # Безопасное исходное состояние: DCL, затем смещение U = 0.0 одним пакетом.
        # Причина аппаратная: включённое смещение на заземлённой линии U/I СМЕЩ.
        # сжигает Пр9 и экранную звезду (инцидент 2026-08).
        try:
            self.device_clear()
            self.write_command_bytes(
                [E712_BIAS_CODES['V'], E712_BIAS_DIGIT3[0],
                 E712_BIAS_DIGIT2[0], E712_BIAS_DIGIT1[0]],
                label='SAFE_BIAS_ZERO')
            log.info('VISA: исходное состояние установлено (DCL + смещение U 0.0)')
        except Exception as e:
            log.warning('VISA: не удалось установить исходное состояние: %s', e)

        log.info('VISA: === ПОДКЛЮЧЕНО === (GPIB addr=%d, resource=%s)',
                 self.gpib_addr, self.resource_string)

    def disconnect(self) -> None:
        """Закрывает VISA-сессию с логированием. Перед закрытием — смещение 0."""
        log = logging.getLogger('E712_RAW')
        if self.instr and self._connected:
            try:
                self.write_command_bytes(
                    [E712_BIAS_CODES['V'], E712_BIAS_DIGIT3[0],
                     E712_BIAS_DIGIT2[0], E712_BIAS_DIGIT1[0]],
                    label='SAFE_BIAS_ZERO')
                self.device_clear()
            except Exception as e:
                log.warning('VISA: обнуление смещения при отключении: %s', e)
        if self.instr:
            log.info('VISA: закрытие ресурса...')
            try:
                self.instr.close()
                log.info('VISA: ресурс закрыт')
            except Exception as e:
                log.warning('VISA: ошибка закрытия ресурса: %s', e)
            self.instr = None
        if self.rm:
            log.info('VISA: закрытие ResourceManager...')
            try:
                self.rm.close()
                log.info('VISA: ResourceManager закрыт')
            except Exception as e:
                log.warning('VISA: ошибка закрытия RM: %s', e)
            self.rm = None
        self._connected = False
        log.info('VISA: === ОТКЛЮЧЕНО ===')

    def close(self) -> None:
        self.disconnect()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    def _find_usbgpib_resource(self) -> Optional[str]:
        resources = self.rm.list_resources()
        matches = []
        for r in resources:
            low = r.lower()
            if f'0x{USB_GPIB_V2_VID:04x}' in low and f'0x{USB_GPIB_V2_PID:04x}' in low:
                matches.append(r)
            elif f'{USB_GPIB_V2_VID:04x}' in low and f'{USB_GPIB_V2_PID:04x}' in low:
                matches.append(r)
        return matches[0] if matches else None

    def _pulse_indicator(self) -> None:
        # USBTMC pulse indicator request — нужен перед !addr/!ver и т.д.
        try:
            self.instr.control_in(
                request_type=USBTMC_PULSE_INDICATOR_REQUEST_TYPE,
                request=USBTMC_PULSE_INDICATOR_REQUEST,
                value=0, index=0, length=1,
            )
        except Exception:
            pass

    def _write_adapter_cmd(self, cmd: str) -> None:
        self._pulse_indicator()
        self.instr.write(cmd)

    def _set_gpib_addr(self, primary: int, secondary: Optional[int] = None) -> None:
        if not 1 <= primary <= 30:
            raise ValueError(f'GPIB primary address должен быть 1..30')
        # Единственный корректный синтаксис для xyphro UsbGpib V2:
        cmd = f'!addr {primary}' if secondary is None else f'!addr {primary} {secondary}'
        self._write_adapter_cmd(cmd)
        # time.sleep(0.05) удалён: адаптер сам выполняет handshake

    def write_command_byte(self, code: int, label: str = '') -> None:
        """Отправляет байт с логированием результата GPIB handshake.

        Логирует:
          • Что отправлено (HEX + ASCII + бинарный вид)
          • Успех/провал handshake (DAV/NRFD/NDAC)
          • Время выполнения (мс)
        """
        if not self._connected:
            raise RuntimeError('Не подключено.')
        if not 0 <= code <= 0xFF:
            raise ValueError(f'Код должен быть 0..255, получено 0x{code:X}')
        if code not in allowed_data_bytes() and not ALLOW_EXPERIMENTAL_TX:
            raise ValueError(
                f'Байт 0x{code:02X} отсутствует в табл. 5 — передача заблокирована. '
                f'Байт без ATN пишет регистр прибора (ROM МС9). '
                f'Для эксперимента: [CODES] allow_experimental = 1')
        log = logging.getLogger('E712_RAW')
        prefix = f'{label}: ' if label else ''
        ascii_char = chr(code) if 32 <= code < 127 else '·'
        bin_str = f'{code:08b}'
        log.info(f'>>> TX {prefix}0x{code:02X} [{ascii_char}] BIN={bin_str}')
        t0 = time.time()
        try:
            self.instr.write_raw(bytes([code]))
            elapsed_ms = (time.time() - t0) * 1000
            log.info(f'    TX OK: handshake завершён за {elapsed_ms:.1f} мс '
                     f'(DAV/NRFD/NDAC — прибор принял данные)')
        except Exception as e:
            elapsed_ms = (time.time() - t0) * 1000
            log.error(f'    TX FAIL: {e} (через {elapsed_ms:.1f} мс) — '
                      f'возможно NRFD/NDAC зависли (прибор не отвечает)')
            raise

    def write_command_bytes(self, codes: list[int], label: str = '') -> None:
        """Отправляет несколько байт ОДНИМ bulk transfer с логированием."""
        if not self._connected:
            raise RuntimeError('Не подключено.')
        if not ALLOW_EXPERIMENTAL_TX:
            wl = allowed_data_bytes()
            bad = [c for c in codes if c not in wl]
            if bad:
                raise ValueError(
                    'Байты вне табл. 5 заблокированы: '
                    + ' '.join(f'0x{c:02X}' for c in bad)
                    + '. Для эксперимента: [CODES] allow_experimental = 1')
        data = bytes(codes)
        log = logging.getLogger('E712_RAW')
        prefix = f'{label}: ' if label else ''
        # Читаемый формат: HEX + ASCII
        hex_str = ' '.join(f'{b:02X}' for b in data)
        ascii_str = ''.join(chr(b) if 32 <= b < 127 else '·' for b in data)
        log.info(f'>>> TX {prefix}({len(data)} bytes) HEX=[{hex_str}] ASCII=[{ascii_str}]')
        t0 = time.time()
        try:
            self.instr.write_raw(data)
            elapsed_ms = (time.time() - t0) * 1000
            log.info(f'    TX OK: handshake завершён за {elapsed_ms:.1f} мс')
        except Exception as e:
            elapsed_ms = (time.time() - t0) * 1000
            log.error(f'    TX FAIL: {e} (через {elapsed_ms:.1f} мс)')
            raise

    def read_raw(self, max_bytes: int = 22, label: str = '') -> bytes:
        """Читает ответ с логированием GPIB handshake результата.

        :param max_bytes: максимум байт (по умолч. 22 = 2 измерения по 11 байт)
        :param label: метка для лога

        Логирует:
          • Что получено (ASCII-декодинг, НЕ hex-дамп)
          • EOI обнаружен (данные пришли) или таймаут (прибор не отвечает)
          • Время ожидания (мс)
          • Декодированные измерения (человекочитаемый формат)
        """
        if not self._connected:
            raise RuntimeError('Не подключено.')
        log = logging.getLogger('E712_RAW')
        prefix = f'{label}: ' if label else ''
        t0 = time.time()
        try:
            # Чтение до КП (EOI): одна USBTMC bulk-транзакция, границу ставит
            # прибор сигналом EOI на последнем байте. max_bytes — справочно.
            data = self.instr.read_raw()
            elapsed_ms = (time.time() - t0) * 1000
            # Декодируем как ASCII (Е7-12 выдаёт текст)
            ascii_str = data.decode('ascii', errors='replace')
            log.info(f'<<< RX {prefix}{len(data)} bytes за {elapsed_ms:.1f} мс '
                     f'(EOI обнаружен)')
            log.info(f'    ASCII: {ascii_str!r}')
            # Разбиваем на измерения по 11 байт
            measurements = split_measurements(data)
            self._last_raw_data = data
            self._last_measurements = measurements
            if measurements:
                log.info(f'    Декодировано {len(measurements)} измерений:')
                for i, m_bytes in enumerate(measurements):
                    meas = decode_measurement(m_bytes)
                    log.info(f'      [{i+1}] {meas.raw_bytes.decode("ascii","replace")} '
                             f'→ {meas.formatted()} '
                             f'(мантисса={meas.mantissa_str} порядок={meas.exp_sign}{meas.exponent})')
            return data
        except Exception as e:
            elapsed_ms = (time.time() - t0) * 1000
            log.error(f'<<< RX {prefix}FAIL: {e} (через {elapsed_ms:.0f} мс) — '
                      f'прибор не ответил (таймаут, возможно не адресован как talker)')
            return b''

    def read_status_byte(self) -> int:
        """Читает IEEE 488 статусный байт (STB).

        Биты STB:
          6 (0x40): SRQ — Service Request (прибор запрашивает внимание)
          4 (0x10): MAV — Message Available (данные в выходном буфере)

        :return: 0..255, или -1 при ошибке
        """
        if not self._connected:
            return -1
        log = logging.getLogger('E712_RAW')
        try:
            stb = self.instr.read_stb()
            srq = 'YES' if stb & 0x40 else 'no'
            mav = 'YES' if stb & 0x10 else 'no'
            log.info(f'    STB: 0x{stb:02X} (SRQ={srq}, MAV={mav})')
            return stb
        except Exception as e:
            log.debug(f'    STB: недоступен ({e})')
            return -1

    def wait_for_mav(self, timeout_ms: int = 5000) -> bool:
        """Однократная проверка MAV (bit 4 STB). Циклический опрос STB
        удалён: опрос шины по часам противоречит событийной модели КОП;
        готовность данных ждётся handshake'ом самого чтения."""
        stb = self.read_status_byte()
        return stb >= 0 and bool(stb & 0x10)

    def wait_for_srq(self, timeout_ms: int = 5000) -> bool:
        """Ожидание SRQ событием VISA (аппаратная линия ЗО), без опроса."""
        try:
            self.instr.wait_for_srq(timeout_ms / 1000.0)
            return True
        except Exception:
            stb = self.read_status_byte()
            return stb >= 0 and bool(stb & 0x40)

    def read_stream(self, max_bytes: int = 22, label: str = '') -> list[E712Measurement]:
        raw = self.read_raw(max_bytes=max_bytes, label=label)
        return decode_stream(raw)

    def set_mode(self, mode: str) -> None:
        mode = mode.upper()
        if mode not in E712_MODE_CODES:
            raise ValueError(f'Неизвестный режим: {mode}')
        self.write_command_byte(E712_MODE_CODES[mode], label=f'MODE_{mode}')

    def set_level(self, level: str) -> None:
        if level not in E712_LEVEL_CODES:
            raise ValueError(f'Неизвестный уровень: {level}')
        self.write_command_byte(E712_LEVEL_CODES[level], label=f'LEVEL_{level}')

    def set_range(self, range_: Union[str, int]) -> None:
        if isinstance(range_, str):
            range_ = range_.upper()
            if range_ == 'AUTO':
                self.write_command_byte(E712_RANGE_CODES['AUTO'], label='RANGE_AUTO')
                return
            range_ = int(range_)
        if range_ not in (1, 2, 3, 4, 5):
            raise ValueError(f'Предел должен быть 1..5 или AUTO')
        self.write_command_byte(E712_RANGE_CODES[range_], label=f'RANGE_{range_}')

    def set_bias_mode(self, mode: str) -> None:
        """Выбирает режим источника смещения: 'V' (напряжение) или 'mA' (ток).

        Внимание: НЕ делаем .upper() — ключ 'mA' чувствителен к регистру!
        """
        # Поддерживаем оба варианта: 'mA', 'ma' → 'mA'; 'V', 'v' → 'V'
        mode_norm = mode.strip()
        if mode_norm.lower() == 'ma':
            mode_norm = 'mA'
        elif mode_norm.lower() == 'v':
            mode_norm = 'V'
        if mode_norm not in E712_BIAS_CODES:
            raise ValueError(f'Неизвестный режим смещения: {mode!r}. '
                             f'Допустимо: V, mA')
        self.write_command_byte(E712_BIAS_CODES[mode_norm], label=f'BIAS_{mode_norm}')

    def set_output(self, channel: str) -> None:
        channel = channel.upper()
        if channel not in E712_OUTPUT_CODES:
            raise ValueError(f'Неизвестный канал: {channel}')
        self.write_command_byte(E712_OUTPUT_CODES[channel], label=f'OUT_{channel}')

    def assert_trigger(self) -> None:
        """ЗАП: посылает GET (0x08 С ЛИНИЕЙ УП/ATN) через USB488 TRIGGER.
        Запускает цикл измерения; счётчик тактов сбрасывается в такт 0."""
        log = logging.getLogger('E712_RAW')
        log.info('>>> ATN ЗАП (GET) via assert_trigger()')
        self.instr.assert_trigger()

    def device_clear(self) -> None:
        """СБУ/СБА: DCL/SDC (С ЛИНИЕЙ УП/ATN) через USBTMC DEVICE CLEAR."""
        log = logging.getLogger('E712_RAW')
        log.info('>>> ATN СБУ (DCL/SDC) via clear()')
        self.instr.clear()

    def send_interface_command(self, name: str) -> None:
        """Интерфейсные команды табл. 6 — ТОЛЬКО VISA-операциями (с ATN).
        Как данные (write_raw) не передаются никогда: байт без ATN
        дешифруется прибором как запись регистра."""
        if name == 'ЗАП':
            self.assert_trigger()
        elif name in ('СБУ', 'СБА'):
            self.device_clear()
        elif name in ('ЭПО', 'ЗПО'):
            self.read_status_byte()   # SPE/SPD выполняет адаптер внутри read_stb
        elif name in ('НИД', 'НПМ'):
            pass                      # UNT/UNL адаптер выдаёт сам при адресации
        else:
            raise ValueError(f'Неизвестная интерфейсная команда: {name}')

    def setup_measurement(self, mode: str, range_: Union[str, int] = 'AUTO',
                          level: str = 'x1', bias_mode: Optional[str] = None) -> None:
        """Настраивает прибор ОДНИМ bulk transfer (batch mode).

        КРИТИЧНО для производительности и корректности:
        Адаптер xyphro/UsbGpib V2 при КАЖДОМ write_raw() отправляет на GPIB:
          1. UNT, TAG(0), UNL, LAG(addr) — адресация (ATN=1)
          2. Данные (ATN=0, EOI на последнем байте)
          3. UNL, UNT — деадресация (ATN=1)

        Если отправлять команды по одной (5× write_raw), прибор получит
        5× UNL+UNT между командами — это сбивает его внутреннее состояние.

        Объединяя все команды в ОДИН write_raw(bytes([...])), прибор
        получает их как единую посылку без разрывов.
        """
        commands = []
        commands.append(E712_MODE_CODES[mode.upper()])
        if isinstance(range_, str):
            range_ = range_.upper()
            if range_ == 'AUTO':
                commands.append(E712_RANGE_CODES['AUTO'])
            else:
                commands.append(E712_RANGE_CODES[int(range_)])
        else:
            commands.append(E712_RANGE_CODES[range_])
        commands.append(E712_LEVEL_CODES[level])
        if bias_mode:
            mode_norm = bias_mode.strip()
            if mode_norm.lower() == 'ma':
                mode_norm = 'mA'
            elif mode_norm.lower() == 'v':
                mode_norm = 'V'
            commands.append(E712_BIAS_CODES[mode_norm])
        self.write_command_bytes(commands, label=f'SETUP({mode},{range_},{level})')

    def setup_and_trigger(self, mode: str, range_: Union[str, int] = 'AUTO',
                          level: str = 'x1', bias_mode: Optional[str] = None,
                          output_channel: str = 'PC') -> None:
        """Настраивает прибор И запускает измерение ОДНИМ bulk transfer.

        Объединяет: mode + range + level + (bias) + output + ПО
        в один write_raw(bytes([...])) — максимально быстро и без UNL/UNT
        между командами.

        ВНИМАНИЕ: ПО всегда запускает НОВЫЙ цикл интегрирования.
        Если output_channel='PC' — прибор выдаст только РС (11 байт).
        Если output_channel='AC' — прибор выдаст только АС (11 байт).
        Для получения обеих составляющих из одного цикла используйте
        measure_once(), который НЕ включает output_channel в команды
        и читает 22 байта (РС + АС).
        """
        commands = []
        commands.append(E712_MODE_CODES[mode.upper()])
        if isinstance(range_, str):
            range_ = range_.upper()
            if range_ == 'AUTO':
                commands.append(E712_RANGE_CODES['AUTO'])
            else:
                commands.append(E712_RANGE_CODES[int(range_)])
        else:
            commands.append(E712_RANGE_CODES[range_])
        commands.append(E712_LEVEL_CODES[level])
        if bias_mode:
            mode_norm = bias_mode.strip()
            if mode_norm.lower() == 'ma':
                mode_norm = 'mA'
            elif mode_norm.lower() == 'v':
                mode_norm = 'V'
            commands.append(E712_BIAS_CODES[mode_norm])
        commands.append(E712_OUTPUT_CODES[output_channel.upper()])
        self.write_command_bytes(commands,
                                 label=f'SETUP({mode},{range_},{level},{output_channel})')
        self.assert_trigger()   # ЗАП = GET с ATN, не байт данных

    def trigger_measurement(self) -> None:
        self.assert_trigger()

    # clear_input_buffer() удалён — на GPIB нет буфера для очистки.
    # Е7-12 хранит данные в ОЗУ (Мс15/Мс16), которые читаются селекторами 0x38/0x30.
    # Адаптер xyphro/UsbGpib V2 выполняет handshake на уровне прошивки.

    def get_adapter_version(self) -> str:
        try:
            self._pulse_indicator()
            return self.instr.query('!ver').strip()
        except Exception:
            return ''


def list_visa_resources(backend: Optional[str] = None) -> list[str]:
    if pyvisa is None:
        return []
    if backend:
        rm = pyvisa.ResourceManager(backend)
    else:
        rm = pyvisa.ResourceManager()
    try:
        all_res = rm.list_resources()
        return [r for r in all_res
                if f'0x{USB_GPIB_V2_VID:04x}' in r.lower()
                or (f'{USB_GPIB_V2_VID:04x}' in r.lower()
                    and f'{USB_GPIB_V2_PID:04x}' in r.lower())]
    finally:
        rm.close()


def create_driver(gpib_addr: int = 1, resource_string: Optional[str] = None,
                  backend: Optional[str] = None):
    drv = E712VisaDriver(resource_string=resource_string, gpib_addr=gpib_addr,
                         backend=backend)
    drv.connect()
    return drv


# =============================================================================
# АДАПТЕРНЫЙ СЛОЙ (E712Controller)
# =============================================================================
# Связывает GUI F0.2 (ожидает set_mode/set_bias_voltage/measure_once и т.д.)
# с реальным VISA-драйвером. 

class E712Controller:
    """Адаптерный слой между GUI F0.2 и реальным VISA-драйвером Е7-12.

       • Программно задаёт смещение 0..39.9 В / 0..39.9 мА через коды 8-4-2-1
    """

    FREQ = 1_000_000.0    # Частота тест-сигнала Е7-12 = 1 МГц (ТО п.2.1)
                            # FIXME: по документации Е7-12 работает на 1 MHz.
                            #        Но в типичных применениях для D/Q берут 1 kHz.
                            #        Уточнить по реальным измерениям.
    OMEGA = 2 * math.pi * FREQ
    MAX_AVERAGES = 100
    MAX_BIAS = 39.9         # Максимум 39.9 В или 39.9 мА

    def __init__(self, gpib_addr: int = 1, resource_string: Optional[str] = None,
                 backend: Optional[str] = None, debug: bool = False,
                 config: Optional[Any] = None):
        self.debug = debug
        self._mode = 'CG'
        self._level_high = True
        self._range = 'AUTO'
        self._bias_on = False
        self._bias_type = 'N'
        self.csv_sink = None       # вызывается после каждого measure_once
        self.csv_context = ''      # вкладка/операция для строки CSV
        self.driver = create_driver(
            gpib_addr=gpib_addr,
            resource_string=resource_string,
            backend=backend,
        )
        self._last_result = None
        if config is None:
            config = load_config()
        # read_pattern и output_prefix удалены — Е7-12 работает только
        # в режиме separate (селекторы ОЗУ 0x38/0x30), combined не поддерживается
        logging.info('E712Controller: режим чтения = separate (единственный корректный)')

    # --- Настройка режима ---
    def set_mode(self, mode: str):
        mode = mode.upper()
        self._mode = mode
        self.driver.set_mode(mode)

    def set_range(self, range_val):
        self._range = range_val
        self.driver.set_range(range_val)

    def set_level(self, high: bool = True):
        self._level_high = high
        self.driver.set_level('x1' if high else 'x0.1')

    # --- Смещение ---
    # Программная установка смещения через КОП.
    #
    # По наблюдениям: команда 0x44 (mA) даёт смещение +2.5В — значит прибор
    # ЧАСТИЧНО реагирует. Коды разрядов значения (8-4-2-1) настраиваются
    # через INI-файл (секция [CODES]), чтобы можно было экспериментировать
    # без перекомпиляции.
    #
    # В INI можно включить/выключить отправку разрядов значения:
    #   [CODES]
    #   bias_enable_value_cmd = 1   # 1 = отправлять, 0 = только тип (V/mA)
    #
    # Базы разрядов (могут быть переопределены):
    #   bias_digit1_base = 0x50    # 1-й разряд = 0x50 + d (десятые)
    #   bias_digit2_base = 0x60    # 2-й разряд = 0x60 + d (единицы)
    #   bias_digit3_base = 0x70    # 3-й разряд = 0x70 + d (десятки 0..3)

    def preflight_bias(self, bias_type: str, value: float) -> dict:
        """Предохранитель смещения: предизмерение БЕЗ смещения + допуск.

        Возвращает {'allowed': bool, 'reason': str, 'g_s': float}.
        Физика: измерение на 1 МГц, смещение постоянное. Для CG/CD G(1 МГц)
        завышает утечку DC — допуск консервативен. Для LR/LD R(1 МГц)
        завышает R_DC — введён коэффициент запаса BIAS_GUARD_L_FACTOR.
        Замыкание самой линии смещения на корпус предизмерение НЕ видит.
        """
        if not BIAS_GUARD_ENABLE or value <= 0:
            return {'allowed': True, 'reason': 'guard off / нулевое смещение',
                    'g_s': float('nan')}
        saved_on = getattr(self, '_bias_on', False)
        saved_ctx = self.csv_context
        self._bias_on = False                     # предизмерение строго без смещения
        self.csv_context = (saved_ctx + '/preflight').lstrip('/')
        try:
            pre = self.measure_once()
        finally:
            self._bias_on = saved_on
            self.csv_context = saved_ctx
        i_lim = BIAS_GUARD_I_LIMIT_MA * 1e-3 * BIAS_GUARD_MARGIN   # А
        mode = self._mode
        # Эквивалентная проводимость постоянному току (оценка сверху/снизу)
        if mode in ('CG', 'CD'):
            G = pre.get('G', 0.0) or 0.0
            if mode == 'CD':
                Rs = pre.get('Rs', 0.0) or 0.0    # последовательная схема
                G = 1.0 / Rs if Rs > 0 else 0.0
            g_note = f'G(1 МГц) = {G:.3e} S (для DC — оценка сверху)'
        else:                                     # LR / LD
            R = pre.get('Rs' if mode == 'LR' else 'Rp', 0.0) or 0.0
            R_dc = R / BIAS_GUARD_L_FACTOR if R > 0 else 0.0
            G = 1.0 / R_dc if R_dc > 0 else float('inf')
            g_note = (f'R(1 МГц) = {R:.3e} Ω, оценка R_DC = {R_dc:.3e} Ω '
                      f'(запас x{BIAS_GUARD_L_FACTOR:g})')
        if bias_type == 'V':
            if G == 0:
                return {'allowed': True, 'g_s': G,
                        'reason': f'U-смещение разрешено: {g_note}, ток ~0'}
            i_pred = G * value
            if i_pred <= i_lim:
                return {'allowed': True, 'g_s': G, 'reason':
                        f'U-смещение разрешено: прогноз {i_pred*1e3:.1f} мА '
                        f'<= {i_lim*1e3:.1f} мА. {g_note}'}
            u_max = i_lim / G
            return {'allowed': False, 'g_s': G, 'reason':
                    f'U-СМЕЩЕНИЕ ЗАПРЕЩЕНО: прогноз тока {i_pred*1e3:.0f} мА '
                    f'> {i_lim*1e3:.1f} мА ({g_note}). '
                    f'Допустимо U <= {u_max:.2f} В, либо Уст. I.'}
        else:                                     # 'mA' — источник тока
            i_req = value * 1e-3
            if not math.isfinite(G) and G != 0:
                return {'allowed': True, 'g_s': G,
                        'reason': f'I-смещение разрешено: {g_note}'}
            u_pred = i_req / G if G > 0 else float('inf')
            if u_pred <= BIAS_GUARD_U_COMPL:
                return {'allowed': True, 'g_s': G, 'reason':
                        f'I-смещение разрешено: прогноз {u_pred:.1f} В на '
                        f'объекте. {g_note}'}
            return {'allowed': False, 'g_s': G, 'reason':
                    f'I-СМЕЩЕНИЕ ЗАПРЕЩЕНО: DC-путь объекта разомкнут '
                    f'({g_note}), источник тока уйдёт в ~{BIAS_GUARD_U_COMPL:g} В '
                    f'на объекте. Для конденсаторов — Уст. U.'}

    def apply_bias_guarded(self, bias_type: str, value: float):
        """Предизмерение -> допуск -> установка смещения. При запрете —
        RuntimeError с причиной, смещение остаётся нулевым."""
        verdict = self.preflight_bias(bias_type, value)
        logging.info('BIAS GUARD: %s', verdict['reason'])
        if not verdict['allowed']:
            self._bias_type = bias_type if bias_type in ('V', 'mA') else 'V'
            self.set_bias_off()
            raise RuntimeError(verdict['reason'])
        if bias_type == 'V':
            self.set_bias_voltage(value)
        else:
            self.set_bias_current(value)
        return verdict

    def set_bias_off(self):
        """Отключение смещения.

        СБУ (DCL, с ATN) сбрасывает устройство; затем гарантированный ноль
        одним пакетом данных: Уст.U + 0.0.0 = 0x48, 0x70, 0x60, 0x50.
        """
        self._bias_on = False
        # Сохраняем тип смещения для корректного сброса (V или mA)
        bias_type_to_reset = getattr(self, '_bias_type', 'V')
        if bias_type_to_reset not in ('V', 'mA'):
            bias_type_to_reset = 'V'
        
        try:
            self.driver.send_interface_command('СБУ')
            logging.info('СБУ (0x14 DCL) отправлена — устройство сброшено')
        except Exception as e:
            logging.warning('СБУ не отправлена: %s', e)
        
        # Гарантированный ноль: Уст.U или Уст.I + 0.0.0
        if BIAS_ENABLE_VALUE_CMD:
            self.driver.set_bias_mode(bias_type_to_reset)
            self._send_bias_value(0.0)

    def set_bias_voltage(self, voltage: float):
        """Устанавливает смещение напряжением.

        :param voltage: 0..39.9 В
        """
        self._bias_on = True
        self._bias_type = 'V'
        self._bias_value = voltage
        self.driver.set_bias_mode('V')
        if BIAS_ENABLE_VALUE_CMD:
            self._send_bias_value(voltage)
            logging.info(f'Смещение: V={voltage:.1f} В')
        else:
            logging.info(f'Смещение: V={voltage:.1f} В (только тип, значение игнорируется)')

    def set_bias_current(self, current: float):
        """Устанавливает смещение током.

        :param current: 0..39.9 мА
        """
        self._bias_on = True
        self._bias_type = 'mA'
        self._bias_value = current
        self.driver.set_bias_mode('mA')
        if BIAS_ENABLE_VALUE_CMD:
            self._send_bias_value(current)
            logging.info(f'Смещение: mA={current:.1f}')
        else:
            logging.info(f'Смещение: mA={current:.1f} (только тип, значение игнорируется)')

    def reset_device(self):
        """Полный сброс прибора интерфейсной командой СБУ (0x14, DCL).

        Рекомендуется вызывать:
          • Перед началом работы (после подключения)
          • При переключении между режимами ДИСТ./МЕСТН.
          • При обнаружении «залипшего» смещения
          • После ошибок измерения
        """
        try:
            self.driver.send_interface_command('СБУ')
            # time.sleep(0.3) удалён: GPIB handshake гарантирует завершение
            logging.info('СБУ (0x14 DCL) отправлена — прибор сброшен')
        except Exception as e:
            logging.error('Ошибка сброса СБУ: %s', e)

    def _send_bias_value(self, value: float):
        """Отправляет 3 разряда значения смещения ОДНИМ bulk transfer.

        value: 0..39.9 (вольты или миллиамперы)
        Порядок по табл. 5: вид (U/I), десятки (0x70+d), единицы (0x60+d),
        десятые (0x50+d) — одним пакетом.

        КРИТИЧНО: Адаптер xyphro/UsbGpib V2 при каждом write_raw() отправляет
        UNL+UNT между байтами, что сбрасывает регистры ЦАП прибора.
        Все 4 байта (тип смещения + 3 цифры) должны идти одним пакетом.
        """
        value = max(0.0, min(self.MAX_BIAS, value))
        v = round(value * 10)   # 0..399
        d0 = v % 10             # десятые
        d1 = (v // 10) % 10     # единицы
        d2 = (v // 100) % 10    # десятки (0..3)
        
        # Формируем команды: тип смещения + 3 цифры
        cmds = [E712_BIAS_CODES[self._bias_type]]
        cmds.append(E712_BIAS_DIGIT3[d2])
        cmds.append(E712_BIAS_DIGIT2[d1])
        cmds.append(E712_BIAS_DIGIT1[d0])
        
        self.driver.write_command_bytes(cmds, label=f'BIAS({self._bias_type})={value:.1f}')
        logging.info(f'BIAS value={value:.1f} → deca={d2} unit={d1} deci={d0} '
                     f'(codes: 0x{E712_BIAS_DIGIT3[d2]:02X} 0x{E712_BIAS_DIGIT2[d1]:02X} 0x{E712_BIAS_DIGIT1[d0]:02X})')

    # --- Измерение ---

    def _append_bias_cmds(self, cmds: list[int], value: float):
        """Добавляет коды разрядов смещения в список команд (без отправки).
        Порядок: десятки → единицы → десятые (ТО 2.724.011)."""
        value = max(0.0, min(self.MAX_BIAS, value))
        v = round(value * 10)
        d0 = v % 10             # десятые
        d1 = (v // 10) % 10     # единицы
        d2 = (v // 100) % 10    # десятки
        cmds.append(E712_BIAS_DIGIT3[d2])
        cmds.append(E712_BIAS_DIGIT2[d1])
        cmds.append(E712_BIAS_DIGIT1[d0])

    def _build_result(self, m_pc: E712Measurement, m_ac: E712Measurement) -> Dict[str, Any]:
        """Строит dict результата из двух измерений PC+AC.
        Вычисляет Q, D, X, Z, Ls, Lp, Cs, Cp, Rs, Rp, θ для всех режимов."""
        result: Dict[str, Any] = {
            'raw': (f"PC={m_pc.raw_bytes.decode('ascii', 'replace')},"
                    f"AC={m_ac.raw_bytes.decode('ascii', 'replace')}"),
            'primary': 0.0,
            'secondary': 0.0,
        }
        pc = m_pc.value
        ac = m_ac.value
        if self._mode == 'CG':
            Cp = pc
            Gp = ac
            result.update({'C': Cp, 'G': Gp, 'primary': Cp, 'secondary': Gp})
            if Cp != 0:
                D = Gp / (self.OMEGA * abs(Cp))
                Q = 1.0 / D if D != 0 else float('inf')
                Xc = -1.0 / (self.OMEGA * Cp) if Cp != 0 else float('inf')
                Z = 1.0 / complex(Gp, self.OMEGA * Cp)
                theta = math.degrees(math.atan2(-self.OMEGA * Cp, Gp)) if (Gp != 0 or Cp != 0) else 0
                denom = Gp**2 + (self.OMEGA * Cp)**2
                Rs = Gp / denom if denom != 0 else float('inf')
                Cs = denom / (self.OMEGA**2 * Cp) if (denom != 0 and Cp != 0) else float('inf')
                result.update({
                    'D': D, 'Q': Q, 'X': Xc, 'Z': abs(Z),
                    'theta': theta, 'Rs': Rs, 'Cs': Cs,
                    'Rp': 1.0/Gp if Gp != 0 else float('inf'),
                    'Ls': 0, 'Lp': 0,
                })
        elif self._mode == 'CD':
            Cs = pc
            D = ac
            result.update({'C': Cs, 'D': D, 'primary': Cs, 'secondary': D})
            if Cs != 0:
                Xc = -1.0 / (self.OMEGA * Cs)
                Rs = D / (self.OMEGA * abs(Cs)) if Cs != 0 else 0
                Q = 1.0 / D if D != 0 else float('inf')
                Z = complex(Rs, Xc)
                theta = math.degrees(math.atan2(Xc, Rs)) if Rs != 0 else (-90 if Xc < 0 else 90)
                denom = Rs**2 + Xc**2
                Rp = denom / Rs if Rs != 0 else float('inf')
                Cp = -Xc / (self.OMEGA * denom) if denom != 0 else float('inf')
                result.update({
                    'Q': Q, 'X': Xc, 'Z': abs(Z), 'theta': theta,
                    'Rs': Rs, 'Cs': Cs, 'Rp': Rp, 'Cp': Cp,
                    'G': 1.0/Rp if Rp != 0 and Rp != float('inf') else 0,
                    'Ls': 0, 'Lp': 0,
                })
        elif self._mode == 'LR':
            Ls = pc
            Rs = ac
            result.update({'L': Ls, 'R': Rs, 'primary': Ls, 'secondary': Rs})
            if Ls != 0:
                Xl = self.OMEGA * Ls
                Q = Xl / Rs if Rs != 0 else float('inf')
                D = 1.0 / Q if Q != 0 and Q != float('inf') else float('inf')
                Z = complex(Rs, Xl)
                theta = math.degrees(math.atan2(Xl, Rs)) if Rs != 0 else 90
                denom = Rs**2 + Xl**2
                Rp = denom / Rs if Rs != 0 else float('inf')
                Lp = denom / (self.OMEGA**2 * Ls) if (denom != 0 and Ls != 0) else float('inf')
                result.update({
                    'Q': Q, 'D': D, 'X': Xl, 'Z': abs(Z), 'theta': theta,
                    'Rs': Rs, 'Ls': Ls, 'Rp': Rp, 'Lp': Lp,
                    'Cs': 0, 'Cp': 0,
                })
        elif self._mode == 'LD':
            Lp = pc
            D = ac
            result.update({'L': Lp, 'D': D, 'primary': Lp, 'secondary': D})
            if Lp != 0:
                Xl = self.OMEGA * Lp
                Rp = Xl / D if D != 0 else float('inf')
                Q = 1.0 / D if D != 0 else float('inf')
                Z = 1.0 / complex(1.0/Rp if Rp != float('inf') else 0, -1.0/Xl) if Xl != 0 else complex(float('inf'), 0)
                theta = math.degrees(math.atan2(Xl, Rp)) if Rp != 0 and Rp != float('inf') else 90
                denom = (1.0/Rp if Rp != float('inf') and Rp != 0 else 0)**2 + (1.0/Xl)**2 if Xl != 0 else 0
                Rs = (1.0/Rp if Rp != float('inf') and Rp != 0 else 0) / denom if denom != 0 else 0
                Ls = (1.0/Xl) / (self.OMEGA * denom) if (denom != 0 and Xl != 0) else 0
                result.update({
                    'Q': Q, 'X': Xl, 'Z': abs(Z), 'theta': theta,
                    'Rp': Rp, 'Lp': Lp, 'Rs': Rs, 'Ls': Ls,
                    'Cs': 0, 'Cp': 0,
                })
        return result

    def _merge_result(self, current: Dict[str, Any], m_pc: E712Measurement,
                      m_ac: E712Measurement) -> Dict[str, Any]:
        """Объединяет текущий результат с новым для усреднения."""
        new = self._build_result(m_pc, m_ac)
        count = current.get('count', 1) + 1
        for key in ['primary', 'secondary', 'C', 'G', 'D', 'L', 'R', 'Q', 'X', 'Z', 'theta', 'Rs', 'Rp', 'Cs', 'Cp', 'Ls', 'Lp']:
            if key in current and key in new:
                current[key] = (current[key] * (count - 1) + new[key]) / count
            elif key in new:
                current[key] = new[key]
        current['count'] = count
        return current

    def measure_once(self) -> Dict[str, Any]:
        """Однократное измерение (только separate-паттерн).

        Е7-12 НЕ выдаёт данные автоматически после ЗАП (0x08).
        Данные пишутся в ОЗУ Мс15 (PC) и Мс16 (AC).
        Для чтения нужно:
          1. Послать 0x38 (Вывод РС) -- селектор ОЗУ Мс15
          2. Адаптер адресует прибор как TALKER -> читаем 11 байт
          3. Послать 0x30 (Вывод АС) -- селектор ОЗУ Мс16
          4. Адаптер адресует прибор как TALKER -> читаем 11 байт

        Адаптер UsbGpib V2 сам выполняет GPIB-handshake (DAV/NRFD/NDAC/EOI)
        и возвращает данные только после EOI -- sleep не нужен.
        """

        # 1. Setup + ЗАП одним bulk transfer
        cmds = [E712_MODE_CODES[self._mode]]

        if isinstance(self._range, str) and self._range.upper() == 'AUTO':
            cmds.append(E712_RANGE_CODES['AUTO'])
        else:
            cmds.append(E712_RANGE_CODES[int(self._range)])

        cmds.append(E712_LEVEL_CODES['x1' if self._level_high else 'x0.1'])

        if self._bias_on and getattr(self, '_bias_value', 0) > 0:
            cmds.append(E712_BIAS_CODES[self._bias_type])
            if BIAS_ENABLE_VALUE_CMD:
                self._append_bias_cmds(cmds, self._bias_value)

        self.driver.write_command_bytes(cmds, label='SETUP')
        self.driver.assert_trigger()   # ЗАП = GET с ATN; счётчик тактов -> 0

        # Синхронизация — сигналами шины, без стенных часов: адресованный как
        # передатчик прибор выставляет СД (DAV) по готовности данных, чтение
        # блокируется handshake'ом до КП (EOI). Таймаут VISA — граница ошибки
        # (паспортное время цикла 0.15..0.5 с), не средство ожидания.

        # 2. Чтение по handshake -- адаптер сам ждёт DAV/EOI от прибора
        # PC -- реактивная составляющая (ОЗУ Мс15)
        self.driver.write_command_byte(E712_OUTPUT_CODES['PC'], label='SELECT_PC')
        raw_pc = self.driver.read_raw(max_bytes=11, label='PC')

        # AC -- активная составляющая (ОЗУ Мс16)
        self.driver.write_command_byte(E712_OUTPUT_CODES['AC'], label='SELECT_AC')
        raw_ac = self.driver.read_raw(max_bytes=11, label='AC')

        m_pc = safe_first_measurement(raw_pc) if raw_pc else E712Measurement()
        m_ac = safe_first_measurement(raw_ac) if raw_ac else E712Measurement()

        self._last_result = self._build_result(m_pc, m_ac)
        self._last_result['raw_pc'] = (raw_pc or b'').decode('ascii', 'replace').strip() \
            if isinstance(raw_pc, (bytes, bytearray)) else str(raw_pc or '').strip()
        self._last_result['raw_ac'] = (raw_ac or b'').decode('ascii', 'replace').strip() \
            if isinstance(raw_ac, (bytes, bytearray)) else str(raw_ac or '').strip()
        if self.csv_sink is not None:
            try:
                self.csv_sink(self._last_result)
            except Exception as e:
                logging.warning('CSV sink: %s', e)
        return self._last_result

    def measure_averaged(self, count: int = 1) -> Dict[str, Any]:
        """Измерение с усреднением по count запускам."""
        count = min(max(1, count), self.MAX_AVERAGES)
        results = []
        for _ in range(count):
            # Темп задаёт сам прибор: каждый measure_once = ЗАП + чтение по
            # handshake, следующий ЗАП уходит только после КП предыдущего вывода.
            results.append(self.measure_once())
        avg: Dict[str, Any] = {'count': count}
        for key in ['primary', 'secondary', 'C', 'G', 'D', 'L', 'R', 'Q', 'X', 'Z', 'theta', 'Rs', 'Rp', 'Cs', 'Cp', 'Ls', 'Lp']:
            vals = [r[key] for r in results if key in r and r[key] is not None]
            if vals:
                avg[key] = sum(vals) / len(vals)
        if results:
            last = results[-1]
            avg['raw'] = f"averaged n={count}"
            for k in ['C', 'G', 'D', 'L', 'R', 'Q']:
                if k in last and k not in avg:
                    avg[k] = last[k]
        return avg

    def read_latest_measurement(self) -> Dict[str, Any]:
        """Читает последние данные из ОЗУ прибора БЕЗ запуска нового цикла.
        Прибор должен быть в циклическом режиме или данные уже в Мс15/Мс16."""
        self.driver.write_command_byte(E712_OUTPUT_CODES['PC'], label='SELECT_PC')
        raw_pc = self.driver.read_raw(max_bytes=11, label='PC')
        self.driver.write_command_byte(E712_OUTPUT_CODES['AC'], label='SELECT_AC')
        raw_ac = self.driver.read_raw(max_bytes=11, label='AC')
        m_pc = safe_first_measurement(raw_pc) if raw_pc else E712Measurement()
        m_ac = safe_first_measurement(raw_ac) if raw_ac else E712Measurement()
        return self._build_result(m_pc, m_ac)

    def read_device_state(self) -> Dict[str, Any]:
        """Читает статусный байт (STB) и текущие настройки прибора."""
        stb = self.driver.read_stb()
        state = {
            'STB': stb,
            'SRQ': bool(stb & 0x40),
            'MAV': bool(stb & 0x10),
            'bias_type': getattr(self, '_bias_type', None),
            'bias_value': getattr(self, '_bias_value', 0),
            'bias_on': getattr(self, '_bias_on', False),
            'mode': getattr(self, '_mode', None),
            'range': getattr(self, '_range', None),
            'level_high': getattr(self, '_level_high', False),
        }
        return state

    def get_last_result(self) -> Optional[Dict[str, Any]]:
        return self._last_result

    def close(self):
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================

def load_config(config_file=CONFIG_FILE):
    config = configparser.ConfigParser()
    if os.path.exists(config_file):
        config.read(config_file, encoding='utf-8')
    if 'DEFAULT' not in config:
        config['DEFAULT'] = {}
    if 'CODES' not in config:
        config['CODES'] = {}
    if 'LOG' not in config:
        config['LOG'] = {}
    return config


def save_config(config, config_file=CONFIG_FILE):
    with open(config_file, 'w', encoding='utf-8') as f:
        config.write(f)


def get_config_int(config, section, key, default=0):
    try:
        return config.getint(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return default


def get_config_bool(config, section, key, default=False):
    try:
        return config.getboolean(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return default


def get_config_str(config, section, key, default=''):
    try:
        return config.get(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError):
        return default


# =============================================================================
# ФОРМАТТЕР СЫРЫХ БАЙТ (для отладочного лога)
# =============================================================================
# Поддерживает представления: ASCII, DEC, HEX, BIN, BCD, 8-4-2-1
# с прямым (big-endian, MSB first) и обратным (little-endian, LSB first) порядком битов.
# Подробнее о порядке байтов/битов:
#   https://betterexplained.com/articles/understanding-big-and-little-endian-byte-order/

def format_byte(byte_val: int, fmt: str = 'hex', bit_order: str = 'msb') -> str:
    """Форматирует один байт в заданное представление.

    :param byte_val: 0..255
    :param fmt: 'ascii' | 'dec' | 'hex' | 'bin' | 'bcd' | '8421'
    :param bit_order: 'msb' (big-endian, бит 7 слева) или 'lsb' (little-endian, бит 0 слева)
    :return: строковое представление
    """
    if not 0 <= byte_val <= 255:
        return f'?{byte_val}?'

    if fmt == 'ascii':
        if 32 <= byte_val < 127:
            return f"'{chr(byte_val)}'"
        else:
            return f"·"   # непечатный

    if fmt == 'dec':
        return f'{byte_val:3d}'

    if fmt == 'hex':
        return f'0x{byte_val:02X}'

    if fmt == 'bin':
        bits = f'{byte_val:08b}'
        if bit_order == 'lsb':
            bits = bits[::-1]   # разворачиваем порядок битов
        return bits

    if fmt == 'bcd':
        # BCD (binary-coded decimal): старшая тетрада = одна цифра, младшая = другая
        hi = (byte_val >> 4) & 0x0F
        lo = byte_val & 0x0F
        if hi > 9 or lo > 9:
            return f'BCD?({hi:X},{lo:X})'
        return f'{hi}{lo}'

    if fmt == '8421':
        # Код 8-4-2-1: каждая тетрада — цифра с весами 8,4,2,1
        # Выводим обе тетрады с указанием весов
        hi = (byte_val >> 4) & 0x0F
        lo = byte_val & 0x0F
        hi_val = (8 if hi & 8 else 0) + (4 if hi & 4 else 0) + (2 if hi & 2 else 0) + (1 if hi & 1 else 0)
        lo_val = (8 if lo & 8 else 0) + (4 if lo & 4 else 0) + (2 if lo & 2 else 0) + (1 if lo & 1 else 0)
        return f'[{hi_val},{lo_val}]'

    return f'?{fmt}?'


def format_bytes(data: bytes, fmt: str = 'hex', bit_order: str = 'msb',
                 separator: str = ' ') -> str:
    """Форматирует последовательность байт.

    :param data: сырые байты
    :param fmt: 'ascii' | 'dec' | 'hex' | 'bin' | 'bcd' | '8421'
    :param bit_order: 'msb' или 'lsb'
    :param separator: разделитель между байтами
    :return: строка
    """
    return separator.join(format_byte(b, fmt, bit_order) for b in data)


def format_bytes_all(data: bytes, bit_order: str = 'msb') -> str:
    """Возвращает ВСЕ представления байт (для подробного лога).

    Формат вывода (одна строка на байт):
      byte 0: HEX=0x2B ASCII='+' DEC=43  BIN=00101011  BCD=2,B 8421=[2,11]
    """
    if not data:
        return '<пусто>'
    lines = []
    for i, b in enumerate(data):
        parts = [f'[{i:2d}]']
        for fmt in ['hex', 'ascii', 'dec', 'bin', 'bcd', '8421']:
            parts.append(f'{fmt.upper()}={format_byte(b, fmt, bit_order)}')
        lines.append(' '.join(parts))
    return '\n'.join(lines)


def log_raw_bytes(direction: str, data: bytes, log: logging.Logger,
                  bit_order: str = 'msb', label: str = ''):
    """Логирует сырые байты во всех форматах.

    :param direction: 'TX' (отправлено) или 'RX' (получено)
    :param data: сырые байты
    :param log: логгер
    :param bit_order: 'msb' или 'lsb'
    :param label: дополнительная метка (например, 'PC' или 'AC')
    """
    if not data:
        return
    arrow = '>>>' if direction == 'TX' else '<<<'
    label_str = f' [{label}]' if label else ''
    log.info(f'{arrow} {direction} RAW{label_str} ({len(data)} bytes):')
    log.info(f'    HEX  ({bit_order}): {format_bytes(data, "hex", bit_order)}')
    log.info(f'    ASCII:           {format_bytes(data, "ascii", "msb")}')
    log.info(f'    DEC:             {format_bytes(data, "dec", "msb")}')
    log.info(f'    BIN  ({bit_order}): {format_bytes(data, "bin", bit_order)}')
    log.info(f'    BCD:             {format_bytes(data, "bcd", "msb")}')
    log.info(f'    8421:            {format_bytes(data, "8421", "msb")}')


def save_to_csv_safe(filename, data, headers):
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(data)


# =============================================================================
# ПОТОК ИЗМЕРЕНИЯ
# =============================================================================

class MeasurementThread(threading.Thread):
    """Поток для выполнения длительных операций без блокировки GUI."""

    def __init__(self, target, args=(), kwargs=None, callback_queue=None):
        super().__init__()
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.callback_queue = callback_queue
        self._stop_event = threading.Event()
        self.daemon = True

    def stop(self):
        self._stop_event.set()

    def stopped(self):
        return self._stop_event.is_set()

    def run(self):
        try:
            kwargs = self.kwargs.copy()
            kwargs['stop_event'] = self._stop_event
            result = self.target(**kwargs)
            if self.callback_queue:
                self.callback_queue.put(('done', result))
        except Exception as e:
            if self.callback_queue:
                self.callback_queue.put(('error', str(e)))
        finally:
            # Смещение = 0 при ЛЮБОМ исходе (завершение, стоп, исключение):
            # включённое смещение при заземлённой линии U/I СМЕЩ. сжигает Пр9.
            ctrl = getattr(self, 'controller', None)
            if ctrl is not None:
                try:
                    if getattr(ctrl, '_bias_on', False):
                        ctrl.set_bias_off()
                except Exception:
                    pass


# (GUI будет в части 2 — см. продолжение файла)


# =============================================================================
# ГЛАВНЫЙ КЛАСС GUI
# =============================================================================

class SessionCSV:
    """Сырой CSV сессии: одна строка = одно реальное измерение прибора,
    всё выражение целиком — условия, сырые сообщения КОП, разобранные
    величины в базовых единицах СИ. Файл открывается при первой строке,
    пишется с flush, закрывается при выходе. Имя: e712_session_ГГГГММДД_ЧЧММСС.csv
    в папке скрипта. Кодировка utf-8-sig (читается Excel), разделитель ';'.
    """
    COLUMNS = [
        'timestamp', 't_s', 'context', 'mode', 'range', 'level',
        'bias_type', 'bias_set', 'raw_PC', 'raw_AC',
        'primary_name', 'primary_SI', 'secondary_name', 'secondary_SI',
        'C_F', 'G_S', 'D', 'L_H', 'R_Ohm', 'Q', 'Z_Ohm',
    ]

    def __init__(self, delimiter=';'):
        self.delimiter = delimiter
        self.enabled = True
        self.rows = []            # буфер сессии для ручного экспорта
        self._fh = None
        self._writer = None
        self._t0 = time.time()
        self.path = None

    def _open(self):
        base = os.path.dirname(os.path.abspath(__file__))
        name = 'e712_session_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.csv'
        self.path = os.path.join(base, name)
        self._fh = open(self.path, 'w', newline='', encoding='utf-8-sig')
        self._writer = csv.writer(self._fh, delimiter=self.delimiter)
        self._writer.writerow(self.COLUMNS)
        self._fh.flush()
        logging.info('CSV сессии: %s', self.path)

    @staticmethod
    def _num(v):
        if v is None:
            return ''
        try:
            return repr(float(v))
        except (TypeError, ValueError):
            return str(v)

    def make_row(self, res, ctrl):
        mode = getattr(ctrl, '_mode', '')
        p_name = {'C': 'C', 'L': 'L'}.get(mode[:1], '')
        s_name = {'G': 'G', 'D': 'D', 'R': 'R'}.get(mode[1:2], '')
        bias_on = getattr(ctrl, '_bias_on', False)
        return [
            datetime.now().isoformat(timespec='milliseconds'),
            f'{time.time() - self._t0:.3f}',
            getattr(ctrl, 'csv_context', ''),
            mode,
            str(getattr(ctrl, '_range', '')),
            'x1' if getattr(ctrl, '_level_high', True) else 'x0.1',
            getattr(ctrl, '_bias_type', '') if bias_on else '',
            self._num(getattr(ctrl, '_bias_value', 0) if bias_on else 0),
            res.get('raw_pc', ''),
            res.get('raw_ac', ''),
            p_name, self._num(res.get('primary')),
            s_name, self._num(res.get('secondary')),
            self._num(res.get('C')), self._num(res.get('G')),
            self._num(res.get('D')), self._num(res.get('L')),
            self._num(res.get('R')), self._num(res.get('Q')),
            self._num(res.get('Z')),
        ]

    def add(self, res, ctrl):
        row = self.make_row(res, ctrl)
        self.rows.append(row)
        if self.enabled:
            if self._fh is None:
                self._open()
            self._writer.writerow(row)
            self._fh.flush()

    def export_copy(self, path):
        with open(path, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.writer(fh, delimiter=self.delimiter)
            w.writerow(self.COLUMNS)
            w.writerows(self.rows)

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None


class E712GUI:
    """Единый GUI для Е7-12 (адаптированный из F0.2 под VISA-драйвер)."""

    def __init__(self, root):
        self.root = root
        self.root.title(f"E7-12 — КОП (v{__version__})")
        self.root.geometry("1023x600")   # Требование: помещаться в 1023x600
        self.root.minsize(1023, 600)
        self.root.minsize(1024, 600)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.config = load_config()
        log = logging.getLogger('E712_GUI')
        log.info('Конфигурация загружена из: %s', CONFIG_FILE)
        for sec in self.config.sections():
            for k, v in self.config.items(sec):
                log.info('  [%s] %s = %s', sec, k, v)

        # Состояние
        self.e712 = None
        self.measurement_thread = None
        self.sorting_thread = None
        self.callback_queue = queue.Queue()
        csv_delim = ';'
        if 'CSV' in self.config:
            csv_delim = self.config['CSV'].get('delimiter', ';').strip() or ';'
        self.session_csv = SessionCSV(delimiter=csv_delim)
        self.csv_auto_var = None   # создаётся в меню
        self.sorting_active = False
        self.stop_sorting_flag = threading.Event()
        self.sort_threshold = None

        # Построение UI
        self.create_menu()
        self.create_connection_frame()
        self.create_notebook()
        self.create_status_bar()

        self.update_gui_from_config()
        self.process_callbacks()

    # ------------------------------------------------------------------
    # Меню
    # ------------------------------------------------------------------
    def create_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Файл", menu=file_menu)
        file_menu.add_command(label="Сохранить конфигурацию", command=self.save_config_gui)
        file_menu.add_separator()
        self.csv_auto_var = tk.BooleanVar(value=True)
        file_menu.add_checkbutton(label="Сырой экспорт CSV (авто)",
                                  variable=self.csv_auto_var,
                                  command=self._toggle_csv_auto)
        file_menu.add_command(label="Сырой экспорт CSV…",
                              command=self._export_csv_copy)
        file_menu.add_separator()
        file_menu.add_command(label="Выход", command=self.on_closing)
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Справка", menu=help_menu)
        help_menu.add_command(label="О программе", command=self.show_about)

    def save_config_gui(self):
        self.config['DEFAULT']['gpib_addr'] = str(self.gpib_var.get())
        self.config['DEFAULT']['debug_mode'] = str(int(self.debug_mode_var.get()))
        save_config(self.config)
        messagebox.showinfo("Конфигурация", "Настройки сохранены.")

    def show_about(self):
        messagebox.showinfo("О программе",
            f"E7-12 — управление по КОП\n"
            f"Версия {__version__} от {__date__}\n\n"
            f"UsbGpib V2 + pyvisa + реальный Е7-12\n"
            f"Программное смещение через КОП (коды 8-4-2-1)\n")
      

    # ------------------------------------------------------------------
    # Панель подключения
    # ------------------------------------------------------------------
    def create_connection_frame(self):
        frame = ttk.LabelFrame(self.root, text="Подключение к прибору", padding=5)
        frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(frame, text="GPIB адрес:").grid(row=0, column=0, padx=5, sticky=tk.W)
        self.gpib_var = tk.IntVar(value=DEFAULT_GPIB_ADDR)
        ttk.Spinbox(frame, from_=1, to=30, textvariable=self.gpib_var, width=5
                    ).grid(row=0, column=1, padx=5)

        self.conn_status_label = ttk.Label(frame, text="○ Не подключено", foreground="red")
        self.conn_status_label.grid(row=0, column=2, padx=10)

        self.debug_mode_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Режим отладки",
                        variable=self.debug_mode_var,
                        command=self.toggle_debug).grid(row=0, column=3, padx=5)

        # Кнопки «Состояние прибора» и «Прочитать измерение» удалены:
        # регистры прибора write-only, чтение ОЗУ без ЗАП возвращает прошлый
        # отсчёт — то же самое даёт «Измерить». Вместо них — теневое состояние:
        # истина о приборе = последнее отправленное.
        self.state_label = ttk.Label(frame, text="состояние: —", foreground="gray")
        self.state_label.grid(row=0, column=4, columnspan=2, padx=10, sticky=tk.W)

        # Флаг подключения (ленивое подключение при первом измерении)
        self._connected = False

    def _toggle_csv_auto(self):
        self.session_csv.enabled = bool(self.csv_auto_var.get())
        state = 'ВКЛ' if self.session_csv.enabled else 'ВЫКЛ'
        self.log_message(f'Сырой CSV (авто): {state}'
                         + (f' -> {self.session_csv.path}' if self.session_csv.path else ''))

    def _export_csv_copy(self):
        if not self.session_csv.rows:
            messagebox.showinfo('CSV', 'В сессии ещё нет измерений.')
            return
        path = filedialog.asksaveasfilename(
            defaultextension='.csv',
            initialfile='e712_export_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.csv',
            filetypes=[('CSV', '*.csv')])
        if path:
            self.session_csv.export_copy(path)
            self.log_message(f'CSV экспортирован: {path} ({len(self.session_csv.rows)} строк)')

    def ensure_connected(self):
        """Ленивое подключение к прибору при первом измерении."""
        if self._connected and self.e712 is not None:
            return True
        try:
            gpib = self.gpib_var.get()
            resources = list_visa_resources()
            if not resources:
                raise RuntimeError("UsbGpib адаптер не найден")
            resource = resources[0]
            self.e712 = E712Controller(
                gpib_addr=gpib,
                resource_string=resource,
                debug=self.debug_mode_var.get(),
                config=self.config,
            )
            ver = self.e712.driver.get_adapter_version()
            self.conn_status_label.config(
                text=f"● Подключено ({ver})", foreground="green"
            )
            self.log_message(f"Подключено: {resource}")
            self._connected = True
            self.e712.csv_sink = lambda res: self.session_csv.add(res, self.e712)
            self.refresh_state_label()
            return True
        except Exception as e:
            self.conn_status_label.config(
                text="○ Ошибка подключения", foreground="red"
            )
            self.log_message(f"Ошибка подключения: {e}")
            return False

    def toggle_debug(self):
        """Режим отладки: зеркалирует побайтовый обмен (логгер E712_RAW,
        TX/RX с HEX/ASCII и временами handshake) в журнал GUI."""
        raw = logging.getLogger('E712_RAW')
        if self.debug_mode_var.get():
            if getattr(self, '_gui_log_handler', None) is None:
                gui = self
                class _QH(logging.Handler):
                    def emit(self, record):
                        try:
                            gui.callback_queue.put(('log', self.format(record)))
                        except Exception:
                            pass
                h = _QH()
                h.setFormatter(logging.Formatter('%(message)s'))
                self._gui_log_handler = h
            raw.addHandler(self._gui_log_handler)
            raw.setLevel(logging.INFO)
            self.log_message('Отладка ВКЛ: обмен TX/RX дублируется в журнал')
        else:
            if getattr(self, '_gui_log_handler', None) is not None:
                raw.removeHandler(self._gui_log_handler)
            self.log_message('Отладка ВЫКЛ')

    def refresh_state_label(self):
        """Теневое состояние: то, что фактически отправлено в регистры."""
        e7 = self.e712
        if not e7:
            self.state_label.config(text='состояние: —', foreground='gray')
            return
        mode = getattr(e7, '_mode', '?')
        rng = getattr(e7, '_range', '?')
        lvl = 'x1' if getattr(e7, '_level_high', True) else 'x0.1'
        if getattr(e7, '_bias_on', False):
            bias = f"{getattr(e7, '_bias_value', 0):.1f} {getattr(e7, '_bias_type', 'V')}"
        else:
            bias = '0'
        self.state_label.config(
            text=f'отправлено: {mode}  предел {rng}  {lvl}  смещ {bias}',
            foreground='black')

    # Notebook и вкладки
    # ------------------------------------------------------------------
    def create_notebook(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.create_single_measure_tab()
        self.create_cv_tab()
        self.create_li_tab()
        self.create_three_term_tab()
        self.create_prepare_tab()
        self.create_sorting_tab()
        self.create_log_tab()

        # Все вкладки доступны всегда (блокируем только кнопки измерений при отсутствии связи)

    def create_status_bar(self):
        self.status_bar = ttk.Label(self.root, text="Готов",
                                    relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    # ------------------------------------------------------------------
    # Очередь callback'ов
    # ------------------------------------------------------------------
    def process_callbacks(self):
        try:
            while True:
                msg_type, data = self.callback_queue.get_nowait()
                if msg_type == 'done':
                    self.on_measurement_done(data)
                elif msg_type == 'error':
                    messagebox.showerror("Ошибка", data)
                    self.set_status("Ошибка измерения")
                elif msg_type == 'progress':
                    self.update_progress(data)
                elif msg_type == 'timer':
                    self.update_warmup_timer(data)
                elif msg_type == 'log':
                    self.log_message(data)
                elif msg_type == 'sorting_result':
                    self.update_sorting_result(data)
                elif msg_type == 'cv_data':
                    self.cv_data.append(data)
                    self.cv_tree.insert('', tk.END,
                        values=(f"{data[0]:.1f}", f"{data[1]:.4e}", f"{data[2]:.4e}"))
                elif msg_type == 'li_data':
                    self.li_data.append(data)
                    self.li_tree.insert('', tk.END,
                        values=(f"{data[0]:.1f}", f"{data[1]:.4e}", f"{data[2]:.4e}"))
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self.process_callbacks)

    def on_measurement_done(self, data):
        self.refresh_state_label()
        if not data:
            return
        if isinstance(data, dict):
            data_type = data.get('type', '')
            if data_type == 'CV':
                self.cv_measurement_running = False
                self.cv_start_btn.config(state=tk.NORMAL)
                self.cv_stop_btn.config(state=tk.DISABLED)
                self.set_status("C-V измерение завершено")
                self.log_message("C-V характеристика измерена")
            elif data_type == 'LI':
                self.li_measurement_running = False
                self.li_start_btn.config(state=tk.NORMAL)
                self.li_stop_btn.config(state=tk.DISABLED)
                self.set_status("L-I измерение завершено")
                self.log_message("L-I характеристика измерена")
            elif data_type == 'three' and 'result' in data:
                res = data['result']
                params = data.get('params', {})
                mode = params.get('mode', 'CG')
                self.display_three_result(res, mode)
                self.set_status("Измерение завершено")
            elif 'result' in data:
                res = data['result']
                params = data.get('params', {})
                mode = params.get('mode', 'CG')
                if 'averages' in params:
                    self.display_single_result(res, mode)
                self.set_status("Измерение завершено")
            elif data_type == 'warmup':
                self.warmup_btn.config(state=tk.NORMAL)
                self.warmup_stop_btn.config(state=tk.DISABLED)
                self.warmup_progress['value'] = 100
                self.warmup_timer_label.config(text="00:00")
                self.log_message("Прогрев завершён")
                messagebox.showinfo("Прогрев", "Прогрев прибора завершён.")
            elif data_type == 'threshold':
                self.sort_threshold = data.get('threshold')
                if hasattr(self, 'threshold_label'):
                    self.threshold_label.config(
                        text=f"Порог: {self.sort_threshold:.3e}"
                    )
                self.set_status("Калибровка завершена")
            else:
                self.log_message("Измерение завершено")
        else:
            self.log_message("Измерение завершено")

    def update_progress(self, value):
        if hasattr(self, 'cv_progress') and self.cv_progress.winfo_exists():
            self.cv_progress['value'] = value
        if hasattr(self, 'warmup_progress') and self.warmup_progress.winfo_exists():
            self.warmup_progress['value'] = value
        if hasattr(self, 'li_progress') and self.li_progress.winfo_exists():
            self.li_progress['value'] = value
        self.root.update_idletasks()

    def update_warmup_timer(self, remaining_seconds):
        if hasattr(self, 'warmup_timer_label') and self.warmup_timer_label.winfo_exists():
            minutes = remaining_seconds // 60
            seconds = remaining_seconds % 60
            self.warmup_timer_label.config(text=f"{minutes:02d}:{seconds:02d}")

    def log_message(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_text.see(tk.END)

    def set_status(self, text):
        self.status_bar.config(text=text)
        self.root.update_idletasks()

    def update_gui_from_config(self):
        self.gpib_var.set(get_config_int(self.config, 'DEFAULT', 'gpib_addr',
                                          DEFAULT_GPIB_ADDR))
        self.debug_mode_var.set(get_config_bool(self.config, 'DEFAULT',
                                                 'debug_mode', False))

    def start_measurement_thread(self, target, kwargs):
        if self.measurement_thread and self.measurement_thread.is_alive():
            messagebox.showwarning("Внимание", "Измерение уже выполняется")
            return
        self.measurement_thread = MeasurementThread(
            target=target, kwargs=kwargs, callback_queue=self.callback_queue
        )
        self.measurement_thread.controller = self.e712   # для finally: смещение 0
        self.measurement_thread.start()

    def stop_measurement(self):
        if self.measurement_thread and self.measurement_thread.is_alive():
            self.measurement_thread.stop()
            self.set_status("Остановка измерения...")

    def on_closing(self):
        # Автополлинг удалён
        if self.measurement_thread and self.measurement_thread.is_alive():
            self.measurement_thread.stop()
            self.measurement_thread.join(timeout=1.0)
        if self.sorting_thread and self.sorting_thread.is_alive():
            self.stop_sorting_flag.set()
            self.sorting_thread.join(timeout=1.0)
        if self.e712:
            try:
                self.e712.close()
            except Exception:
                pass
        try:
            self.session_csv.close()
        except Exception:
            pass
        self.root.destroy()

    # ------------------------------------------------------------------
    # Вкладка: Однократное измерение
    # ------------------------------------------------------------------
    def create_single_measure_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Однократное")

        frame = ttk.LabelFrame(tab, text="Параметры измерения", padding=3)
        frame.pack(fill=tk.X, padx=3, pady=3)

        # Row 0: Режим, Предел, Уровень
        ttk.Label(frame, text="Режим:").grid(row=0, column=0, sticky=tk.W, padx=2, pady=1)
        self.single_mode = tk.StringVar(value="CG")
        ttk.Combobox(frame, textvariable=self.single_mode,
                     values=['CG', 'CD', 'LR', 'LD'],
                     state='readonly', width=4).grid(row=0, column=1, padx=2, pady=1)

        ttk.Label(frame, text="Предел:").grid(row=0, column=2, sticky=tk.W, padx=2, pady=1)
        self.single_range = tk.StringVar(value="AUTO")
        ttk.Combobox(frame, textvariable=self.single_range,
                     values=['AUTO', '1', '2', '3', '4', '5'],
                     state='readonly', width=4).grid(row=0, column=3, padx=2, pady=1)

        self.single_level = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Выс.уровень",
                        variable=self.single_level).grid(row=0, column=4, padx=3, pady=1)

        # Row 1: Смещение, Усреднение
        ttk.Label(frame, text="Смещение:").grid(row=1, column=0, sticky=tk.W, padx=2, pady=1)
        self.single_bias_type = tk.StringVar(value="V")
        ttk.Combobox(frame, textvariable=self.single_bias_type,
                     values=['V', 'mA'],
                     state='readonly', width=4).grid(row=1, column=1, padx=2, pady=1)
        self.single_bias_value = tk.DoubleVar(value=0.0)
        ttk.Entry(frame, textvariable=self.single_bias_value, width=6).grid(
            row=1, column=2, padx=2, pady=1
        )

        ttk.Label(frame, text="Усредн.:").grid(row=1, column=3, sticky=tk.W, padx=2, pady=1)
        self.single_avg = tk.IntVar(value=1)
        ttk.Spinbox(frame, from_=1, to=MAX_AVERAGES,
                    textvariable=self.single_avg, width=4).grid(
            row=1, column=4, padx=2, pady=1
        )

        btn_frame = ttk.Frame(tab)
        btn_frame.pack(fill=tk.X, padx=3, pady=2)
        ttk.Button(btn_frame, text="Измерить",
                   command=self.run_single_measurement).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text="Сохранить",
                   command=self.save_single_result).pack(side=tk.LEFT, padx=3)

        fmt_frame = ttk.Frame(tab)
        fmt_frame.pack(fill=tk.X, padx=3, pady=1)
        ttk.Label(fmt_frame, text="Формат:").pack(side=tk.LEFT, padx=2)
        self.display_format = tk.StringVar(value='SCI')
        for fmt_label, fmt_val in [('SCI', 'SCI'), ('ENG', 'ENG'), ('СИ', 'SI2019'), ('Табло', 'PANEL')]:
            ttk.Radiobutton(fmt_frame, text=fmt_label, variable=self.display_format,
                            value=fmt_val, command=self._refresh_display
                            ).pack(side=tk.LEFT, padx=3)

        result_frame = ttk.LabelFrame(tab, text="Результат", padding=3)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)
        self.single_result_text = tk.Text(result_frame, height=8, state=tk.DISABLED)
        self.single_result_text.pack(fill=tk.BOTH, expand=True)

        self.last_single_result = None

    def run_single_measurement(self):
        if not self.ensure_connected():
            messagebox.showerror("Ошибка", "Прибор не подключен или не найден")
            return
        params = {
            'mode': self.single_mode.get(),
            'range': self.single_range.get(),
            'level': self.single_level.get(),
            'bias_type': self.single_bias_type.get(),
            'bias_val': self.single_bias_value.get(),
            'averages': self.single_avg.get()
        }
        self.set_status("Выполняется однократное измерение...")
        self.start_measurement_thread(self._single_measurement_target, params)

    def _single_measurement_target(self, **kwargs):
        kwargs.pop('stop_event')
        e7 = self.e712
        e7.csv_context = 'single'
        e7.set_mode(kwargs['mode'])
        e7.set_range(kwargs['range'])
        e7.set_level(kwargs['level'])
        bias_type = kwargs['bias_type']
        bias_val = kwargs['bias_val']
        if bias_type in ('V', 'mA') and bias_val > 0:
            e7.apply_bias_guarded(bias_type, bias_val)   # предизмерение + допуск
        elif bias_type in ('V', 'mA'):
            e7._bias_type = bias_type
            e7.set_bias_off()
        else:
            # Если тип смещения не V и не mA — сбрасываем в 0.0 В (по умолчанию V)
            e7._bias_type = 'V'
            e7.set_bias_off()
        result = e7.measure_averaged(kwargs['averages'])
        return {'result': result, 'params': kwargs}

    @staticmethod
    def _range_hint(range_str):
        """'1'..'5' -> int, 'AUTO' -> None (формат Табло нормализует сам)."""
        try:
            return int(range_str)
        except (TypeError, ValueError):
            return None

    def display_single_result(self, res, mode):
        param_names = {
            'CG': ('Ёмкость C', 'Проводимость G'),
            'CD': ('Ёмкость C', 'Тангенс D'),
            'LR': ('Индуктивность L', 'Сопротивление R'),
            'LD': ('Индуктивность L', 'Тангенс D')
        }
        primary_label, secondary_label = param_names.get(mode, ('Primary', 'Secondary'))
        fmt = self.display_format.get()
        rng = self._range_hint(self.single_range.get())
        pchar, schar = mode[0], mode[1]           # 'C'/'L' и 'G'/'D'/'R'
        p_unit = {'C': 'F', 'L': 'H'}[pchar]
        s_unit = {'G': 'S', 'R': 'Ω', 'D': ''}[schar]
        fv = lambda v, u='', p='': format_value(v, u, fmt, rng=rng, param=p)

        lines = [
            f"━━━ Аппаратные величины ━━━",
            f"{primary_label}:  {fv(res.get('primary', 0), p_unit, pchar)}",
            f"{secondary_label}: {fv(res.get('secondary', 0), s_unit, schar)}",
            "",
            f"━━━ Вычисляемые величины ━━━",
        ]
        derived = [
            ('Q', 'Q', ''), ('D', 'D', ''), ('θ', 'theta', '°'),
            ('|Z|', 'Z', 'Ω'), ('X', 'X', 'Ω'),
            ('Rs', 'Rs', 'Ω'), ('Rp', 'Rp', 'Ω'),
            ('Cs', 'Cs', 'F'), ('Cp', 'Cp', 'F'),
            ('Ls', 'Ls', 'H'), ('Lp', 'Lp', 'H'),
        ]
        for label, key, unit in derived:
            val = res.get(key)
            if val is not None and val != 0:
                lines.append(f"{label:>4}: {fv(val, unit)}")

        self.single_result_text.config(state=tk.NORMAL)
        self.single_result_text.delete(1.0, tk.END)
        self.single_result_text.insert(tk.END, '\n'.join(lines))
        self.single_result_text.config(state=tk.DISABLED)
        self.last_single_result = res

    def _refresh_display(self):
        """Перерисовывает последние результаты в новом формате."""
        if getattr(self, 'last_single_result', None):
            self.display_single_result(self.last_single_result,
                                       self.single_mode.get())
        if getattr(self, 'last_three_result', None):
            self.display_three_result(self.last_three_result,
                                      self.three_mode.get())

    def save_single_result(self):
        if not self.last_single_result:
            messagebox.showwarning("Предупреждение", "Нет результата для сохранения")
            return
        filename = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV files", "*.csv")]
        )
        if not filename:
            return
        res = self.last_single_result
        mode = self.single_mode.get()
        headers = ['Mode', 'Range', 'Level', 'Bias', 'Primary', 'Secondary']
        bias_str = f"{self.single_bias_type.get()}={self.single_bias_value.get():.1f}"
        data = [[mode, self.single_range.get(),
                 'x1' if self.single_level.get() else 'x0.1', bias_str,
                 res.get('primary', 0), res.get('secondary', 0)]]
        save_to_csv_safe(filename, data, headers)
        messagebox.showinfo("Сохранение", f"Результат сохранён в {filename}")

    # ------------------------------------------------------------------
    # Вкладка: C-V характеристика
    # ------------------------------------------------------------------
    def create_cv_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="C-V характеристика")

        frame = ttk.LabelFrame(tab, text="Параметры сканирования", padding=3)
        frame.pack(fill=tk.X, padx=3, pady=3)

        # Row 0: U нач, U кон, Шаг
        ttk.Label(frame, text="U нач (В):").grid(row=0, column=0, sticky=tk.W, padx=2, pady=1)
        self.cv_start = tk.DoubleVar(value=0.0)
        ttk.Entry(frame, textvariable=self.cv_start, width=6).grid(row=0, column=1, padx=2, pady=1)

        ttk.Label(frame, text="U кон (В):").grid(row=0, column=2, sticky=tk.W, padx=2, pady=1)
        self.cv_stop = tk.DoubleVar(value=30.0)
        ttk.Entry(frame, textvariable=self.cv_stop, width=6).grid(row=0, column=3, padx=2, pady=1)

        ttk.Label(frame, text="Шаг (В):").grid(row=0, column=4, sticky=tk.W, padx=2, pady=1)
        self.cv_step = tk.DoubleVar(value=0.5)
        ttk.Entry(frame, textvariable=self.cv_step, width=6).grid(row=0, column=5, padx=2, pady=1)

        # Row 1: Предел, Уровень, Усреднение
        ttk.Label(frame, text="Предел:").grid(row=1, column=0, sticky=tk.W, padx=2, pady=1)
        self.cv_range = tk.StringVar(value="AUTO")
        ttk.Combobox(frame, textvariable=self.cv_range,
                     values=['AUTO','1','2','3','4','5'],
                     state='readonly', width=4).grid(row=1, column=1, padx=2, pady=1)

        self.cv_level = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Выс.уровень",
                        variable=self.cv_level).grid(row=1, column=2, padx=3, pady=1)

        ttk.Label(frame, text="Усредн.:").grid(row=1, column=3, sticky=tk.W, padx=2, pady=1)
        self.cv_avg = tk.IntVar(value=1)
        ttk.Spinbox(frame, from_=1, to=MAX_AVERAGES,
                    textvariable=self.cv_avg, width=4).grid(row=1, column=4, padx=2, pady=1)

        btn_frame = ttk.Frame(tab)
        btn_frame.pack(fill=tk.X, padx=3, pady=2)
        self.cv_start_btn = ttk.Button(btn_frame, text="Запуск",
                                        command=self.run_cv_measurement)
        self.cv_start_btn.pack(side=tk.LEFT, padx=3)
        self.cv_stop_btn = ttk.Button(btn_frame, text="Отмена",
                                       command=self.stop_cv_measurement,
                                       state=tk.DISABLED)
        self.cv_stop_btn.pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text="CSV",
                   command=self.save_cv_result).pack(side=tk.LEFT, padx=3)
        if PLOT_AVAILABLE:
            ttk.Button(btn_frame, text="График",
                       command=self.plot_cv_result).pack(side=tk.LEFT, padx=3)

        fmt_frame = ttk.Frame(tab)
        fmt_frame.pack(fill=tk.X, padx=3, pady=1)
        ttk.Label(fmt_frame, text="Формат:").pack(side=tk.LEFT, padx=2)
        if not hasattr(self, 'display_format'):
            self.display_format = tk.StringVar(value='SCI')
        for fmt_label, fmt_val in [('SCI', 'SCI'), ('ENG', 'ENG'), ('СИ', 'SI2019'), ('Табло', 'PANEL')]:
            ttk.Radiobutton(fmt_frame, text=fmt_label, variable=self.display_format,
                            value=fmt_val, command=self._refresh_display
                            ).pack(side=tk.LEFT, padx=3)

        self.cv_progress = ttk.Progressbar(tab, orient=tk.HORIZONTAL, mode='determinate')
        self.cv_progress.pack(fill=tk.X, padx=3, pady=3)

        tree_frame = ttk.Frame(tab)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)
        columns = ('U_bias', 'C', 'D')
        self.cv_tree = ttk.Treeview(tree_frame, columns=columns, show='headings', height=6)
        self.cv_tree.heading('U_bias', text='U смещения (В)')
        self.cv_tree.heading('C', text='Ёмкость (Ф)')
        self.cv_tree.heading('D', text='Тангенс D')
        for c, w in zip(columns, (100, 150, 150)):
            self.cv_tree.column(c, width=w, anchor='center')
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL,
                                   command=self.cv_tree.yview)
        self.cv_tree.configure(yscrollcommand=scrollbar.set)
        self.cv_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.cv_data = []
        self.cv_measurement_running = False

    def run_cv_measurement(self):
        if not self.ensure_connected():
            messagebox.showerror("Ошибка", "Прибор не подключен или не найден")
            return
        if self.cv_measurement_running:
            messagebox.showwarning("Внимание", "Измерение уже выполняется")
            return
        params = {
            'start': self.cv_start.get(),
            'stop': self.cv_stop.get(),
            'step': self.cv_step.get(),
            'range': self.cv_range.get(),
            'level': self.cv_level.get(),
            'averages': self.cv_avg.get()
        }
        if params['start'] >= params['stop']:
            messagebox.showerror("Ошибка",
                                 "Начальное напряжение должно быть меньше конечного")
            return
        self.cv_data.clear()
        for item in self.cv_tree.get_children():
            self.cv_tree.delete(item)
        self.cv_progress['value'] = 0
        self.cv_measurement_running = True
        self.cv_start_btn.config(state=tk.DISABLED)
        self.cv_stop_btn.config(state=tk.NORMAL)
        self.set_status("Запуск C-V измерения...")
        self.start_measurement_thread(self._cv_measurement_target, params)

    def stop_cv_measurement(self):
        if self.measurement_thread and self.measurement_thread.is_alive():
            self.measurement_thread.stop()
        self.cv_measurement_running = False
        self.cv_start_btn.config(state=tk.NORMAL)
        self.cv_stop_btn.config(state=tk.DISABLED)
        self.set_status("C-V измерение отменено")
        self.log_message("C-V измерение отменено пользователем")

    def _cv_measurement_target(self, **kwargs):
        stop_event = kwargs.pop('stop_event')
        e7 = self.e712
        e7.csv_context = 'C-V'
        e7.set_mode('CG')
        e7.set_level(kwargs['level'])
        if kwargs['range'] == 'AUTO':
            e7.set_range('AUTO')
        else:
            e7.set_range(int(kwargs['range']))
        e7.set_bias_off()

        voltages = []
        v = kwargs['start']
        while v <= kwargs['stop'] + 1e-9 and not stop_event.is_set():
            voltages.append(round(v, 1))
            v += kwargs['step']

        # Guard один раз, на максимальном напряжении скана
        v_max = max(voltages) if voltages else 0.0
        if v_max > 0:
            verdict = e7.preflight_bias('V', v_max)
            logging.info('BIAS GUARD (C-V, %.1f В): %s', v_max, verdict['reason'])
            if not verdict['allowed']:
                raise RuntimeError('C-V остановлен. ' + verdict['reason'])
        results = []
        total = len(voltages)
        d_min = None                       # минимум D данной развёртки
        for i, v in enumerate(voltages):
            if stop_event.is_set():
                break
            e7.set_bias_voltage(v)
            res = e7.measure_averaged(kwargs['averages'])
            d_now = res.get('D')
            if SCAN_GUARD_ENABLE and d_now and d_now > 0:
                if d_min is None or d_now < d_min:
                    d_min = d_now
                elif d_now > d_min * SCAN_GUARD_D_RATIO:
                    self.callback_queue.put(('log',
                        f'SCAN GUARD: стоп на {v:.1f} В — D={d_now:.4f} '
                        f'> {SCAN_GUARD_D_RATIO:g} x D_min={d_min:.4f}. '
                        f'Перегиб D = предвестник пробоя варикапа.'))
                    break
            c_val = res.get('primary', 0)
            d_val = res.get('D', 0)
            results.append((v, c_val, d_val))
            progress = int((i+1)/total*100)
            self.callback_queue.put(('progress', progress))
            self.callback_queue.put(('log',
                f"C-V: U={v:.1f} В, C={c_val:.3e} Ф, D={d_val:.4e}"))
            self.callback_queue.put(('cv_data', (v, c_val, d_val)))
        return {'results': results, 'params': kwargs, 'type': 'CV'}

    def save_cv_result(self):
        if not self.cv_data:
            messagebox.showwarning("Предупреждение", "Нет данных для сохранения")
            return
        filename = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV files", "*.csv")]
        )
        if not filename:
            return
        headers = ['U_bias (V)', 'C (F)', 'D']
        save_to_csv_safe(filename, self.cv_data, headers)
        messagebox.showinfo("Сохранение", f"Данные сохранены в {filename}")

    def plot_cv_result(self):
        if not PLOT_AVAILABLE:
            messagebox.showerror("Ошибка",
                "Библиотеки matplotlib/pandas не установлены")
            return
        if not self.cv_data:
            messagebox.showwarning("Предупреждение", "Нет данных для графика")
            return
        df = pd.DataFrame(self.cv_data, columns=['U', 'C', 'D'])
        win = tk.Toplevel(self.root)
        win.title("C-V характеристика")
        win.geometry("700x500")
        fig = Figure(figsize=(7, 5))
        ax1 = fig.add_subplot(111)
        ax1.plot(df['U'], df['C'] * 1e12, 'b-o', label='C, pF')
        ax1.set_xlabel('U смещения, В')
        ax1.set_ylabel('Ёмкость, pF', color='b')
        ax1.tick_params(axis='y', labelcolor='b')
        ax2 = ax1.twinx()
        ax2.plot(df['U'], df['D'], 'r-s', label='D')
        ax2.set_ylabel('Тангенс D', color='r')
        ax2.tick_params(axis='y', labelcolor='r')
        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=win)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------
    # Вкладка: L-I характеристика
    # ------------------------------------------------------------------
    def create_li_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="L-I характеристика")

        frame = ttk.LabelFrame(tab, text="Параметры сканирования", padding=3)
        frame.pack(fill=tk.X, padx=3, pady=3)

        # Row 0: I нач, I кон, Шаг
        ttk.Label(frame, text="I нач (мА):").grid(row=0, column=0, sticky=tk.W, padx=2, pady=1)
        self.li_start = tk.DoubleVar(value=0.0)
        ttk.Entry(frame, textvariable=self.li_start, width=6).grid(row=0, column=1, padx=2, pady=1)

        ttk.Label(frame, text="I кон (мА):").grid(row=0, column=2, sticky=tk.W, padx=2, pady=1)
        self.li_stop = tk.DoubleVar(value=30.0)
        ttk.Entry(frame, textvariable=self.li_stop, width=6).grid(row=0, column=3, padx=2, pady=1)

        ttk.Label(frame, text="Шаг (мА):").grid(row=0, column=4, sticky=tk.W, padx=2, pady=1)
        self.li_step = tk.DoubleVar(value=0.5)
        ttk.Entry(frame, textvariable=self.li_step, width=6).grid(row=0, column=5, padx=2, pady=1)

        # Row 1: Предел, Уровень, Усреднение
        ttk.Label(frame, text="Предел:").grid(row=1, column=0, sticky=tk.W, padx=2, pady=1)
        self.li_range = tk.StringVar(value="AUTO")
        ttk.Combobox(frame, textvariable=self.li_range,
                     values=['AUTO','1','2','3','4','5'],
                     state='readonly', width=4).grid(row=1, column=1, padx=2, pady=1)

        self.li_level = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Выс.уровень",
                        variable=self.li_level).grid(row=1, column=2, padx=3, pady=1)

        ttk.Label(frame, text="Усредн.:").grid(row=1, column=3, sticky=tk.W, padx=2, pady=1)
        self.li_avg = tk.IntVar(value=1)
        ttk.Spinbox(frame, from_=1, to=MAX_AVERAGES,
                    textvariable=self.li_avg, width=4).grid(row=1, column=4, padx=2, pady=1)

        btn_frame = ttk.Frame(tab)
        btn_frame.pack(fill=tk.X, padx=3, pady=2)
        self.li_start_btn = ttk.Button(btn_frame, text="Запуск",
                                        command=self.run_li_measurement)
        self.li_start_btn.pack(side=tk.LEFT, padx=3)
        self.li_stop_btn = ttk.Button(btn_frame, text="Отмена",
                                       command=self.stop_li_measurement,
                                       state=tk.DISABLED)
        self.li_stop_btn.pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text="CSV",
                   command=self.save_li_result).pack(side=tk.LEFT, padx=3)
        if PLOT_AVAILABLE:
            ttk.Button(btn_frame, text="График",
                       command=self.plot_li_result).pack(side=tk.LEFT, padx=3)

        fmt_frame = ttk.Frame(tab)
        fmt_frame.pack(fill=tk.X, padx=3, pady=1)
        ttk.Label(fmt_frame, text="Формат:").pack(side=tk.LEFT, padx=2)
        if not hasattr(self, 'display_format'):
            self.display_format = tk.StringVar(value='SCI')
        for fmt_label, fmt_val in [('SCI', 'SCI'), ('ENG', 'ENG'), ('СИ', 'SI2019'), ('Табло', 'PANEL')]:
            ttk.Radiobutton(fmt_frame, text=fmt_label, variable=self.display_format,
                            value=fmt_val, command=self._refresh_display
                            ).pack(side=tk.LEFT, padx=3)

        self.li_progress = ttk.Progressbar(tab, orient=tk.HORIZONTAL, mode='determinate')
        self.li_progress.pack(fill=tk.X, padx=3, pady=3)

        tree_frame = ttk.Frame(tab)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)
        columns = ('I_bias', 'L', 'R')
        self.li_tree = ttk.Treeview(tree_frame, columns=columns, show='headings', height=6)
        self.li_tree.heading('I_bias', text='I смещения (мА)')
        self.li_tree.heading('L', text='Индуктивность (Гн)')
        self.li_tree.heading('R', text='Сопротивление (Ом)')
        for c, w in zip(columns, (100, 150, 150)):
            self.li_tree.column(c, width=w, anchor='center')
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL,
                                   command=self.li_tree.yview)
        self.li_tree.configure(yscrollcommand=scrollbar.set)
        self.li_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.li_data = []
        self.li_measurement_running = False

    def run_li_measurement(self):
        if not self.ensure_connected():
            messagebox.showerror("Ошибка", "Прибор не подключен или не найден")
            return
        if self.li_measurement_running:
            messagebox.showwarning("Внимание", "Измерение уже выполняется")
            return
        params = {
            'start': self.li_start.get(),
            'stop': self.li_stop.get(),
            'step': self.li_step.get(),
            'range': self.li_range.get(),
            'level': self.li_level.get(),
            'averages': self.li_avg.get()
        }
        if params['start'] >= params['stop']:
            messagebox.showerror("Ошибка",
                                 "Начальный ток должен быть меньше конечного")
            return
        self.li_data.clear()
        for item in self.li_tree.get_children():
            self.li_tree.delete(item)
        self.li_progress['value'] = 0
        self.li_measurement_running = True
        self.li_start_btn.config(state=tk.DISABLED)
        self.li_stop_btn.config(state=tk.NORMAL)
        self.set_status("Запуск L-I измерения...")
        self.start_measurement_thread(self._li_measurement_target, params)

    def stop_li_measurement(self):
        if self.measurement_thread and self.measurement_thread.is_alive():
            self.measurement_thread.stop()
        self.li_measurement_running = False
        self.li_start_btn.config(state=tk.NORMAL)
        self.li_stop_btn.config(state=tk.DISABLED)
        self.set_status("L-I измерение отменено")
        self.log_message("L-I измерение отменено пользователем")

    def _li_measurement_target(self, **kwargs):
        stop_event = kwargs.pop('stop_event')
        e7 = self.e712
        e7.csv_context = 'L-I'
        e7.set_mode('LR')
        e7.set_level(kwargs['level'])
        if kwargs['range'] == 'AUTO':
            e7.set_range('AUTO')
        else:
            e7.set_range(int(kwargs['range']))
        e7.set_bias_off()

        currents = []
        i = kwargs['start']
        while i <= kwargs['stop'] + 1e-9 and not stop_event.is_set():
            currents.append(round(i, 1))
            i += kwargs['step']

        # Guard один раз, на максимальном токе скана
        i_max = max(currents) if currents else 0.0
        if i_max > 0:
            verdict = e7.preflight_bias('mA', i_max)
            logging.info('BIAS GUARD (L-I, %.1f мА): %s', i_max, verdict['reason'])
            if not verdict['allowed']:
                raise RuntimeError('L-I остановлен. ' + verdict['reason'])
        results = []
        total = len(currents)
        l0 = r0 = None                     # опорные значения первой точки
        for n, i_val in enumerate(currents):
            if stop_event.is_set():
                break
            e7.set_bias_current(i_val)
            res = e7.measure_averaged(kwargs['averages'])
            l_val = res.get('primary', 0)
            r_val = res.get('secondary', 0)
            if SCAN_GUARD_ENABLE and l_val and l_val > 0:
                if l0 is None:
                    l0, r0 = l_val, r_val
                else:
                    if l_val < l0 * (1.0 - SCAN_GUARD_L_SAT_DROP):
                        self.callback_queue.put(('log',
                            f'SCAN GUARD: стоп на {i_val:.1f} мА — '
                            f'L={l_val:.3e} < {1-SCAN_GUARD_L_SAT_DROP:.0%} '
                            f'от L0={l0:.3e}. Насыщение сердечника.'))
                        break
                    if r0 and r0 > 0 and r_val > r0 * SCAN_GUARD_R_RATIO:
                        self.callback_queue.put(('log',
                            f'SCAN GUARD: стоп на {i_val:.1f} мА — '
                            f'R={r_val:.3e} > {SCAN_GUARD_R_RATIO:g} x '
                            f'R0={r0:.3e}. Рост потерь/нагрев обмотки.'))
                        break
            results.append((i_val, l_val, r_val))
            progress = int((n+1)/total*100)
            self.callback_queue.put(('progress', progress))
            self.callback_queue.put(('log',
                f"L-I: I={i_val:.1f} мА, L={l_val:.3e} Гн, R={r_val:.3e} Ом"))
            self.callback_queue.put(('li_data', (i_val, l_val, r_val)))
        return {'results': results, 'params': kwargs, 'type': 'LI'}

    def save_li_result(self):
        if not self.li_data:
            messagebox.showwarning("Предупреждение", "Нет данных для сохранения")
            return
        filename = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV files", "*.csv")]
        )
        if not filename:
            return
        headers = ['I_bias (mA)', 'L (H)', 'R (Ohm)']
        save_to_csv_safe(filename, self.li_data, headers)
        messagebox.showinfo("Сохранение", f"Данные сохранены в {filename}")

    def plot_li_result(self):
        if not PLOT_AVAILABLE:
            messagebox.showerror("Ошибка",
                "Библиотеки matplotlib/pandas не установлены")
            return
        if not self.li_data:
            messagebox.showwarning("Предупреждение", "Нет данных для графика")
            return
        df = pd.DataFrame(self.li_data, columns=['I', 'L', 'R'])
        win = tk.Toplevel(self.root)
        win.title("L-I характеристика")
        win.geometry("700x500")
        fig = Figure(figsize=(7, 5))
        ax1 = fig.add_subplot(111)
        ax1.plot(df['I'], df['L'] * 1e6, 'b-o', label='L, µH')
        ax1.set_xlabel('I смещения, мА')
        ax1.set_ylabel('Индуктивность, µH', color='b')
        ax1.tick_params(axis='y', labelcolor='b')
        ax2 = ax1.twinx()
        ax2.plot(df['I'], df['R'], 'r-s', label='R')
        ax2.set_ylabel('Сопротивление, Ом', color='r')
        ax2.tick_params(axis='y', labelcolor='r')
        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=win)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------
    # Вкладка: Трёхполюсник
    # ------------------------------------------------------------------
    def create_three_term_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Трёхполюсник")

        frame = ttk.LabelFrame(tab, text="Параметры измерения", padding=3)
        frame.pack(fill=tk.X, padx=3, pady=3)

        # Row 0: Режим, Предел, Уровень
        ttk.Label(frame, text="Режим:").grid(row=0, column=0, sticky=tk.W, padx=2, pady=1)
        self.three_mode = tk.StringVar(value="CG")
        ttk.Combobox(frame, textvariable=self.three_mode,
                     values=['CG', 'CD', 'LR', 'LD'],
                     state='readonly', width=4).grid(row=0, column=1, padx=2, pady=1)

        ttk.Label(frame, text="Предел:").grid(row=0, column=2, sticky=tk.W, padx=2, pady=1)
        self.three_range = tk.StringVar(value="AUTO")
        ttk.Combobox(frame, textvariable=self.three_range,
                     values=['AUTO', '1', '2', '3', '4', '5'],
                     state='readonly', width=4).grid(row=0, column=3, padx=2, pady=1)

        self.three_level = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Выс.уровень",
                        variable=self.three_level).grid(row=0, column=4, padx=3, pady=1)

        # Row 1: Усреднение, Смещение
        ttk.Label(frame, text="Усредн.:").grid(row=1, column=0, sticky=tk.W, padx=2, pady=1)
        self.three_avg = tk.IntVar(value=3)
        ttk.Spinbox(frame, from_=1, to=MAX_AVERAGES,
                    textvariable=self.three_avg, width=4).grid(row=1, column=1, padx=2, pady=1)

        ttk.Label(frame, text="Смещение:").grid(row=1, column=2, sticky=tk.W, padx=2, pady=1)
        self.three_bias_type = tk.StringVar(value="V")
        ttk.Combobox(frame, textvariable=self.three_bias_type,
                     values=['V', 'mA'],
                     state='readonly', width=4).grid(row=1, column=3, padx=2, pady=1)
        self.three_bias_value = tk.DoubleVar(value=0.0)
        ttk.Entry(frame, textvariable=self.three_bias_value, width=6).grid(
            row=1, column=4, padx=2, pady=1
        )

        btn_frame = ttk.Frame(tab)
        btn_frame.pack(fill=tk.X, padx=3, pady=2)
        ttk.Button(btn_frame, text="Измерить",
                   command=self.run_three_measurement).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text="Сохранить",
                   command=self.save_three_result).pack(side=tk.LEFT, padx=3)

        fmt_frame = ttk.Frame(tab)
        fmt_frame.pack(fill=tk.X, padx=3, pady=1)
        ttk.Label(fmt_frame, text="Формат:").pack(side=tk.LEFT, padx=2)
        if not hasattr(self, 'display_format'):
            self.display_format = tk.StringVar(value='SCI')
        for fmt_label, fmt_val in [('SCI', 'SCI'), ('ENG', 'ENG'), ('СИ', 'SI2019'), ('Табло', 'PANEL')]:
            ttk.Radiobutton(fmt_frame, text=fmt_label, variable=self.display_format,
                            value=fmt_val, command=self._refresh_display
                            ).pack(side=tk.LEFT, padx=3)

        result_frame = ttk.LabelFrame(tab, text="Результат", padding=3)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)
        self.three_result_text = tk.Text(result_frame, height=6, state=tk.DISABLED)
        self.three_result_text.pack(fill=tk.BOTH, expand=True)

        self.last_three_result = None

    def run_three_measurement(self):
        if not self.ensure_connected():
            messagebox.showerror("Ошибка", "Прибор не подключен или не найден")
            return
        params = {
            'mode': self.three_mode.get(),
            'range': self.three_range.get(),
            'level': self.three_level.get(),
            'bias_type': self.three_bias_type.get(),
            'bias_val': self.three_bias_value.get(),
            'averages': self.three_avg.get()
        }
        self.set_status("Выполняется измерение трёхполюсника...")
        self.start_measurement_thread(self._three_measurement_target, params)

    def _three_measurement_target(self, **kwargs):
        kwargs.pop('stop_event')
        e7 = self.e712
        e7.csv_context = 'three'
        e7.set_mode(kwargs['mode'])
        e7.set_level(kwargs['level'])
        if kwargs['range'] == 'AUTO':
            e7.set_range('AUTO')
        else:
            e7.set_range(int(kwargs['range']))
        bias_type = kwargs['bias_type']
        bias_val = kwargs['bias_val']
        if bias_type in ('V', 'mA') and bias_val > 0:
            e7.apply_bias_guarded(bias_type, bias_val)   # предизмерение + допуск
        elif bias_type in ('V', 'mA'):
            e7._bias_type = bias_type
            e7.set_bias_off()
        else:
            e7._bias_type = 'V'
            e7.set_bias_off()
        res = e7.measure_averaged(kwargs['averages'])
        return {'result': res, 'params': kwargs, 'type': 'three'}

    def display_three_result(self, res, mode):
        param_names = {
            'CG': ('Ёмкость C', 'Проводимость G'),
            'CD': ('Ёмкость C', 'Тангенс D'),
            'LR': ('Индуктивность L', 'Сопротивление R'),
            'LD': ('Индуктивность L', 'Тангенс D')
        }
        primary_label, secondary_label = param_names.get(mode, ('Primary', 'Secondary'))
        fmt = self.display_format.get()
        rng = self._range_hint(self.three_range.get())
        pchar, schar = mode[0], mode[1]
        p_unit = {'C': 'F', 'L': 'H'}[pchar]
        s_unit = {'G': 'S', 'R': 'Ω', 'D': ''}[schar]
        fv = lambda v, u='', p='': format_value(v, u, fmt, rng=rng, param=p)
        text = "=== Результат трёхполюсника ===\n\n"
        text += f"{primary_label}: {fv(res.get('primary', 0), p_unit, pchar)}\n"
        text += f"{secondary_label}: {fv(res.get('secondary', 0), s_unit, schar)}\n"
        for label, key, unit in [('Q', 'Q', ''), ('D', 'D', ''), ('|Z|', 'Z', 'Ω')]:
            val = res.get(key)
            if val:
                text += f"{label}: {fv(val, unit)}\n"
        self.three_result_text.config(state=tk.NORMAL)
        self.three_result_text.delete(1.0, tk.END)
        self.three_result_text.insert(tk.END, text)
        self.three_result_text.config(state=tk.DISABLED)
        self.last_three_result = res

    def save_three_result(self):
        if not self.last_three_result:
            messagebox.showwarning("Предупреждение", "Нет результата для сохранения")
            return
        filename = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV files", "*.csv")]
        )
        if not filename:
            return
        res = self.last_three_result
        mode = self.three_mode.get()
        headers = ['Mode', 'Primary', 'Secondary', 'C', 'G', 'D', 'L', 'R', 'Q']
        data = [[mode, res.get('primary', 0), res.get('secondary', 0),
                 res.get('C', ''), res.get('G', ''), res.get('D', ''),
                 res.get('L', ''), res.get('R', ''), res.get('Q', '')]]
        save_to_csv_safe(filename, data, headers)
        messagebox.showinfo("Сохранение", f"Результат сохранён в {filename}")

    # ------------------------------------------------------------------
    # Вкладка: Подготовка
    # ------------------------------------------------------------------
    def create_prepare_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Подготовка")

        frame = ttk.LabelFrame(tab, text="Прогрев прибора", padding=5)
        frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(frame, text="Время прогрева (мин):").grid(row=0, column=0, padx=5)
        self.warmup_minutes = tk.IntVar(value=30)
        ttk.Spinbox(frame, from_=1, to=120,
                    textvariable=self.warmup_minutes, width=5).grid(row=0, column=1, padx=5)
        self.warmup_btn = ttk.Button(frame, text="Начать прогрев",
                                      command=self.run_warmup)
        self.warmup_btn.grid(row=0, column=2, padx=10)
        self.warmup_stop_btn = ttk.Button(frame, text="Остановить",
                                           command=self.stop_warmup,
                                           state=tk.DISABLED)
        self.warmup_stop_btn.grid(row=0, column=3, padx=5)
        self.warmup_timer_label = ttk.Label(frame, text="00:00",
                                             font=('Consolas', 14))
        self.warmup_timer_label.grid(row=0, column=4, padx=20)
        self.warmup_progress = ttk.Progressbar(frame, orient=tk.HORIZONTAL,
                                                length=200, mode='determinate')
        self.warmup_progress.grid(row=0, column=5, padx=10)

        cg_frame = ttk.LabelFrame(tab, text="Начальный баланс CG", padding=5)
        cg_frame.pack(fill=tk.X, padx=5, pady=5)
        self.cg_balance_btn = ttk.Button(cg_frame, text="Баланс CG",
                                          command=self.run_cg_balance)
        self.cg_balance_btn.pack(side=tk.LEFT, padx=5)
        self.cg_status = ttk.Label(cg_frame, text="Не выполнено", foreground="gray")
        self.cg_status.pack(side=tk.LEFT, padx=10)

        lr_frame = ttk.LabelFrame(tab, text="Начальный баланс LR", padding=5)
        lr_frame.pack(fill=tk.X, padx=5, pady=5)
        self.lr_balance_btn = ttk.Button(lr_frame, text="Баланс LR",
                                          command=self.run_lr_balance)
        self.lr_balance_btn.pack(side=tk.LEFT, padx=5)
        self.lr_status = ttk.Label(lr_frame, text="Не выполнено", foreground="gray")
        self.lr_status.pack(side=tk.LEFT, padx=10)

    def run_warmup(self):
        if not self.ensure_connected():
            messagebox.showerror("Ошибка", "Прибор не подключен или не найден")
            return
        params = {'minutes': self.warmup_minutes.get()}
        self.warmup_btn.config(state=tk.DISABLED)
        self.warmup_stop_btn.config(state=tk.NORMAL)
        self.set_status(f"Прогрев {params['minutes']} мин...")
        self.start_measurement_thread(self._warmup_target, params)

    def stop_warmup(self):
        if self.measurement_thread and self.measurement_thread.is_alive():
            self.measurement_thread.stop()
        self.warmup_btn.config(state=tk.NORMAL)
        self.warmup_stop_btn.config(state=tk.DISABLED)
        self.set_status("Прогрев отменён")

    def _warmup_target(self, **kwargs):
        stop_event = kwargs.pop('stop_event')
        total_seconds = kwargs['minutes'] * 60
        for sec in range(total_seconds, 0, -1):
            if stop_event.is_set():
                break
            time.sleep(1)
            self.callback_queue.put(('timer', sec))
            progress = int((total_seconds - sec) / total_seconds * 100)
            self.callback_queue.put(('progress', progress))
        return {'type': 'warmup'}

    def run_cg_balance(self):
        if not self.ensure_connected():
            messagebox.showerror("Ошибка", "Прибор не подключен или не найден")
            return
        self.cg_status.config(text="Выполняется...", foreground="blue")
        self.start_measurement_thread(self._cg_balance_target, {})

    def _cg_balance_target(self, **kwargs):
        kwargs.pop('stop_event')
        e7 = self.e712
        e7.csv_context = 'warmup'
        e7.set_mode('CG')
        e7.set_level(True)
        e7.set_range('AUTO')
        e7.set_bias_off()
        res = e7.measure_once()
        return {'type': 'cg_balance', 'result': res}

    def run_lr_balance(self):
        if not self.ensure_connected():
            messagebox.showerror("Ошибка", "Прибор не подключен или не найден")
            return
        self.lr_status.config(text="Выполняется...", foreground="blue")
        self.start_measurement_thread(self._lr_balance_target, {})

    def _lr_balance_target(self, **kwargs):
        kwargs.pop('stop_event')
        e7 = self.e712
        e7.set_mode('LR')
        e7.set_level(True)
        e7.set_range('AUTO')
        e7.set_bias_off()
        res = e7.measure_once()
        return {'type': 'lr_balance', 'result': res}

    # ------------------------------------------------------------------
    # Вкладка: Сортировка
    # ------------------------------------------------------------------
    def create_sorting_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Сортировка")

        frame = ttk.LabelFrame(tab, text="Параметры измерения", padding=5)
        frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(frame, text="Режим:").grid(row=0, column=0, sticky=tk.W, padx=2)
        self.sort_mode = tk.StringVar(value="CG")
        ttk.Combobox(frame, textvariable=self.sort_mode,
                     values=['CG', 'CD', 'LR', 'LD'],
                     state='readonly', width=5).grid(row=0, column=1, padx=2)

        ttk.Label(frame, text="Предел:").grid(row=0, column=2, sticky=tk.W, padx=2)
        self.sort_range = tk.StringVar(value="AUTO")
        ttk.Combobox(frame, textvariable=self.sort_range,
                     values=['AUTO', '1', '2', '3', '4', '5'],
                     state='readonly', width=5).grid(row=0, column=3, padx=2)

        self.sort_level = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Высокий уровень",
                        variable=self.sort_level).grid(row=0, column=4, padx=5)

        ttk.Label(frame, text="Усреднений:").grid(row=1, column=0, sticky=tk.W, padx=2)
        self.sort_avg = tk.IntVar(value=1)
        ttk.Spinbox(frame, from_=1, to=MAX_AVERAGES,
                    textvariable=self.sort_avg, width=5).grid(row=1, column=1, padx=2)

        ttk.Label(frame, text="Порог:").grid(row=1, column=2, sticky=tk.W, padx=2)
        self.sort_threshold = tk.DoubleVar(value=0.0)
        ttk.Entry(frame, textvariable=self.sort_threshold, width=12).grid(
            row=1, column=3, padx=2
        )
        self.threshold_label = ttk.Label(frame, text="Порог: не задан",
                                          foreground="gray")
        self.threshold_label.grid(row=1, column=4, padx=5)
        ttk.Button(frame, text="Калибровать",
                   command=self.calibrate_sorting_threshold).grid(
            row=1, column=5, padx=5
        )

        # Карточки сортировки (бины)
        bins_frame = ttk.LabelFrame(tab, text="Карточки сортировки (Bin)", padding=5)
        bins_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        columns = ('Bin', 'Min', 'Max', 'Label', 'Count')
        self.bin_tree = ttk.Treeview(bins_frame, columns=columns,
                                      show='headings', height=8)
        self.bin_tree.heading('Bin', text='Bin #')
        self.bin_tree.heading('Min', text='Min')
        self.bin_tree.heading('Max', text='Max')
        self.bin_tree.heading('Label', text='Метка')
        self.bin_tree.heading('Count', text='Кол-во')
        for c, w in zip(columns, (60, 120, 120, 200, 80)):
            self.bin_tree.column(c, width=w, anchor='center')
        self.bin_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(bins_frame, orient=tk.VERTICAL,
                            command=self.bin_tree.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.bin_tree.configure(yscrollcommand=sb.set)
        self.bin_counts = {}

        btn_frame = ttk.Frame(tab)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(btn_frame, text="Добавить карточку",
                   command=self.add_bin_dialog).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Удалить выбранную",
                   command=self.delete_selected_bin).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Очистить все",
                   command=self.clear_bins).pack(side=tk.LEFT, padx=5)

        run_frame = ttk.Frame(tab)
        run_frame.pack(fill=tk.X, padx=5, pady=5)
        self.start_sort_btn = ttk.Button(run_frame, text="Начать сортировку",
                                          command=self.start_sorting)
        self.start_sort_btn.pack(side=tk.LEFT, padx=5)
        self.stop_sort_btn = ttk.Button(run_frame, text="Остановить",
                                         command=self.stop_sorting,
                                         state=tk.DISABLED)
        self.stop_sort_btn.pack(side=tk.LEFT, padx=5)
        ttk.Button(run_frame, text="Очистить счётчики",
                   command=self.clear_bin_counts).pack(side=tk.LEFT, padx=5)

        status_frame = ttk.LabelFrame(tab, text="Текущий статус", padding=5)
        status_frame.pack(fill=tk.X, padx=5, pady=5)
        self.current_value_label = ttk.Label(status_frame,
                                              text="Значение: ---",
                                              font=('Consolas', 14))
        self.current_value_label.pack(side=tk.LEFT, padx=20)
        self.current_bin_label = ttk.Label(status_frame,
                                            text="Bin: ---",
                                            font=('Consolas', 14),
                                            foreground="blue")
        self.current_bin_label.pack(side=tk.LEFT, padx=20)
        self.counter_label = ttk.Label(status_frame,
                                        text="Всего: 0",
                                        font=('Consolas', 12))
        self.counter_label.pack(side=tk.LEFT, padx=20)
        self.total_sorted = 0

    def calibrate_sorting_threshold(self):
        if not self.ensure_connected():
            messagebox.showerror("Ошибка", "Прибор не подключен или не найден")
            return
        self.set_status("Калибровка порога...")
        self.start_measurement_thread(self._calibrate_threshold_target, {
            'mode': self.sort_mode.get(),
            'range': self.sort_range.get(),
            'level': self.sort_level.get(),
            'averages': self.sort_avg.get(),
        })

    def _calibrate_threshold_target(self, **kwargs):
        kwargs.pop('stop_event')
        e7 = self.e712
        e7.set_mode(kwargs['mode'])
        e7.set_level(kwargs['level'])
        if kwargs['range'] == 'AUTO':
            e7.set_range('AUTO')
        else:
            e7.set_range(int(kwargs['range']))
        e7.set_bias_off()
        res = e7.measure_averaged(kwargs['averages'])
        return {'type': 'threshold', 'threshold': res.get('primary', 0)}

    def add_bin_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Новая карточка сортировки")
        win.geometry("300x200")
        win.transient(self.root)

        ttk.Label(win, text="Min:").grid(row=0, column=0, padx=5, pady=5)
        min_var = tk.DoubleVar()
        ttk.Entry(win, textvariable=min_var).grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(win, text="Max:").grid(row=1, column=0, padx=5, pady=5)
        max_var = tk.DoubleVar()
        ttk.Entry(win, textvariable=max_var).grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(win, text="Метка:").grid(row=2, column=0, padx=5, pady=5)
        label_var = tk.StringVar(value="OK")
        ttk.Entry(win, textvariable=label_var).grid(row=2, column=1, padx=5, pady=5)

        def save():
            try:
                mn = min_var.get()
                mx = max_var.get()
                lbl = label_var.get()
                bin_num = len(self.bin_tree.get_children()) + 1
                self.bin_tree.insert('', tk.END,
                                      values=(bin_num, f"{mn:.3e}", f"{mx:.3e}",
                                              lbl, 0))
                self.bin_counts[bin_num] = 0
                win.destroy()
            except Exception as e:
                messagebox.showerror("Ошибка", str(e))

        ttk.Button(win, text="OK", command=save).grid(row=3, column=0,
                                                       columnspan=2, pady=10)

    def delete_selected_bin(self):
        sel = self.bin_tree.selection()
        if not sel:
            return
        for item in sel:
            self.bin_tree.delete(item)

    def clear_bins(self):
        self.bin_tree.delete(*self.bin_tree.get_children())
        self.bin_counts.clear()

    def clear_bin_counts(self):
        for item in self.bin_tree.get_children():
            vals = list(self.bin_tree.item(item, 'values'))
            vals[4] = 0
            self.bin_tree.item(item, values=vals)
        self.bin_counts.clear()
        self.total_sorted = 0
        self.counter_label.config(text="Всего: 0")

    def start_sorting(self):
        if not self.ensure_connected():
            messagebox.showerror("Ошибка", "Прибор не подключен или не найден")
            return
        if not self.bin_tree.get_children():
            messagebox.showwarning("Внимание", "Нет карточек сортировки")
            return
        self.sorting_active = True
        self.stop_sorting_flag.clear()
        self.start_sort_btn.config(state=tk.DISABLED)
        self.stop_sort_btn.config(state=tk.NORMAL)
        self.set_status("Сортировка запущена")
        self.sorting_thread = threading.Thread(target=self._sorting_loop, daemon=True)
        self.sorting_thread.start()

    def _sorting_loop(self):
        e7 = self.e712
        self.e712.csv_context = 'sort'
        e7.set_mode(self.sort_mode.get())
        e7.set_level(self.sort_level.get())
        if self.sort_range.get() == 'AUTO':
            e7.set_range('AUTO')
        else:
            e7.set_range(int(self.sort_range.get()))
        e7.set_bias_off()
        while not self.stop_sorting_flag.is_set():
            try:
                res = e7.measure_averaged(self.sort_avg.get())
                value = res.get('primary', 0)
                bin_label = "N/A"
                bin_num = None
                for item in self.bin_tree.get_children():
                    vals = self.bin_tree.item(item, 'values')
                    try:
                        mn = float(vals[1])
                        mx = float(vals[2])
                        if mn <= value <= mx:
                            bin_label = vals[3]
                            bin_num = int(vals[0])
                            new_count = int(vals[4]) + 1
                            new_vals = list(vals)
                            new_vals[4] = new_count
                            self.bin_tree.item(item, values=new_vals)
                            break
                    except (ValueError, IndexError):
                        continue
                self.callback_queue.put(('sorting_result',
                                          (value, bin_label, bin_num)))
                # time.sleep(0.5) удалён: GPIB handshake гарантирует завершение измерения
            except Exception as e:
                self.callback_queue.put(('log', f"Ошибка сортировки: {e}"))
                break
        self.sorting_active = False
        self.callback_queue.put(('log', "Сортировка остановлена"))

    def update_sorting_result(self, data):
        value, bin_label, bin_num = data
        self.current_value_label.config(text=f"Значение: {value:.4e}")
        self.current_bin_label.config(text=f"Bin: {bin_label}")
        self.total_sorted += 1
        self.counter_label.config(text=f"Всего: {self.total_sorted}")

    def stop_sorting(self):
        self.stop_sorting_flag.set()
        self.start_sort_btn.config(state=tk.NORMAL)
        self.stop_sort_btn.config(state=tk.DISABLED)
        self.set_status("Сортировка остановлена")

    # ------------------------------------------------------------------
    # Вкладка: Журнал
    # ------------------------------------------------------------------
    def create_log_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Журнал")
        self.log_text = tk.Text(tab, height=20, state=tk.NORMAL, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        sb = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=self.log_text.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=sb.set)
        btn_frame = ttk.Frame(tab)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(btn_frame, text="Очистить журнал",
                   command=lambda: self.log_text.delete(1.0, tk.END)
                   ).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Сохранить в файл",
                   command=self.save_log).pack(side=tk.LEFT, padx=5)

    def save_log(self):
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt", filetypes=[("Text files", "*.txt")]
        )
        if not filename:
            return
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(self.log_text.get(1.0, tk.END))
        messagebox.showinfo("Сохранение", f"Журнал сохранён в {filename}")


# =============================================================================
# ТОЧКА ВХОДА
# =============================================================================

def show_connection_notice(root):
    """Показывает при старте напоминание о правильном подключении Е7-12.

    Диалог «Устройство присоединительное» с правилом 3 крестов.
    """
    notice_text = (
        "⚠️  УСТРОЙСТВО ПРИСОЕДИНИТЕЛЬНОЕ  ⚠️\n"
        "\n"
        "При сборке измерительной цепи с использованием устройства\n"
        "присоединительного, отличного от «Устройство присоединительное E7-12»,\n"
        "использовать кабели типа РК50-2-11 — 4 шт., или аналогичные\n"
        "зарубежного производства.\n"
        "\n"
        "Следует соблюдать ПРАВИЛО 3 КРЕСТОВ:\n"
        "\n"
        "1. ЭКРАНЫ КАНАЛОВ собираются попарно 1-3 и 2-4, затем перекрестно\n"
        "   соединяются в общую точку.\n"
        "\n"
        "2. ПРИСОЕДИНЕНИЕ измеряемого элемента к четырёхзажимному устройству\n"
        "   присоединительному производится ПЕРЕКРЕСТНО, так что 1-4 и 2-3 —\n"
        "   диагонали соединения.\n"
        "\n"
        "3. ЦЕНТРАЛЬНЫЕ ЖИЛЫ каналов 1-2 ВСТРЕЧНО проходят сквозь ферритовое\n"
        "   кольцо (сердечник М20ВН-1 К10×6×3 — 3 шт.).\n"
        "\n"
        "─────────────────────────────────────────────────\n"
        "Также проверьте перед работой:\n"
        "  • Тумблер «УПРАВЛЕНИЕ» на Е7-12: «ДИСТ.» (Remote On) для\n"
        "    дистанционного управления, «МЕСТН.» (Remote Off) для\n"
        "    пассивного мониторинга.\n"
        "  • Адаптер UsbGpib V2 подключён к ПК и Е7-12.\n"
        "  • Прибор прогрет (не менее 30 мин)."
    )

    dlg = tk.Toplevel(root)
    dlg.title("Напоминание: Устройство присоединительное")
    dlg.geometry("900x480")
    dlg.resizable(False, False)
    dlg.transient(root)
    dlg.grab_set()

    # Иконка предупреждения
    header = tk.Label(dlg,
        text="⚠️  ВНИМАНИЕ  ⚠️",
        font=('Arial', 14, 'bold'),
        foreground='#c00',
        pady=5)
    header.pack(fill='x')

    # Текст уведомления
    text_widget = tk.Text(dlg, wrap='word', padx=15, pady=10,
                          font=('Consolas', 9), relief='flat')
    text_widget.insert('1.0', notice_text)
    text_widget.config(state='disabled')
    text_widget.pack(fill='both', expand=True, padx=10, pady=5)

    # Скроллбар
    sb = ttk.Scrollbar(dlg, orient='vertical', command=text_widget.yview)
    text_widget.configure(yscrollcommand=sb.set)

    # Кнопка «Закрыть»
    btn_frame = ttk.Frame(dlg)
    btn_frame.pack(fill='x', padx=15, pady=10)
    ttk.Button(btn_frame, text="Закрыть",
               command=dlg.destroy).pack(side='right', padx=5)
    ttk.Button(btn_frame, text="Больше не показывать",
               command=lambda: (dlg.destroy(),
                                setattr(app_ref[0], '_skip_notice', True))
               ).pack(side='left', padx=5)

    # Центрируем диалог
    dlg.update_idletasks()
    x = root.winfo_x() + (root.winfo_width() - dlg.winfo_width()) // 2
    y = root.winfo_y() + (root.winfo_height() - dlg.winfo_height()) // 2
    dlg.geometry(f"+{x}+{y}")
    dlg.wait_window()
# Ссылка на app для callback'а «Больше не показывать»
app_ref = [None]


def main():
    # Загружаем конфиг ДО настройки логирования, чтобы знать уровень
    config = load_config()
    debug_mode = get_config_bool(config, 'DEFAULT', 'debug_mode', False)

    # Настройка логирования
    log_level = logging.DEBUG if debug_mode else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='[%(asctime)s.%(msecs)03d] %(levelname)s %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )
    # Отдельный уровень для E712_RAW (сырые байты)
    logging.getLogger('E712_RAW').setLevel(logging.DEBUG if debug_mode else logging.INFO)

    # Применяем коды из INI-файла
    apply_config_codes(config)

    root = tk.Tk()
    style = ttk.Style()
    try:
        style.theme_use('clam')
    except tk.TclError:
        pass

    app = E712GUI(root)
    app_ref[0] = app

    log = logging.getLogger('E712_GUI')
    log.info('=== E712_GUI v%s от %s ===', __version__, __date__)
    log.info('Адаптер: UsbGpib V2 (xyphro, USBTMC, VID=0x%04X, PID=0x%04X)',
             USB_GPIB_V2_VID, USB_GPIB_V2_PID)
    log.info('Backend: авто (NI-VISA если установлена, иначе pyvisa-py)')
    log.info('Протокол: USBTMC')
    log.info('Командсет: ТО 2.724.011 (обновлённый)')
    log.info('Смещение: 0x20/0x24 + 0x28-0x31, порядок: десятки→единицы→десятые')
    log.info('  + СБУ=0x14 (DCL), ЗАП=0x08 (Trigger), НИД=0x5F (UNT), НПМ=0x3F (UNL)')
    log.info('Режимы CD (C-D) и LD (L‖D) — реальные режимы прибора')
    log.info('Тумблер УПРАВЛЕНИЕ на Е7-12: ДИСТ. (Remote On) по умолчанию')
    if debug_mode:
        log.info('🔍 РЕЖИМ ОТЛАДКИ ВКЛЮЧЁН — сырые байты КОП будут логироваться')
        log.info('   в форматах: HEX, ASCII, DEC, BIN, BCD, 8-4-2-1 (MSB first)')
        log.info('   Для изменения порядка битов: [LOG] bit_order = lsb в INI-файле')
    else:
        log.info('Режим отладки ВЫКЛЮЧЕН. Включите чекбокс «Режим отладки» '
                 'для логирования сырых байт КОП')


    # Автополлинг удалён — подключение только по кнопке (требование Е7-12)
    root.mainloop()


if __name__ == '__main__':
    main()
