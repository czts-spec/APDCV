"use client";
/**
 * Панель «Справка» — методика §2–3, §8–11, §14.
 * Термины, формулы, численный пример §8, энергия, кинетическая энергия носителей,
 * хвост/неопределённость, библиография и границы применимости.
 */
import { useMemo, useState } from "react";
import { AlertTriangle, BookOpen, Calculator, CheckCircle2, XCircle } from "lucide-react";
import { formatEng, formatJoules } from "@/lib/qscv/format";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { NumField, PanelHeader } from "./shared";

const Q_E = 1.602e-19; // Кл
const M_E = 9.109e-31; // кг

// ============================== §8: численный пример ==============================

function ExampleS8() {
  const [dv, setDv] = useState(0.01);
  const [width, setWidth] = useState(0.001);
  const [currentsText, setCurrentsText] = useState("5e-9\n3e-9\n1.5e-9\n0.4e-9\n1e-10");
  const [check, setCheck] = useState<null | boolean>(null);

  const currents = useMemo(
    () =>
      currentsText
        .split("\n")
        .map((l) => parseFloat(l.trim()))
        .filter((v) => Number.isFinite(v)),
    [currentsText],
  );

  const qObs = currents.reduce((s, i) => s + i * width, 0);
  const cSec = dv !== 0 ? qObs / dv : NaN;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <NumField label="ΔV, В" value={dv} onChange={setDv} step="any" />
        <NumField label="Длительность апертуры w, с" value={width} onChange={setWidth} step="any" />
      </div>
      <div className="space-y-1.5">
        <Label className="text-xs">Токи апертур Ī_j, А — по одному в строке</Label>
        <Textarea
          rows={5}
          value={currentsText}
          onChange={(e) => {
            setCurrentsText(e.target.value);
            setCheck(null);
          }}
          className="font-mono text-xs"
          aria-label="Токи апертур"
        />
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <div className="rounded-md border p-3">
          <div className="text-[11px] text-muted-foreground">ΔQ_obs = Σ Ī_j·w_j ({currents.length} окон)</div>
          <div className="font-mono text-sm">{formatEng(qObs, "Кл")}</div>
        </div>
        <div className="rounded-md border p-3">
          <div className="text-[11px] text-muted-foreground">C_sec = ΔQ/ΔV</div>
          <div className="font-mono text-sm">{formatEng(cSec, "Ф")}</div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="outline"
          onClick={() => {
            const qOk = Math.abs(qObs - 10e-12) <= 0.1e-12;
            const cOk = Math.abs(cSec - 1e-9) <= 0.01e-9;
            setCheck(qOk && cOk);
          }}
        >
          <Calculator className="h-4 w-4" />
          Проверить пример §8
        </Button>
        {check !== null &&
          (check ? (
            <Badge variant="outline" className="border-emerald-500/60 text-emerald-700 dark:text-emerald-400">
              <CheckCircle2 className="h-3.5 w-3.5" />
              PASS: ΔQ ≈ 10 пКл, C_sec ≈ 1 нФ
            </Badge>
          ) : (
            <Badge variant="outline" className="border-red-500/60 text-red-600 dark:text-red-400">
              <XCircle className="h-3.5 w-3.5" />
              FAIL: значения не соответствуют примеру §8
            </Badge>
          ))}
      </div>
      <div className="rounded-md bg-muted/50 p-3 text-sm">
        <p>
          ΔV=0.01 В. Пять последовательных апертур по 1 мс дали после коррекции токи 5, 3, 1.5, 0.4 и 0.1 нА.
          Зарегистрированный заряд: 10 пКл. Секущая: 1 нФ.
        </p>
        <p className="mt-1 text-muted-foreground">
          Это полная ёмкость только если отсутствуют существенные пропущенное начало и хвост.
        </p>
      </div>
    </div>
  );
}

// ============================== §10: энергия ёмкости ==============================

