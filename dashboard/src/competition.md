# Concurrence

```js
import {money, moneyShort, number, percent} from "./components/format.js";

const competition = await FileAttachment("data/competition.csv").csv({typed: true});
const buyers = await FileAttachment("data/buyers.csv").csv({typed: true});
```

Trois modes d'adjudication, harmonisés entre les deux ères de publication :
**appel d'offres public** (annoncé publiquement), **sur invitation** (fournisseurs
sélectionnés) et **gré à gré** (attribué sans mise en concurrence).

## Valeur adjugée par mode et par année

```js
Plot.plot({
  width,
  height: 340,
  marginLeft: 60,
  x: {label: null, tickFormat: "d"},
  y: {label: "Valeur adjugée", tickFormat: moneyShort, grid: true},
  color: {legend: true, domain: ["Appel d'offres public", "Sur invitation", "Gré à gré", "Autre"],
          range: ["#4269d0", "#97bbf5", "#efb118", "#9498a0"]},
  marks: [
    Plot.barY(competition, {x: "year", y: "total_cad", fill: "method",
                            order: ["Appel d'offres public", "Sur invitation", "Gré à gré", "Autre"],
                            tip: {format: {y: money}}}),
    Plot.ruleY([0])
  ]
})
```

## Nombre de contrats par mode

Le gré à gré représente une part bien plus grande du **nombre** de contrats que
de leur **valeur** : beaucoup de petits contrats attribués directement, quelques
très gros passés en appel d'offres public.

```js
Plot.plot({
  width,
  height: 340,
  marginLeft: 60,
  x: {label: null, tickFormat: "d"},
  y: {label: "Contrats adjugés", grid: true},
  color: {legend: true, domain: ["Appel d'offres public", "Sur invitation", "Gré à gré", "Autre"],
          range: ["#4269d0", "#97bbf5", "#efb118", "#9498a0"]},
  marks: [
    Plot.barY(competition, {x: "year", y: "awards", fill: "method",
                            order: ["Appel d'offres public", "Sur invitation", "Gré à gré", "Autre"],
                            tip: true}),
    Plot.ruleY([0])
  ]
})
```

## Écart entre part du nombre et part de la valeur

```js
const byYear = d3.rollups(competition, (v) => {
  const totalValue = d3.sum(v, (d) => d.total_cad);
  const totalCount = d3.sum(v, (d) => d.awards);
  const direct = v.filter((d) => d.method_key === "direct");
  return {
    valueShare: totalValue ? d3.sum(direct, (d) => d.total_cad) / totalValue : 0,
    countShare: totalCount ? d3.sum(direct, (d) => d.awards) / totalCount : 0
  };
}, (d) => d.year).map(([year, v]) => [
  {year, mesure: "Part de la valeur", share: v.valueShare},
  {year, mesure: "Part du nombre", share: v.countShare}
]).flat();
```

```js
Plot.plot({
  width,
  height: 300,
  marginLeft: 60,
  x: {label: null, tickFormat: "d"},
  y: {label: "Part attribuée de gré à gré", percent: true, grid: true},
  color: {legend: true, range: ["#efb118", "#4269d0"]},
  marks: [
    Plot.lineY(byYear, {x: "year", y: "share", stroke: "mesure", strokeWidth: 2,
                        tip: {format: {y: (d) => percent(100 * d)}}}),
    Plot.ruleY([0])
  ]
})
```

## Organismes les plus dépendants du gré à gré

Parmi les 100 plus gros acheteurs, ceux dont la plus forte proportion de contrats
est attribuée sans mise en concurrence.

```js
const topDirect = buyers
  .filter((d) => d.awards >= 20)
  .map((d) => ({...d, share: d.direct_awards / d.awards}))
  .sort((a, b) => b.share - a.share)
  .slice(0, 15);
```

```js
Plot.plot({
  width,
  height: 380,
  marginLeft: 260,
  x: {label: "Part des contrats de gré à gré", percent: true, grid: true, domain: [0, 1]},
  y: {label: null},
  marks: [
    Plot.barX(topDirect, {x: "share", y: "buyer_name", sort: {y: "-x"}, fill: "#efb118",
                          tip: {format: {x: (d) => percent(100 * d)},
                                channels: {Contrats: "awards", Valeur: (d) => money(d.total_cad)}}}),
    Plot.ruleX([0])
  ]
})
```

<div class="note">

Seuls les organismes ayant adjugé au moins 20 contrats figurent ici, pour éviter
qu'un acheteur ayant passé deux contrats de gré à gré n'apparaisse à 100 %.

</div>

<style>
.note {
  border-left: 3px solid var(--theme-foreground-focus);
  padding: 0.5rem 0 0.5rem 1rem;
  margin: 1.5rem 0;
  font-size: 0.9rem;
  color: var(--theme-foreground-muted);
}
</style>
