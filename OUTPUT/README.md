# OUTPUT — QSCV B2910BL: приложение и автономный скрипт

Квазистатическая C–V (QSCV) на одном SMU Keysight B2910BL по методике
`QSCV_E7_12_B2910BL_FINAL.md` (корень репозитория; далее — «методика»),
с расширением диапазона ёмкостей до фемтофарад. Ничего не выдумано:
все SCPI-команды сверены с B2900B/BL Programming Guide и
`quasi_static_cv_B2910BL.md` (inbox); физическая модель DUT —
`physical_observer.py`; офлайн-анализатор — `qscv_final.py` (inbox).

Состав:

| Путь | Что это |
|---|---|
| `qscv_b2910bl.py` | Автономный однофайловый скрипт Python (CLI + tkinter-GUI, режим `--demo`, пресеты фФ). Только стандартная библиотека; `pyvisa` — опционально для реального прибора. |
| `webapp/` | Приложение (веб-рабочее место): Next.js 16 + TypeScript + Tailwind + shadcn/ui, симуляция физической модели DUT, генерация проверенного SCPI-скрипта, журнал CSV, Prisma/SQLite. |
| `webapp/docs/foundation/` | Документация-обоснование: методика, SCPI-последовательность, физическая модель, извлечённые тексты Programming/User's Guide (соответствуют PDF в `inbox/`). |

## 1. Автономный скрипт `qscv_b2910bl.py`

Однофайловый порт веб-приложения с расширением до фемтофарад.

```bash
python3 qscv_b2910bl.py                 # tkinter-GUI (по умолчанию симуляция DUT)
python3 qscv_b2910bl.py --demo DIR      # офлайн-прогон без GUI: CSV/JSON/SCPI в DIR
python3 qscv_b2910bl.py --demo DIR --preset ff_mos   # фФ-пресет (MOS-демо)
python3 qscv_b2910bl.py --demo DIR --preset ff_100f  # 100 фФ эталон, протокол B
python3 qscv_b2910bl.py --help
```

Зависимости: только стандартная библиотека Python 3. Для реального прибора —
`pip install pyvisa pyvisa-py` (ресурс задаётся в GUI).

Что делает:
- методика §6 — обязательная верификация временного режима на известных C и RC
  (гейт: FAIL останавливает измерение);
- §7 — пошаговый регламент (прямое/обратное направление, стоп-условия);
- §8 — дискретный заряд по апертурам, поправки на начальный/межапертурный
  пропуски и хвост (§9): `ΔQ = Q_obs + Q_gap + Q_tail`;
- §10–11 — работа и энергия, неопределённости; §12 — критерии пригодности;
- §13 — журнал: строка на апертуру, формат строго по методике:

```csv
run,direction,step,v_before_V,v_after_V,a_s,b_s,i_avg_A,i_background_A,u_i_A,pre_settled,status
```

- фемтофарадный режим: пресеты `ff_100f / ff_10f / ff_1f / ff_mos`
  (развитые ΔV при FIXED 10 нА — минимальный диапазон измерения тока
  B2910BL по User's Guide, табл. 2-7) и «фФ-консультант» — априорная оценка
  SNR заряда шага `ΔQ = C·ΔV` по шумовой модели, честно помечающая
  `OK / MARGINAL / BELOW_FLOOR`;
- генерация SCPI-журнала (`.scpi`) — только команды, сверенные с
  Programming Guide (триггерная модель `:TRIG:SOUR TIM/:TRIG:TIM/:TRIG:COUN`,
  `:SOUR:VOLT:TRIG`, `:FETC:ARR:CURR?`, `*WAI` и др.).

Чего скрипт сознательно НЕ делает (не выдумано): неподтверждённых SCPI-команд
нет; autozero/фильтры задаются пользователем отдельно по SCPI Command
Reference; параметры шумовой модели (`u_i`, offset) — входные данные
симуляции, а не паспортные значения.

## 2. Приложение `webapp/` (Next.js)

Веб-рабочее место оператора: вкладки «Оборудование» (§4), «Паспорт
программы» (§5), «Верификация» (§6), «Измерение» (§7), «Результаты» (§8–12),
«Анализ данных» (qscv_final: фиты, пропуски, пары fwd/rev), «Журнал CSV»
(§13), «SCPI-скрипт», «Справка» (§2–3, §8–11).

```bash
cd webapp
bun install            # или npm install
cp .env.example .env   # SQLite (создаётся автоматически)
bun run db:push        # схема Prisma
bun run dev            # http://localhost:3000
```

Режим по умолчанию — симуляция пассивной модели DUT
(`C0 || G || Σ(R_m–C_m)`, точное аналитическое распространение по линейным
сегментам — `physical_observer.py`), тот же PRNG, что в автономном скрипте.
Для реального прибора генерируется SCPI-скрипт (исполнять на шине VISA).

Исходный код приложения: `webapp/src/` (компоненты `components/qscv/`,
движок `lib/qscv/`, API-роуты `app/api/`). База (Prisma/SQLite):
паспорт прибора, паспорт программы, прогоны, журнал — `webapp/prisma/schema.prisma`.

## 3. Обоснование (grounding)

- Методика: `QSCV_E7_12_B2910BL_FINAL.md` (корень репозитория) и
  `webapp/docs/foundation/`.
- SCPI: inbox `B2900B-BL-Series-Precision-Source-Measure-Unit-Programming-Guide.pdf`,
  inbox `keysight b2910bl manual.pdf` (текстовые извлечения — в
  `webapp/docs/foundation/*.txt`).
- Теория: Kuhn 1970, Berglund 1966, Terman 1962, Lopez 1973, Monderer 1985
  (inbox), улучшенная техника QSCV (inbox).
- Ограничения фФ-диапазона честно документированы в самом инструменте
  (SNR-консультант): при 10 фА шума и разумных ΔV method floor ~1–10 фФ.
