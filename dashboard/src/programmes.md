# Fédéral — par programme

```js
import {money, moneyShort, number, percent, truncate} from "./components/format.js";

const meta = await FileAttachment("data/drilldown_meta.json").json();
const rows = (await FileAttachment("data/program_drilldown.csv").csv())
  .map((d) => ({...d, amount: +d.amount}));
```

```js
const CATEGORY_FR = {
  personnel: "Rémunération", operating: "Fonctionnement", transfers: "Transferts",
  capital: "Immobilisations", debt_service: "Service de la dette", other: "Autres"
};
const CATEGORY_COLOR = {
  domain: Object.values(CATEGORY_FR),
  range: ["#4269d0", "#97bbf5", "#efb118", "#3ca951", "#9c6b4e", "#9498a0"]
};
const cat = (d) => CATEGORY_FR[d.economic_category] ?? "Autres";

// Totals include the negative revenue offsets — excluding them overstates
// federal spending by $16.3B and breaks the reconciliation. Charts use
// `positive`, headline figures use `rows`.
const total = d3.sum(rows, (d) => d.amount);
const positive = rows.filter((d) => d.amount > 0);
const programmes = new Set(rows.map((d) => d.programme));
const departments = Array.from(d3.rollup(rows, (v) => d3.sum(v, (d) => d.amount),
                                         (d) => d.organization))
  .sort((a, b) => b[1] - a[1]);
```

Les dépenses fédérales de ${meta.fiscal_year}, croisées par **ministère,
programme et objet standard**. C'est le seul jeu de GC InfoBase qui porte ces
trois dimensions sur la même ligne : les autres tableaux donnent la dépense par
objet **ou** par programme, jamais les deux.

<div class="grid grid-cols-4">
  <div class="card"><h2>Dépenses ventilées</h2>
    <span class="big">${moneyShort(total)}</span>
    <span class="muted">${meta.fiscal_year}</span></div>
  <div class="card"><h2>Programmes</h2>
    <span class="big">${number(programmes.size)}</span>
    <span class="muted">avec des dépenses</span></div>
  <div class="card"><h2>Ministères</h2>
    <span class="big">${number(departments.length)}</span>
    <span class="muted">et organismes</span></div>
  <div class="card"><h2>Lignes</h2>
    <span class="big">${number(rows.length)}</span>
    <span class="muted">programme × objet standard</span></div>
</div>

## Une catégorie, ventilée par programme

La question que les tableaux à plat ne pouvaient pas trancher : la rémunération
fédérale totalise des dizaines de milliards, mais **dans quels programmes** ?

```js
const chosenCategory = view(Inputs.select(
  Object.values(CATEGORY_FR),
  {label: "Catégorie", value: "Rémunération"}
));
```

```js
const byProgramme = Array.from(
  d3.rollup(
    positive.filter((d) => cat(d) === chosenCategory),
    (v) => ({amount: d3.sum(v, (d) => d.amount), org: v[0].organization}),
    (d) => d.programme
  ),
  ([programme, v]) => ({programme, ...v})
).sort((a, b) => b.amount - a.amount);
```

```js
Plot.plot({
  width,
  height: 460,
  marginLeft: 250,
  x: {label: `${chosenCategory} — dépenses`, tickFormat: moneyShort, grid: true},
  y: {label: null},
  marks: [
    Plot.barX(byProgramme.slice(0, 20), {
      x: "amount", y: (d) => truncate(d.programme, 42), sort: {y: "-x"},
      fill: CATEGORY_COLOR.range[CATEGORY_COLOR.domain.indexOf(chosenCategory)] ?? "#4269d0",
      tip: {format: {x: money}, channels: {Ministère: "org"}}
    }),
    Plot.ruleX([0])
  ]
})
```

<div class="note">

${byProgramme.length ? `Les 20 premiers programmes représentent ${percent(100 * d3.sum(byProgramme.slice(0, 20), (d) => d.amount) / d3.sum(byProgramme, (d) => d.amount))} de la catégorie « ${chosenCategory} », répartie sur ${number(byProgramme.length)} programmes au total.` : "Aucune donnée pour cette catégorie."}

</div>

## Un ministère, programme par programme

```js
const chosenDept = view(Inputs.select(
  departments.map((d) => d[0]),
  {label: "Ministère", value: departments[0][0], format: (d) => truncate(d, 52)}
));
```

