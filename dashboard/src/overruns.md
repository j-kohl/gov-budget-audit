# Dépassements de coûts

```js
import {money, moneyShort, number, percent, truncate} from "./components/format.js";

const overruns = await FileAttachment("data/overruns.csv").csv({typed: true});
const variance = await FileAttachment("data/variance.csv").csv({typed: true});
const summary = await FileAttachment("data/summary.json").json();
```

SEAO exige la publication de toute dépense supplémentaire excédant 10 % du
montant initial d'un contrat, avec un motif. Ces déclarations sont publiées dans
un fichier distinct des adjudications ; le dépassement n'apparaît qu'en joignant
les deux sur le numéro d'avis.

<div class="grid grid-cols-3">
  <div class="card">
    <h2>Dépenses supplémentaires déclarées</h2>
    <span class="big">${moneyShort(summary.overrun_total_cad)}</span>
    <span class="muted">au-delà des montants initiaux</span>
  </div>
  <div class="card">
    <h2>Contrats concernés</h2>
    <span class="big">${number(overruns.length)}</span>
    <span class="muted">parmi les 100 plus importants</span>
  </div>
  <div class="card">
    <h2>Dépassement médian</h2>
    <span class="big">${percent(d3.median(overruns, (d) => d.overrun_pct))}</span>
    <span class="muted">du montant adjugé</span>
  </div>
</div>

## Ampleur des dépassements

Position horizontale : montant initialement adjugé. Position verticale : le
dépassement en pourcentage de ce montant. Un point au-dessus de la ligne des
100 % a coûté plus du double du contrat initial.

```js
Plot.plot({
  width,
  height: 400,
  marginLeft: 60,
  x: {label: "Montant adjugé", type: "log", tickFormat: moneyShort, grid: true},
  y: {label: "Dépassement", tickFormat: (d) => `${d} %`, grid: true, type: "log"},
  // Plot sqrt-scales r itself; pre-squaring hides the smaller overruns.
  r: {range: [2.5, 16]},
  marks: [
    Plot.ruleY([100], {stroke: "var(--theme-foreground-muted)", strokeDasharray: "3,3"}),
    Plot.dot(overruns.filter((d) => d.overrun_pct > 0 && d.awarded_cad > 0), {
      x: "awarded_cad",
      y: "overrun_pct",
      r: "overrun_cad",
      fill: "#efb118",
      fillOpacity: 0.6,
      stroke: "var(--theme-background)",
      tip: {
        format: {x: money, y: (d) => `${d} %`, r: false, fill: false},
        channels: {
          Organisme: "buyer_name",
          Fournisseur: "supplier_name",
          Dépassement: (d) => money(d.overrun_cad)
        }
      }
    })
  ]
})
```

## Les plus importants en valeur

```js
const overrunSearch = view(Inputs.search(overruns, {
  placeholder: "Chercher un organisme, un fournisseur, un objet…",
  columns: ["buyer_name", "supplier_name", "title", "reason"]
}));
```

```js
Inputs.table(overrunSearch, {
  columns: ["buyer_name", "supplier_name", "title", "awarded_cad", "overrun_cad", "overrun_pct"],
  header: {
    buyer_name: "Organisme",
    supplier_name: "Fournisseur",
    title: "Objet",
    awarded_cad: "Adjugé",
    overrun_cad: "Dépassement",
    overrun_pct: "%"
  },
  format: {
    buyer_name: (d) => truncate(d, 34),
    supplier_name: (d) => truncate(d, 30),
    title: (d) => truncate(d, 46),
    awarded_cad: money,
    overrun_cad: money,
    overrun_pct: (d) => percent(d)
  },
  align: {awarded_cad: "right", overrun_cad: "right", overrun_pct: "right"},
  rows: 25
})
```

## Motifs invoqués

```js
const reasons = d3.rollups(
  overruns.filter((d) => d.reason),
  (v) => ({count: v.length, total: d3.sum(v, (d) => d.overrun_cad)}),
  (d) => truncate(d.reason.split(" - ")[0], 60)
).map(([reason, v]) => ({reason, ...v}))
 .sort((a, b) => b.total - a.total)
 .slice(0, 10);
```

```js
Plot.plot({
  width,
  height: 300,
  marginLeft: 320,
  x: {label: "Valeur des dépassements", tickFormat: moneyShort, grid: true},
  y: {label: null},
  marks: [
    Plot.barX(reasons, {
      x: "total",
      y: "reason",
      sort: {y: "-x"},
      fill: "#efb118",
      tip: {format: {x: money}}
    }),
    Plot.ruleX([0])
  ]
})
```

## Adjugé contre montant final

Un troisième montant existe : le montant final déclaré à la clôture du contrat,
publié dans `Contrats_*.xml`. Il diffère à la fois du montant adjugé et des
dépenses supplémentaires. Un écart négatif signifie que le contrat a coûté moins
que prévu.

```js
Inputs.table(variance, {
  columns: ["buyer_name", "supplier_name", "awarded_cad", "final_cad", "delta_cad", "delta_pct"],
  header: {
    buyer_name: "Organisme",
    supplier_name: "Fournisseur",
    awarded_cad: "Adjugé",
    final_cad: "Final",
    delta_cad: "Écart",
    delta_pct: "%"
  },
  format: {
    buyer_name: (d) => truncate(d, 34),
    supplier_name: (d) => truncate(d, 30),
    awarded_cad: money,
    final_cad: money,
    delta_cad: money,
    delta_pct: (d) => percent(d)
  },
  align: {awarded_cad: "right", final_cad: "right", delta_cad: "right", delta_pct: "right"},
  rows: 20
})
```

<div class="note">

Un dépassement n'est pas en soi une irrégularité. Les conditions de chantier
imprévues, les réclamations et les changements de portée en produisent
légitimement. Ce qui mérite examen, c'est le **motif** et sa répétition chez un
même organisme ou fournisseur — pas le dépassement isolé.

</div>

<style>
.big { display: block; font-size: 2rem; font-weight: 600; line-height: 1.2; margin: 0.2rem 0; }
.muted { display: block; font-size: 0.8rem; color: var(--theme-foreground-muted); }
.note {
  border-left: 3px solid #efb118;
  padding: 0.5rem 0 0.5rem 1rem;
  margin: 1.5rem 0;
  font-size: 0.9rem;
  color: var(--theme-foreground-muted);
}
.card h2 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; }
</style>
