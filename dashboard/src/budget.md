# Où va l'argent

```js
import {money, moneyShort, number, percent, truncate} from "./components/format.js";

const meta = await FileAttachment("data/comparability.json").json();

// Untyped on purpose: a fiscal year like "2024-25" matches d3.autoType's
// ISO-date pattern, so `typed: true` turns the column into Date objects and
// every comparison against the string year fails — silently, with zeros.
const asRows = (rows) => rows.map((d) => ({...d, amount: +d.amount}));

const categories = asRows(await FileAttachment("data/budget_categories.csv").csv());
const organizations = asRows(await FileAttachment("data/budget_organizations.csv").csv());
const programmes = asRows(await FileAttachment("data/spending_programmes.csv").csv());
const lapsed = (await FileAttachment("data/spending_lapsed.csv").csv())
  .map((d) => ({...d, authorities: +d.authorities, expenditures: +d.expenditures}));
```

```js
const FED = "ca-federal", QC = "qc";
const latest = meta.latest_year;
const byCategory = new Map(meta.categories.map((c) => [c.key, c]));
const LEVEL_COLOR = {comparable: "#3ca951", caution: "#efb118", "not-comparable": "#e8635a"};

const current = categories.filter((d) => d.fiscal_year === latest[d.jurisdiction]);
const total = (jur) => d3.sum(current.filter((d) => d.jurisdiction === jur), (d) => d.amount);
const progs = (jur, kind) => programmes.filter(
  (d) => d.jurisdiction === jur && d.kind === kind && d.fiscal_year === latest[jur]
);
const lastLapsed = lapsed.at(-1);
```

<div class="grid grid-cols-4">
  <div class="card">
    <h2>Dépenses fédérales</h2>
    <span class="big">${moneyShort(total(FED))}</span>
    <span class="muted">${latest[FED]} · dépenses réelles</span>
  </div>
  <div class="card">
    <h2>Dépenses du Québec</h2>
    <span class="big">${moneyShort(total(QC))}</span>
    <span class="muted">${latest[QC]} · Comptes publics</span>
  </div>
  <div class="card">
    <h2>Transferts fédéraux</h2>
    <span class="big">${percent(100 * (current.find((d) => d.jurisdiction === FED && d.economic_category === "transfers")?.amount ?? 0) / total(FED))}</span>
    <span class="muted">de la dépense fédérale</span>
  </div>
  <div class="card">
    <h2>Crédits périmés</h2>
    <span class="big">${moneyShort(lastLapsed.authorities - lastLapsed.expenditures)}</span>
    <span class="muted">autorisés mais non dépensés, ${lastLapsed.fiscal_year}</span>
  </div>
</div>

## Les plus gros postes

Le fédéral dépense l'essentiel de son budget par **transferts** — à des
particuliers, à des provinces, à des organismes — plutôt qu'en achetant des
biens et des services. Les cinq premiers postes représentent à eux seuls une
part majeure de la dépense totale.

<div class="grid grid-cols-2">
  <div class="card">

### Fédéral — crédits les plus importants

```js
resize((width) => Plot.plot({
  width, height: 420, marginLeft: 230,
  x: {label: "Dépenses réelles", tickFormat: moneyShort, grid: true},
  y: {label: null},
  marks: [
    Plot.barX(progs(FED, "appropriation").slice(0, 15), {
      x: "amount", y: (d) => truncate(d.programme, 40), sort: {y: "-x"},
      fill: "#4269d0", tip: {format: {x: money}, channels: {Organisme: "organization"}}
    }),
    Plot.ruleX([0])
  ]
}))
```

  </div>
  <div class="card">

### Québec — programmes les plus importants

```js
resize((width) => Plot.plot({
  width, height: 420, marginLeft: 230,
  x: {label: "Dépenses", tickFormat: moneyShort, grid: true},
  y: {label: null},
  marks: [
    Plot.barX(progs(QC, "programme").slice(0, 15), {
      x: "amount", y: (d) => truncate(d.programme, 40), sort: {y: "-x"},
      fill: "#efb118", tip: {format: {x: money}, channels: {Portefeuille: "organization"}}
    }),
    Plot.ruleX([0])
  ]
}))
```

  </div>
