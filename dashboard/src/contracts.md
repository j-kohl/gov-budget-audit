# Contrats publics du Québec

Les contrats adjugés par les organismes publics québécois, publiés dans SEAO.
Cette section porte sur l'approvisionnement, pas sur l'ensemble de la dépense
publique — voir [Où va l'argent](./) pour les dépenses des deux gouvernements.

```js
import {money, moneyShort, number, percent, competitionColor} from "./components/format.js";

const summary = await FileAttachment("data/summary.json").json();
const monthly = await FileAttachment("data/monthly.csv").csv({typed: true});
const competition = await FileAttachment("data/competition.csv").csv({typed: true});
const regions = await FileAttachment("data/regions.csv").csv({typed: true});
const categories = await FileAttachment("data/categories.csv").csv({typed: true});
```

```js
const directShare = (() => {
  const total = d3.sum(competition, (d) => d.total_cad);
  const direct = d3.sum(competition.filter((d) => d.method_key === "direct"), (d) => d.total_cad);
  return total ? (100 * direct) / total : 0;
})();
```

<div class="grid grid-cols-4">
  <div class="card">
    <h2>Valeur totale adjugée</h2>
    <span class="big">${moneyShort(summary.total_cad)}</span>
    <span class="muted">${summary.first_award} → ${summary.last_award}</span>
  </div>
  <div class="card">
    <h2>Contrats adjugés</h2>
    <span class="big">${number(summary.awards)}</span>
    <span class="muted">${number(summary.notices)} avis distincts</span>
  </div>
  <div class="card">
    <h2>Fournisseurs payés</h2>
    <span class="big">${number(summary.suppliers)}</span>
    <span class="muted">auprès de ${number(summary.buyers)} organismes</span>
  </div>
  <div class="card">
    <h2>Part de gré à gré</h2>
    <span class="big">${percent(directShare)}</span>
    <span class="muted">de la valeur, sans appel d'offres</span>
  </div>
</div>

<div class="note">

Les montants ci-dessus ne comptent que les **soumissions gagnantes** libellées en
dollars. SEAO publie toutes les soumissions reçues, pas seulement celles
retenues : sur ${number(summary.all_bids)} soumissions, ${number(summary.winning_bids)}
ont été adjugées. ${number(summary.excluded_non_dollar)} adjudications sont
exprimées dans une autre unité (%, points, $/h, $/km) et sont exclues des
totaux — les additionner n'aurait aucun sens.

</div>

## Valeur adjugée par mois

```js
const monthlyTotals = d3.rollups(
  monthly,
  (v) => d3.sum(v, (d) => d.total_cad),
  (d) => d.month
).map(([month, total_cad]) => ({month, total_cad}));
```

```js
Plot.plot({
  width,
  height: 300,
  marginLeft: 60,
  y: {label: "Valeur adjugée", tickFormat: moneyShort, grid: true},
  x: {label: null},
  marks: [
    Plot.rectY(monthlyTotals, {
      x: "month",
      y: "total_cad",
      interval: "month",
      fill: "var(--theme-foreground-focus)",
      tip: {format: {y: money, x: (d) => d.toLocaleDateString("fr-CA", {year: "numeric", month: "long"})}}
    }),
    Plot.ruleY([0])
  ]
})
```

Les pics correspondent à des contrats d'infrastructure de très grande valeur
plutôt qu'à une hausse générale de l'activité.

## Mode d'adjudication dans le temps

```js
Plot.plot({
  width,
  height: 300,
  marginLeft: 60,
  y: {label: "Part de la valeur adjugée", percent: true, grid: true},
  x: {label: null, tickFormat: "d"},
  color: {...competitionColor, legend: true},
  marks: [
    Plot.areaY(competition, {
      x: "year",
      y: "total_cad",
      fill: "method",
      offset: "normalize",
      order: competitionColor.domain,
      tip: true
    }),
    Plot.ruleY([0])
  ]
})
```

<div class="note">

Les deux ères de publication de SEAO nomment les modes d'adjudication
différemment. Ce graphique utilise une catégorie harmonisée dérivée du code
`<type>` (XML) et de `procurementMethod` (OCDS) — sans quoi une même catégorie
se scinderait en deux à la frontière de 2021.

</div>

<div class="grid grid-cols-2">
  <div class="card">

### Par nature du contrat

```js
resize((width) => Plot.plot({
  width,
  height: 260,
  marginLeft: 150,
  x: {label: "Valeur adjugée", tickFormat: moneyShort, grid: true},
  y: {label: null},
  marks: [
    Plot.barX(categories.slice(0, 8), {
      x: "total_cad",
      y: "category",
      sort: {y: "-x"},
      fill: "var(--theme-foreground-focus)",
      tip: {format: {x: money}}
    }),
    Plot.ruleX([0])
  ]
}))
```

  </div>
  <div class="card">

### Par région de livraison

```js
resize((width) => Plot.plot({
  width,
  height: 260,
  marginLeft: 150,
  x: {label: "Valeur adjugée", tickFormat: moneyShort, grid: true},
  y: {label: null},
  marks: [
    Plot.barX(regions.slice(0, 8), {
      x: "total_cad",
      y: "region",
      sort: {y: "-x"},
      fill: "var(--theme-foreground-focus)",
      tip: {format: {x: money}}
    }),
    Plot.ruleX([0])
  ]
}))
```

  </div>
</div>

## Couverture et qualité

```js
Inputs.table(
  summary.coverage.map((c) => ({
    format: c.format === "seao-xml" ? "XML (2009 → mai 2021)" : "OCDS JSON (juin 2021 →)",
    rows: c.rows,
    share: c.rows / summary.awards
  })),
  {
    columns: ["format", "rows", "share"],
    header: {format: "Format source", rows: "Adjudications", share: "Part"},
    format: {rows: number, share: (d) => percent(100 * d)},
    align: {rows: "right", share: "right"},
    width: {format: 260}
  }
)
```

Deux formats se succèdent : XML jusqu'en mai 2021, puis JSON conforme à l'Open
Contracting Data Standard. Les deux se chevauchent de juin 2021 à mai 2024 dans
la source ; l'ingestion n'en retient qu'un seul par période pour éviter le double
comptage.

Une adjudication est rattachée à la période du fichier qui la publie, pas à sa
date d'adjudication : un avis publié en 2024 peut porter sur un contrat conclu
bien avant, et c'est fréquent.

<div class="note">

Quelques lignes portent des dates d'adjudication invraisemblables :
${number(summary.data_quality.before_2009)} antérieures à 2009
(${percent(100 * summary.data_quality.before_2009 / summary.awards)} du total),
${number(summary.data_quality.future_dated)} postérieures à aujourd'hui, et
${number(summary.data_quality.missing_date)} sans date. Ce sont des erreurs de
saisie à la source. Elles restent dans les données et dans les totaux, mais les
graphiques temporels sont bornés à 2009 pour éviter qu'elles n'écrasent les
échelles.

</div>

<style>
.big { display: block; font-size: 2rem; font-weight: 600; line-height: 1.2; margin: 0.2rem 0; }
.muted { display: block; font-size: 0.8rem; color: var(--theme-foreground-muted); }
.note {
  border-left: 3px solid var(--theme-foreground-focus);
  padding: 0.5rem 0 0.5rem 1rem;
  margin: 1.5rem 0;
  font-size: 0.9rem;
  color: var(--theme-foreground-muted);
}
.card h2 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; }
</style>