function EnergyCalc() {
  const [cNf, setCnf] = useState(1);
  const [v1, setV1] = useState(0);
  const [v2, setV2] = useState(1);

  const c = cNf * 1e-9;
  const dU = 0.5 * c * (v2 * v2 - v1 * v1);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <NumField label="C, нФ" value={cNf} onChange={setCnf} step="any" />
        <NumField label="V₁, В" value={v1} onChange={setV1} step="any" />
        <NumField label="V₂, В" value={v2} onChange={setV2} step="any" />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="outline"
          onClick={() => {
            setCnf(1);
            setV1(0);
            setV2(1);
          }}
        >
          Пресет: C=1 нФ, V1=0, V2=1 В → 0.5 нДж
        </Button>
      </div>
      <div className="rounded-md border p-3">
        <div className="text-[11px] text-muted-foreground">ΔU = ½·C·(V₂² − V₁²)</div>
        <div className="font-mono text-sm">{formatJoules(dU)}</div>
      </div>
      <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
        <li>
          <span className="font-mono text-foreground">W_port = ∫ V·I dt</span> — работа порта за переход.
        </li>
        <li>
          <span className="font-mono text-foreground">U = ½·C·V²</span> — энергия идеальной линейной ёмкости.
        </li>
        <li>
          <span className="font-mono text-foreground">ΔU = ∫ V dQ</span> — для нелинейной обратимой Q(V).
        </li>
        <li>
          Баланс: <span className="font-mono text-foreground">W_port = ΔU + Q_heat,out</span> — при утечке, trapping и
          гистерезисе работа порта не равна автоматически изменению запасённой энергии.
        </li>
        <li>Произведение отдельных средних V и I в общем случае не равно среднему V·I.</li>
      </ul>
    </div>
  );
}

// ============================== §11: кинетическая энергия носителей ==============================

function DriftCalc() {
  const [iA, setIA] = useState(1e-9);
  const [nCm3, setNCm3] = useState(1e16);
  const [aCm2, setACm2] = useState(1e-4);
  const [lCm, setLCm] = useState(1e-4);
  const [mStar, setMStar] = useState(1);

  const nM3 = nCm3 * 1e6;
  const aM2 = aCm2 * 1e-4;
  const lM = lCm * 1e-2;
  const mKg = mStar * M_E;

  const vd = nM3 > 0 && aM2 > 0 ? iA / (nM3 * Q_E * aM2) : NaN;
  const eDrift = 0.5 * mKg * vd * vd;
  const nCarriers = nCm3 * aCm2 * lCm;
  const eTotal = 0.5 * nCarriers * mKg * vd * vd;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <NumField label="Ток I, А" value={iA} onChange={setIA} step="any" />
        <NumField label="n, см⁻³" value={nCm3} onChange={setNCm3} step="any" hint="концентрация носителей" />
        <NumField label="A, см²" value={aCm2} onChange={setACm2} step="any" hint="площадь сечения" />
        <NumField label="L, см" value={lCm} onChange={setLCm} step="any" hint="длина канала" />
        <NumField label="m*/m_e" value={mStar} onChange={setMStar} step="any" hint="эффективная масса в единицах m_e" />
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-md border p-3">
          <div className="text-[11px] text-muted-foreground">v_d = I/(n·q·A)</div>
          <div className="font-mono text-sm">{formatEng(vd, "м/с")}</div>
        </div>
        <div className="rounded-md border p-3">
          <div className="text-[11px] text-muted-foreground">E_drift = ½·m*·v_d²</div>
          <div className="font-mono text-sm">
            {formatJoules(eDrift)} ({formatEng(eDrift / Q_E, "эВ")})
          </div>
        </div>
        <div className="rounded-md border p-3">
          <div className="text-[11px] text-muted-foreground">число носителей n·A·L</div>
          <div className="font-mono text-sm">{Number.isFinite(nCarriers) ? nCarriers.toExponential(3) : "—"}</div>
        </div>
        <div className="rounded-md border p-3">
          <div className="text-[11px] text-muted-foreground">E_total = ½·n·A·L·m*·v_d²</div>
          <div className="font-mono text-sm">{formatJoules(eTotal)}</div>
        </div>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Константы: q = 1.602·10⁻¹⁹ Кл; m_e = 9.109·10⁻³¹ кг. Единицы ввода — см/см²/см⁻³, перевод в СИ выполняется
        автоматически.
      </p>
      <Alert variant="destructive">
        <AlertTriangle />
        <AlertTitle>Ограничения модели (§11)</AlertTitle>
        <AlertDescription>
          B2910BL непосредственно не измеряет скорость носителей. Подставлять весь ток SMU в эту формулу нельзя, если он
          включает ток смещения, несколько типов носителей, неоднородные каналы и обмен зарядом с ловушками. Ток зарядки
          MOS не доказывает проводниковый перенос через оксид. Модельная энергия направленного движения — не полная
          тепловая энергия, не энергия поля и не V·I.
        </AlertDescription>
      </Alert>
    </div>
  );
}