</div>

## Les transferts fédéraux, poste par poste

Le tableau par objet standard réduit 60 % de la dépense fédérale à une seule
ligne, « paiements de transfert ». Voici ce qu'elle contient : les programmes
nommés, avec leur montant réel.

```js
const transferSearch = view(Inputs.search(progs(FED, "transfer"), {
  placeholder: "Chercher un programme de transfert…",
  columns: ["programme", "organization"]
}));
```

```js
Inputs.table(transferSearch, {
  columns: ["programme", "organization", "amount"],
  header: {programme: "Programme", organization: "Ministère", amount: "Dépenses"},
  format: {
    programme: (d) => truncate(d, 62),
    organization: (d) => truncate(d, 34),
    amount: money
  },
  align: {amount: "right"},
  width: {programme: 420, amount: 130},
  rows: 15
})
```

<div class="note">

Les deux vues se recoupent : la Sécurité de la vieillesse figure à 60,6 G$ dans
les crédits **et** dans les transferts, parce que ce sont deux découpages du
même argent — l'un par autorisation parlementaire, l'autre par programme de
transfert. Il ne faut pas les additionner.

</div>

### Québec — qui reçoit les transferts

Le Québec ne nomme pas ses programmes de transfert un par un ; il publie les
montants par **type de bénéficiaire**. Les deux premiers postes disent
l'essentiel du modèle québécois.

```js
Plot.plot({
  width,
  height: 280,
  marginLeft: 250,
  x: {label: "Transferts versés", tickFormat: moneyShort, grid: true},
  y: {label: null},
  marks: [
    Plot.barX(progs(QC, "transfer").slice(0, 10), {
      x: "amount", y: (d) => truncate(d.programme, 44), sort: {y: "-x"},
      fill: "#efb118", tip: {format: {x: money}, channels: {Portefeuille: "organization"}}
    }),
    Plot.ruleX([0])
  ]
})
```

<div class="note">

Les établissements de santé et les institutions d'enseignement reçoivent à eux
seuls la majeure partie des transferts québécois. C'est la raison pour laquelle
la rémunération du Québec paraît si faible en comparaison de celle du fédéral :
le personnel des réseaux est payé par ces organismes, à même les transferts, et
non par le gouvernement directement.

</div>

## Autorisé contre dépensé

Le Parlement autorise chaque année plus que ce qui est finalement dépensé.
L'écart, les **crédits périmés**, revient au Trésor. Il a atteint 56,8 G$ en
2022-23, soit 12,7 % des montants autorisés.

```js
Plot.plot({
  width,
  height: 320,
  marginLeft: 62,
  y: {label: "Milliards de dollars", tickFormat: moneyShort, grid: true},
  color: {legend: true, domain: ["Autorisé", "Dépensé"], range: ["#97bbf5", "#4269d0"]},
  marks: [
    Plot.barY(lapsed.flatMap((d) => [
      {fiscal_year: d.fiscal_year, mesure: "Autorisé", amount: d.authorities},
      {fiscal_year: d.fiscal_year, mesure: "Dépensé", amount: d.expenditures}
    ]), {
      fx: "fiscal_year", x: "mesure", y: "amount", fill: "mesure",
      tip: {format: {y: money}}
    }),
    Plot.ruleY([0])
  ],
  fx: {label: null, tickRotate: -45},
  x: {axis: null}
})
```

```js
Plot.plot({
  width,
  height: 220,
  marginLeft: 62,
  x: {label: null, tickRotate: -40},
  y: {label: "Part des crédits périmés", percent: true, grid: true},
  marks: [
    Plot.lineY(lapsed, {
      x: "fiscal_year",
      y: (d) => (d.authorities - d.expenditures) / d.authorities,
      stroke: "#e8635a", strokeWidth: 2,
      tip: {format: {y: (d) => percent(100 * d)}}
    }),
    Plot.dot(lapsed, {
      x: "fiscal_year",
      y: (d) => (d.authorities - d.expenditures) / d.authorities,
      fill: "#e8635a"
    }),
    Plot.ruleY([0])
  ]
})
```

## Par organisation

<div class="grid grid-cols-2">
  <div class="card">

### Fédéral — ministères et organismes

