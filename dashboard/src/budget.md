# Fédéral et Québec

```js
import {money, moneyShort, number, percent, truncate} from "./components/format.js";

const meta = await FileAttachment("data/comparability.json").json();

// Loaded untyped on purpose. A fiscal year like "2024-25" matches d3.autoType's
// ISO-date pattern, so `typed: true` silently converts it to a Date and every
// comparison against the string year in comparability.json fails — leaving the
// page rendering zeros with no error. Amounts are coerced explicitly instead.
const asRows = (rows) => rows.map((d) => ({...d, amount: +d.amount}));

const categories = asRows(await FileAttachment("data/budget_categories.csv").csv());
const organizations = asRows(await FileAttachment("data/budget_organizations.csv").csv());
const appropriation = asRows(await FileAttachment("data/budget_appropriation.csv").csv());
```

```js
const FED = "ca-federal", QC = "qc";
const latest = meta.latest_year;
const JURISDICTION = {[FED]: "Fédéral", [QC]: "Québec"};
const LEVEL = {
  comparable: {label: "Comparable", color: "#3ca951"},
  caution: {label: "Avec prudence", color: "#efb118"},
  "not-comparable": {label: "Non comparable", color: "#e8635a"}
};

// Latest year per jurisdiction — they differ, and that is stated rather than hidden.
const current = categories.filter((d) => d.fiscal_year === latest[d.jurisdiction]);
const byCategory = new Map(meta.categories.map((c) => [c.key, c]));
const amount = (jur, key) =>
  current.find((d) => d.jurisdiction === jur && d.economic_category === key)?.amount ?? 0;
const total = (jur) => d3.sum(current.filter((d) => d.jurisdiction === jur), (d) => d.amount);
```

<div class="grid grid-cols-3">
  <div class="card">
    <h2>Fédéral — dépenses réelles</h2>
    <span class="big">${moneyShort(total(FED))}</span>
    <span class="muted">${latest[FED]} · Comptes publics via GC InfoBase</span>
  </div>
  <div class="card">
    <h2>Québec — crédits votés</h2>
    <span class="big">${moneyShort(total(QC))}</span>
    <span class="muted">${latest[QC]} · Budget de dépenses</span>
  </div>
  <div class="card">
    <h2>Catégories comparables</h2>
    <span class="big">${meta.categories.filter((c) => c.level === "comparable").length} / ${meta.categories.length}</span>
    <span class="muted">une seule résiste à la comparaison directe</span>
  </div>
</div>

<div class="warn">

**Ces deux totaux ne s'additionnent pas et ne se comparent pas directement.**
Le fédéral publie des **dépenses réelles**, le Québec des **crédits votés** —
un montant dépensé contre un montant autorisé. Les transferts fédéraux versés au
Québec figurent en plus dans les deux colonnes : comme dépense fédérale, puis
comme revenu finançant la dépense québécoise. Additionner les deux compterait
la santé et la péréquation deux fois.

</div>

## Ce qui peut être comparé, et ce qui ne peut pas

Chaque catégorie porte un verdict établi à partir des vocabulaires publiés. Le
tableau ne masque pas les catégories non comparables : il les affiche en
indiquant pourquoi la comparaison échoue.

```js
const verdictRows = meta.categories.map((c) => ({
  Catégorie: c.label_fr,
  Fédéral: amount(FED, c.key),
  Québec: amount(QC, c.key),
  Verdict: LEVEL[c.level].label,
  level: c.level
}));
```