// ============================== Основная панель ==============================

const BIBLIOGRAPHY: { text: string; ref: string }[] = [
  {
    text: "F. E. Terman. An investigation of surface states at a silicon/silicon oxide interface employing metal-oxide-silicon diodes. Solid-State Electronics, 5 (1962).",
    ref: "DOI: 10.1016/0038-1101(62)90111-9",
  },
  {
    text: "C. N. Berglund. Surface states at steam-grown silicon–silicon dioxide interfaces. IEEE Transactions on Electron Devices, ED-13 (1966) 701–705.",
    ref: "DOI: 10.1109/T-ED.1966.15827",
  },
  {
    text: "M. Kuhn. A quasi-static technique for MOS C–V and surface state measurements. Solid-State Electronics, 13 (1970) 873–885.",
    ref: "DOI: 10.1016/0038-1101(70)90073-0",
  },
  {
    text: "M. Kuhn, E. H. Nicollian. Fundamental limitations in the use of the quasi-static technique for MOS C–V measurements. J. Electrochem. Soc., 118 (1971).",
    ref: "DOI: 10.1149/1.2408053",
  },
  {
    text: "J. Schmitz et al. Leakage current correction in quasi-static C–V measurements. ICMTS 2004.",
    ref: "конференция ICMTS",
  },
  {
    text: "Keysight. B2900B/BL Series Precision Source/Measure Unit — Programming Guide.",
    ref: "keysight.com",
  },
  {
    text: "Keysight. B2900B/BL Series Precision Source/Measure Unit — SCPI Command Reference.",
    ref: "keysight.com",
  },
  {
    text: "Keysight. B2900B/BL Series Precision Source/Measure Unit — User's Guide.",
    ref: "keysight.com",
  },
  {
    text: "Keysight. Quasi-Static C–V Measurement (Application Note 5992-0436).",
    ref: "keysight.com",
  },
  {
    text: "OpenStax. University Physics Vol. 2, §9.1 (электрический ток, скорость дрейфа).",
    ref: "openstax.org",
  },
];

const MECHANICAL_ANALOGY: [string, string][] = [
  ["Заряд Q", "обобщённое перемещение"],
  ["Ток dQ/dt", "скорость"],
  ["Напряжение V", "обобщённая сила"],
  ["Ёмкость dQ/dV", "податливость"],
  ["Обратная ёмкость dV/dQ", "дифференциальная жёсткость"],
  ["V·I", "мощность"],
  ["Интеграл V dQ", "работа"],
];