```js
const orgs = organizations.filter((d) => d.fiscal_year === latest[d.jurisdiction]);
```

```js
resize((width) => Plot.plot({
  width, height: 340, marginLeft: 180,
  x: {label: "Dépenses", tickFormat: moneyShort, grid: true},
  y: {label: null},
  marks: [
    Plot.barX(orgs.filter((d) => d.jurisdiction === FED), {
      x: "amount", y: (d) => truncate(d.organization, 32), sort: {y: "-x"},
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
  width, height: 340, marginLeft: 180,
  x: {label: "Dépenses", tickFormat: moneyShort, grid: true},
  y: {label: null},
  marks: [
    Plot.barX(orgs.filter((d) => d.jurisdiction === QC), {
      x: "amount", y: (d) => truncate(d.organization, 32), sort: {y: "-x"},
      fill: "#efb118", tip: {format: {x: money}}
    }),
    Plot.ruleX([0])
  ]
}))
```

  </div>
</div>

## Par nature de dépense

<div class="grid grid-cols-2">
  <div class="card">

### Fédéral

```js
resize((width) => Plot.plot({
  width, height: 260, marginLeft: 110,
  x: {label: "Dépenses", tickFormat: moneyShort, grid: true},
  y: {label: null},
  marks: [
    Plot.barX(current.filter((d) => d.jurisdiction === FED), {
      x: "amount",
      y: (d) => byCategory.get(d.economic_category)?.label_fr ?? d.economic_category,
      sort: {y: "-x"},
      fill: (d) => LEVEL_COLOR[byCategory.get(d.economic_category)?.level ?? "not-comparable"],
      tip: {format: {x: money, fill: false}}
    }),
    Plot.ruleX([0])
  ]
}))
```

  </div>
  <div class="card">

### Québec

```js
resize((width) => Plot.plot({
  width, height: 260, marginLeft: 110,
  x: {label: "Dépenses", tickFormat: moneyShort, grid: true},
  y: {label: null},
  marks: [
    Plot.barX(current.filter((d) => d.jurisdiction === QC), {
      x: "amount",
      y: (d) => byCategory.get(d.economic_category)?.label_fr ?? d.economic_category,
      sort: {y: "-x"},
      fill: (d) => LEVEL_COLOR[byCategory.get(d.economic_category)?.level ?? "not-comparable"],
      tip: {format: {x: money, fill: false}}
    }),
    Plot.ruleX([0])
  ]
}))
```

  </div>
</div>

<div class="warn">

**Les deux colonnes ne s'additionnent pas.** Les deux juridictions présentent
désormais des dépenses réelles — le fédéral par les Comptes publics du Canada,
le Québec par les Comptes publics du Québec — mais les transferts fédéraux
versés au Québec figurent dans les deux : comme dépense fédérale, puis comme
revenu finançant la dépense québécoise. Les additionner compterait la santé et
la péréquation deux fois.

La couleur des barres indique dans quelle mesure une catégorie peut être mise en
regard de l'autre juridiction — verte pour comparable, ambre pour prudence,
rouge pour non comparable. Une seule catégorie sur six résiste à la comparaison
directe :

</div>

```js
Inputs.table(
  meta.categories.map((c) => ({
    Catégorie: c.label_fr,
    Verdict: {comparable: "Comparable", caution: "Avec prudence",
              "not-comparable": "Non comparable"}[c.level],
    Pourquoi: c.note_fr || c.note
  })),
  {
    columns: ["Catégorie", "Verdict", "Pourquoi"],
    width: {Catégorie: 130, Verdict: 130},
    rows: 6,
    sort: null
  }
)
```

<style>
.big { display: block; font-size: 1.9rem; font-weight: 600; line-height: 1.2; margin: 0.2rem 0; }
.muted { display: block; font-size: 0.78rem; color: var(--theme-foreground-muted); }
.card h2 { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; }
.note, .warn {
  border-left: 3px solid var(--theme-foreground-focus);
  padding: 0.5rem 0 0.5rem 1rem; margin: 1.5rem 0;
  font-size: 0.9rem; color: var(--theme-foreground-muted);
}
.warn { border-left-color: #e8635a; }
</style>
