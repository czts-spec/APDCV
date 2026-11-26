# Квазистатическая C–V (QSCV) на Keysight B2910BL

## Идея метода

Метод Куна (M. Kuhn, 1970) измеряет квазистатическую ёмкость MOS/MIS-структуры по зарядному току при линейном изменении напряжения:

\[
C_{\mathrm{qs}}(V)=\frac{I(V)}{dV/dt}
\]

B2910BL задаёт ступенчатую линейную развёртку напряжения на затворе и измеряет ток. При постоянном шаге \(\Delta V\) и интервале между точками \(T\):

\[
\frac{dV}{dt}\approx\frac{\Delta V}{T}
\qquad\Rightarrow\qquad
C_{\mathrm{qs}}(V)=I(V)\frac{T}{\Delta V}
\]

Для MOS-конденсатора результат обычно нормируют на площадь затвора:

\[
C'_{\mathrm{qs}}(V)=\frac{C_{\mathrm{qs}}(V)}{A}
\]

## Что нужно

- Keysight B2910BL, подключённый по USB, LAN или GPIB.
- Python 3.9+.
- PyVISA и совместимый VISA backend, обычно NI-VISA.
- Экранированный измерительный кабель, желательно triax/коаксиальный в зависимости от вашей fixture.
- MOS-конденсатор с известной площадью затвора.
- Опционально: LCR-метр или C–V-анализатор для независимой high-frequency C–V проверки.

Установка зависимостей:

```bash
python -m pip install pyvisa pyvisa-py numpy matplotlib pandas
```

Если используется NI-VISA, обычно достаточно `pyvisa`; `pyvisa-py` полезен как резервный backend.

## Подключение

Для MOS-конденсатора:

| B2910BL | DUT |
|---|---|
| Force HI | Затвор |
| Sense HI | Затвор, при 4-проводном подключении |
| Force LO | Подложка / общий контакт |
| Sense LO | Подложка / общий контакт, при 4-проводном подключении |
| Guard | Экран кабеля / guard электрод fixture |

Важно: guard подключают к экрану и охранным проводам, но **не** соединяют его с Force LO или Sense LO.

## Настройка измерения

B2910BL выполняет staircase voltage sweep: на каждом шаге задаётся напряжение и измеряется ток. Для QSCV обычно используют:

- Источник напряжения: DC, linear sweep.
- Измеряемая величина: ток.
- Auto range тока: включён, если ожидаемый ток меняется на порядки.
- Integration time / NPLC: подбирается по шуму и скорости ramp.
- Compliance: ограничение тока для защиты тонкого оксида.
- Forward и reverse sweep: выявляют гистерезис, trapping и неравновесность.

### Выбор скорости

Начальная скорость ramp — примерно 0.01–0.1 В/с.

| Наблюдение | Действие |
|---|---|
| Сильный гистерезис forward/reverse | Уменьшить скорость ramp |
| Ток ниже шума | Увеличить скорость ramp, улучшить экранирование |
| Заметная утечка затвора | Использовать более медленный ramp и коррекцию утечки |
| Кривая зависит от скорости | Уменьшить скорость; проверить время релаксации инверсионного слоя |
| Малая ёмкость | Минимизировать длину кабелей, использовать guard |

## Формулы

Для каждой точки:

\[
C_i=\frac{I_i}{r}
\]

где \(r=dV/dt\).

Для forward sweep \(r>0\), для reverse sweep \(r<0\). В скрипте используется абсолютное значение:

\[
C_i=\left|\frac{I_i}{r}\right|
\]

Удельная ёмкость:

\[
C'_i=\frac{C_i}{A}
\]

Плотность тока утечки, если она известна или измерена отдельно:

\[
I_{\mathrm{cap}}(V)=I_{\mathrm{meas}}(V)-I_{\mathrm{leak}}(V)
\]

Тогда:

\[
C_{\mathrm{qs}}(V)=\frac{I_{\mathrm{cap}}(V)}{r}
\]

## SCPI-последовательность

Минимальная последовательность для forward sweep:

```scpi
*RST
*CLS

:OUTP OFF

:SENS:FUNC "CURR"
:SENS:CURR:RANG:AUTO ON
:SENS:CURR:NPLC 1

:SOUR:FUNC VOLT
:SOUR:VOLT:MODE SWE
:SOUR:VOLT:STAR -2
:SOUR:VOLT:STOP 2
:SOUR:VOLT:STEP 0.01
:SOUR:SWE:RANG FIX
:SOUR:SWE:SPAC LIN
:SOUR:SWE:DIR UP
:SOUR:VOLT:TRIG:SOUR TIM
:SOUR:VOLT:TRIG:TIM 0.05

:OUTP ON
:INIT:IMM
*WAI
:FETC?
:OUTP OFF
```

Для reverse sweep:

```scpi
:SOUR:SWE:DIR DOWN
:SOUR:VOLT:STAR 2
:SOUR:VOLT:STOP -2
```

Точные допустимые параметры, диапазоны и синтаксис необходимо сверять с *B2900B/BL Series Precision Source/Measure Unit — Programming Guide* и *SCPI Command Reference* для установленной версии прошивки.

## Как пользоваться скриптом

Скрипт `qscv_b2910bl.py`:

1. Подключается к B2910BL по VISA.
2. Настраивает voltage sweep и измерение тока.
3. Выполняет forward и reverse ramp.
4. Считывает массивы напряжения и тока.
5. Вычисляет \(C_{\mathrm{qs}}(V)\) и удельную ёмкость.
6. Сохраняет CSV и строит график.

Пример запуска:

```bash
python qscv_b2910bl.py --resource "USB0::0x0957::0x8718::MY12345678::INSTR"
```

Или по TCP/IP:

```bash
python qscv_b2910bl.py --resource "TCPIP0::192.168.1.100::inst0::INSTR"
```

Для симуляции без прибора:

```bash
python qscv_b2910bl.py --simulate
```

## Интерпретация результата

В accumulation и depletion QSCV-кривая должна быть близка к low-frequency C–V. В inversion идеальная low-frequency кривая возвращается к высокой ёмкости, если инверсионный слой успевает формироваться; high-frequency кривая остаётся низкой.

Расхождение forward и reverse sweep может означать:

- слишком быстрый ramp;
- trapping / slow states;
- гистерезис диэлектрика;
- зарядку ловушек;
- нестабильность температуры или смещения;
- утечку затвора.

## Ограничения B2910BL

B2910BL не поддерживает pulse mode и FAST-режим, но для классической QSCV это не является препятствием: нужны DC voltage sweep и точное измерение тока.

Практические ограничения:

- Утечка затвора может быть сравнима с зарядным током.
- Паразитная ёмкость кабелей добавляется к ёмкости DUT.
- Шум важен при малых токах.
- Скорость ramp должна быть согласована с временем отклика инверсионного слоя и ловушек.
- Compliance необходимо ограничить для защиты тонкого оксида.

## Ссылки

- M. Kuhn, “A quasi-static technique for MOS C–V and surface state measurements,” *Solid-State Electronics*, vol. 13, pp. 873–885, 1970. DOI: 10.1016/0038-1101(70)90073-0.
- C. N. Berglund, “Surface States at Steam-Grown Silicon–Silicon Dioxide Interfaces,” *IEEE Transactions on Electron Devices*, vol. ED-13, pp. 701–705, 1966.
- Keysight, *B2900B/BL Series Precision Source/Measure Unit — Programming Guide*.
- Keysight, *B2900B/BL Series Precision Source/Measure Unit — SCPI Command Reference*.