export default function ReferencePanel() {
  return (
    <div className="space-y-4">
      <PanelHeader
        title="Справка"
        subtitle="Методика §2–3, §8–11, §14: термины, формулы, интерактивные примеры, библиография"
      />

      <Accordion type="multiple" defaultValue={["s2", "s3"]} className="rounded-xl border bg-card px-6">
        <AccordionItem value="s2">
          <AccordionTrigger className="text-sm font-semibold">§2. Термины и энергия</AccordionTrigger>
          <AccordionContent>
            <div className="space-y-3">
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <div className="rounded-md bg-muted/50 p-3 font-mono text-sm">I = dQ/dt [А = Кл/с]</div>
                <div className="rounded-md bg-muted/50 p-3 font-mono text-sm">V = dW/dQ [В = Дж/Кл]</div>
                <div className="rounded-md bg-muted/50 p-3 font-mono text-sm">P(t) = V·I</div>
                <div className="rounded-md bg-muted/50 p-3 font-mono text-sm">W_port = ∫ V·I dt</div>
              </div>
              <div>
                <div className="mb-2 text-xs font-semibold">Механическая аналогия</div>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Электрическая величина</TableHead>
                      <TableHead>Механическая аналогия</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {MECHANICAL_ANALOGY.map(([el, mech]) => (
                      <TableRow key={el}>
                        <TableCell className="text-xs">{el}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">{mech}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <p className="text-[11px] text-muted-foreground">
                Аналогия верна как язык обобщённых координат, а не как утверждение о буквальной механической пружине.
                CV²/2 — энергия идеальной линейной ёмкости, не кинетическая энергия носителей.
              </p>
            </div>
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="s3">
          <AccordionTrigger className="text-sm font-semibold">§3. Теория квазистатической C–V</AccordionTrigger>
          <AccordionContent>
            <div className="space-y-3 text-sm">
              <div className="rounded-md bg-muted/50 p-3 font-mono">C_qs(V) = dQ_eq/dV</div>
              <p>
                Квазистатическая ёмкость — производная равновесного заряда по напряжению; измерение должно быть
                достаточно медленным, чтобы состояние успевало следовать за смещением.
              </p>
              <div className="rounded-md bg-muted/50 p-3 font-mono">Метод Куна: C = I / (dV/dt)</div>
              <p>
                Применим при непрерывном линейном изменении напряжения (ramp): ток пропорционален скорости развёртки.
              </p>
              <div className="rounded-md bg-muted/50 p-3 font-mono">Ступенчатый sweep: C_sec,k = ΔQ_k / ΔV_DUT,k</div>
              <p>
                Секущая на середине интервала: результат относится к середине шага и зависит от длительности окна
                наблюдения и подготовки.
              </p>
              <Alert>
                <AlertTriangle />
                <AlertDescription>
                  Ступенчатый sweep SMU требует регистрировать заряд перехода, а не делить одиночный ток на ΔV/dwell.
                </AlertDescription>
              </Alert>
            </div>
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="s8">
          <AccordionTrigger className="text-sm font-semibold">§8. Численный пример (интерактивный)</AccordionTrigger>
          <AccordionContent>
            <ExampleS8 />
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="s10">
          <AccordionTrigger className="text-sm font-semibold">§10. Энергия (интерактивный расчёт)</AccordionTrigger>
          <AccordionContent>
            <EnergyCalc />
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="s11">
          <AccordionTrigger className="text-sm font-semibold">
            §11. Кинетическая энергия носителей (интерактивный расчёт)
          </AccordionTrigger>
          <AccordionContent>
            <DriftCalc />
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="s912">
          <AccordionTrigger className="text-sm font-semibold">§9. Хвост и §12. Неопределённость</AccordionTrigger>
          <AccordionContent>
            <div className="space-y-3 text-sm">
              <p className="font-medium">Критерий установления хвоста (§9)</p>
              <div className="rounded-md bg-muted/50 p-3 font-mono text-sm">|B| + k·u(B) ≤ I_lim,&nbsp;&nbsp; |S| + k·u(S) ≤ S_lim</div>
              <p className="text-muted-foreground">
                Аппроксимация последних N окон: i = B + S(t − t̄). При k=1.96 нормальная интерпретация лишь приближённая;
                это не доказательство исчезновения всех медленных процессов.
              </p>
              <p className="font-medium">Неопределённость заряда (§12)</p>
              <div className="rounded-md bg-muted/50 p-3 font-mono text-sm">
                u²(Q_obs) ≈ Σ [w_j²·u²(I_j−L_j) + (I_j−L_j)²·u²(w_j)]
              </div>
              <p className="text-muted-foreground">
                w_j = b_j − a_j. Малый ток может иметь большой интеграл на длинном времени. Общие offset/gain-члены не
                усредняются как независимый шум.
              </p>
            </div>
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="s14">
          <AccordionTrigger className="text-sm font-semibold">§14. Библиография</AccordionTrigger>
          <AccordionContent>
            <ol className="list-decimal space-y-2 pl-5 text-sm">
              {BIBLIOGRAPHY.map((b, i) => (
                <li key={i}>
                  {b.text} <span className="font-mono text-xs text-muted-foreground">[{b.ref}]</span>
                </li>
              ))}
            </ol>
          </AccordionContent>
        </AccordionItem>
      </Accordion>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Границы применимости</CardTitle>
          <CardDescription>Что модель и анализ НЕ доказывают</CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="list-disc space-y-2 pl-5 text-sm text-muted-foreground">
            <li>
              Пассивная линейная модель — структурная причинность выбранного класса моделей, не доказательство, что
              реальный DUT относится к нему.
            </li>
            <li>Fast/slow ≠ автоматически capacitance/traps: медленный ток дают RC тракта, генерация, абсорбция, контакты, drift.</li>
            <li>
              Для идентификации ловушек нужны эталонные RC, вариации подготовки/температуры и независимая электростатика.
            </li>
          </ul>
          <div className="mt-3 flex items-center gap-2 text-[11px] text-muted-foreground">
            <BookOpen className="h-3.5 w-3.5" />
            Разделы нумерации соответствуют «Методике квазистатических измерений на Keysight B2910BL».
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