```js
const deptRows = positive.filter((d) => d.organization === chosenDept);
const deptProgrammes = Array.from(
  d3.rollup(deptRows, (v) => d3.sum(v, (d) => d.amount), (d) => d.programme)
).sort((a, b) => b[1] - a[1]).slice(0, 18).map(([programme]) => programme);
```

```js
Plot.plot({
  width,
  height: Math.max(240, 28 * deptProgrammes.length),
  marginLeft: 250,
  x: {label: "Dépenses", tickFormat: moneyShort, grid: true},
  y: {label: null, domain: deptProgrammes.map((p) => truncate(p, 42))},
  color: {...CATEGORY_COLOR, legend: true},
  marks: [
    Plot.barX(deptRows.filter((d) => deptProgrammes.includes(d.programme)), {
      x: "amount", y: (d) => truncate(d.programme, 42), fill: cat,
      order: CATEGORY_COLOR.domain,
      tip: {format: {x: money}, channels: {"Objet standard": "standard_object"}}
    }),
    Plot.ruleX([0])
  ]
})
```

Chaque barre est décomposée par catégorie de dépense : c'est le croisement que
seuls ces fichiers permettent.

## Un programme, objet standard par objet standard

```js
const programmeList = Array.from(
  d3.rollup(positive, (v) => d3.sum(v, (d) => d.amount), (d) => d.programme)
).sort((a, b) => b[1] - a[1]).map((d) => d[0]);
```

```js
const chosenProgramme = view(Inputs.select(programmeList, {
  label: "Programme", value: programmeList[0], format: (d) => truncate(d, 52)
}));
```

```js
const progRows = Array.from(
  d3.rollup(
    positive.filter((d) => d.programme === chosenProgramme),
    (v) => d3.sum(v, (d) => d.amount),
    (d) => d.standard_object
  ),
  ([standard_object, amount]) => ({standard_object, amount})
).sort((a, b) => b.amount - a.amount);
```

<div class="grid grid-cols-2">
  <div class="card">

```js
resize((width) => Plot.plot({
  width, height: 300, marginLeft: 190,
  x: {label: "Dépenses", tickFormat: moneyShort, grid: true},
  y: {label: null},
  marks: [
    Plot.barX(progRows, {
      x: "amount", y: (d) => truncate(d.standard_object, 32), sort: {y: "-x"},
      fill: "#4269d0", tip: {format: {x: money}}
    }),
    Plot.ruleX([0])
  ]
}))
```

  </div>
  <div class="card">

```js
Inputs.table(progRows, {
  columns: ["standard_object", "amount"],
  header: {standard_object: "Objet standard", amount: "Dépenses"},
  format: {standard_object: (d) => truncate(d, 40), amount: money},
  align: {amount: "right"},
  rows: 10
})
```

  </div>
</div>

## Tout le détail

```js
const search = view(Inputs.search(rows, {
  placeholder: "Chercher un programme, un ministère, un objet standard…",
  columns: ["programme", "organization", "standard_object"]
}));
```

```js
Inputs.table(search, {
  columns: ["organization", "programme", "standard_object", "appropriation", "amount"],
  header: {
    organization: "Ministère", programme: "Programme",
    standard_object: "Objet standard", appropriation: "Crédit", amount: "Dépenses"
  },
  format: {
    organization: (d) => truncate(d, 30),
    programme: (d) => truncate(d, 34),
    standard_object: (d) => truncate(d, 28),
    appropriation: (d) => (d === "voted" ? "Voté" : d === "statutory" ? "Permanent" : "—"),
    amount: money
  },
  align: {amount: "right"},
  rows: 16
})
```

<div class="note">

Ces chiffres proviennent de deux fichiers réunis : les dépenses **votées** et
les dépenses **permanentes**. Le fichier des dépenses votées, seul, ne
représente que 45 % du total — les grands postes permanents comme la Sécurité de
la vieillesse n'y figurent pas. Ensemble, ils concordent à 0,4 % près avec le
tableau par objet standard, qui est publié séparément.

</div>

<style>
.big { display: block; font-size: 1.8rem; font-weight: 600; line-height: 1.2; margin: 0.2rem 0; }
.muted { display: block; font-size: 0.78rem; color: var(--theme-foreground-muted); }
.card h2 { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; }
.note {
  border-left: 3px solid var(--theme-foreground-focus);
  padding: 0.5rem 0 0.5rem 1rem; margin: 1.5rem 0;
  font-size: 0.9rem; color: var(--theme-foreground-muted);
}
</style>