```js
Inputs.table(verdictRows, {
  columns: ["Catégorie", "Fédéral", "Québec", "Verdict"],
  format: {
    Fédéral: moneyShort,
    Québec: moneyShort,
    Verdict: (d, i) => htl.html`<span class="pill" style="background:${
      LEVEL[verdictRows[i].level].color}22; color:${LEVEL[verdictRows[i].level].color}">${d}</span>`
  },
  align: {Fédéral: "right", Québec: "right"},
  width: {Catégorie: 160, Verdict: 150},
  rows: 8,
  sort: null
})
```

<div class="grid grid-cols-3">
${meta.categories.filter((c) => c.level !== "comparable").map((c) => htl.html`
  <div class="card verdict" style="border-left-color:${LEVEL[c.level].color}">
    <h2>${c.label_fr}</h2>
    <span class="pill" style="background:${LEVEL[c.level].color}22; color:${LEVEL[c.level].color}">${LEVEL[c.level].label}</span>
    <p>${c.note_fr || c.note}</p>
  </div>`)}
</div>

## La seule comparaison directe : le service de la dette

```js
const comparableKeys = meta.categories.filter((c) => c.level === "comparable").map((c) => c.key);
const comparableSeries = categories.filter((d) => comparableKeys.includes(d.economic_category));
```

Les deux ordres de gouvernement paient les intérêts de leur propre dette au même
niveau de consolidation, ce qui rend cette série directement comparable — la
seule du tableau.

```js
Plot.plot({
  width,
  height: 300,
  marginLeft: 62,
  y: {label: "Service de la dette", tickFormat: moneyShort, grid: true},
  color: {legend: true, domain: [FED, QC], range: ["#4269d0", "#efb118"],
          tickFormat: (d) => JURISDICTION[d]},
  fx: {label: null, tickRotate: -45},
  marks: [
    // Faceted by year, not stacked. A stacked bar would add federal to Quebec —
    // the exact sum this page exists to say you must not compute.
    Plot.barY(comparableSeries, {
      fx: "fiscal_year", x: "jurisdiction", y: "amount", fill: "jurisdiction",
      tip: {format: {y: money, x: (d) => JURISDICTION[d]}}
    }),
    Plot.ruleY([0])
  ],
  x: {axis: null}
})
```

## Chaque juridiction sur son propre axe

Pour tout le reste, les deux séries sont présentées séparément. Les échelles
diffèrent d'un facteur trois et les catégories ne recouvrent pas les mêmes
réalités ; les superposer donnerait une fausse impression de commensurabilité.

<div class="grid grid-cols-2">
  <div class="card">

### Fédéral — dépenses par catégorie

```js
resize((width) => Plot.plot({
  width, height: 300, marginLeft: 110,
  x: {label: "Dépenses", tickFormat: moneyShort, grid: true},
  y: {label: null},
  marks: [
    Plot.barX(current.filter((d) => d.jurisdiction === FED), {
      x: "amount",
      y: (d) => byCategory.get(d.economic_category)?.label_fr ?? d.economic_category,
      sort: {y: "-x"},
      fill: (d) => LEVEL[byCategory.get(d.economic_category)?.level ?? "not-comparable"].color,
      tip: {format: {x: money, fill: false}}
    }),
    Plot.ruleX([0])
  ]
}))
```

  </div>
  <div class="card">

### Québec — crédits par catégorie

```js
resize((width) => Plot.plot({
  width, height: 300, marginLeft: 110,
  x: {label: "Crédits", tickFormat: moneyShort, grid: true},
  y: {label: null},
  marks: [
    Plot.barX(current.filter((d) => d.jurisdiction === QC), {
      x: "amount",
      y: (d) => byCategory.get(d.economic_category)?.label_fr ?? d.economic_category,
      sort: {y: "-x"},
      fill: (d) => LEVEL[byCategory.get(d.economic_category)?.level ?? "not-comparable"].color,
      tip: {format: {x: money, fill: false}}
    }),
    Plot.ruleX([0])
  ]
}))
```

  </div>
</div>

<div class="note">

Les barres reprennent la couleur du verdict : vert pour comparable, ambre pour
prudence, rouge pour non comparable. La part rouge et ambre de chaque graphique
donne une idée directe de la proportion des dépenses qui **ne** peut pas être
mise en regard de l'autre juridiction.

</div>

## Voté contre permanent

La dimension qui se traduit exactement. Le fédéral distingue *voted* et
*statutory*, le Québec *votés* et *permanents* : dans les deux cas, l'argent
approuvé chaque année par l'assemblée législative contre l'argent qui découle
d'une loi existante.

```js
const approp = appropriation.filter(
  (d) => d.fiscal_year === latest[d.jurisdiction] && d.appropriation !== "unknown"
);
```

```js
Plot.plot({
  width,
  height: 190,
  marginLeft: 90,
  x: {label: "Part des dépenses", percent: true, grid: true},
  y: {label: null, tickFormat: (d) => JURISDICTION[d]},
  color: {legend: true, domain: ["voted", "statutory"], range: ["#4269d0", "#97bbf5"],
          tickFormat: (d) => (d === "voted" ? "Votés" : "Permanents")},
  marks: [
    Plot.barX(approp, {
      x: "amount", y: "jurisdiction", fill: "appropriation",
      offset: "normalize", order: ["voted", "statutory"],
      tip: {format: {x: money}}
    }),
    Plot.ruleX([0])
  ]
})
```

<div class="note">

Les données fédérales par objet standard ne portent pas cette dimension ; la
part fédérale provient de la table par crédit, qui couvre le même périmètre
découpé autrement. Les deux barres restent donc indicatives.

</div>

## Les plus gros postes de chaque côté

```js
const orgs = organizations.filter((d) => d.fiscal_year === latest[d.jurisdiction]);
```

<div class="grid grid-cols-2">
  <div class="card">

### Fédéral — organisations

```js
resize((width) => Plot.plot({
  width, height: 340, marginLeft: 170,
  x: {label: "Dépenses", tickFormat: moneyShort, grid: true},
  y: {label: null},
  marks: [
    Plot.barX(orgs.filter((d) => d.jurisdiction === FED), {
      x: "amount", y: (d) => truncate(d.organization, 30), sort: {y: "-x"},
      fill: "#4269d0", tip: {format: {x: money}}
    }),
    Plot.ruleX([0])
  ]
}))
```

  </div>
  <div class="card">

### Québec — portefeuilles

```js
resize((width) => Plot.plot({
  width, height: 340, marginLeft: 170,
  x: {label: "Crédits", tickFormat: moneyShort, grid: true},
  y: {label: null},
  marks: [
    Plot.barX(orgs.filter((d) => d.jurisdiction === QC), {
      x: "amount", y: (d) => truncate(d.organization, 30), sort: {y: "-x"},
      fill: "#efb118", tip: {format: {x: money}}
    }),
    Plot.ruleX([0])
  ]
}))
```

  </div>
</div>

<div class="note">

Les organisations fédérales et les portefeuilles québécois ne se correspondent
pas — 130 entités d'un côté, 25 de l'autre, découpées selon des logiques
différentes. Aucune table de correspondance n'est appliquée, et il n'y en aura
pas : c'est la dimension que la taxonomie déclare explicitement non joignable.

</div>

<style>
.big { display: block; font-size: 2rem; font-weight: 600; line-height: 1.2; margin: 0.2rem 0; }
.muted { display: block; font-size: 0.8rem; color: var(--theme-foreground-muted); }
.card h2 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; }
.card.verdict { border-left: 3px solid; }
.card.verdict p { font-size: 0.82rem; color: var(--theme-foreground-muted); margin: 0.6rem 0 0; }
.pill {
  display: inline-block; padding: 0.1rem 0.5rem; border-radius: 999px;
  font-size: 0.72rem; font-weight: 600; white-space: nowrap;
}
.note, .warn {
  border-left: 3px solid var(--theme-foreground-focus);
  padding: 0.5rem 0 0.5rem 1rem; margin: 1.5rem 0;
  font-size: 0.9rem; color: var(--theme-foreground-muted);
}
.warn { border-left-color: #e8635a; }
</style>
