// Observable Framework configuration.
//
// The site is fully static: `govbudget dashboard:data` precomputes aggregates
// into src/data, and the pages read them with FileAttachment. Nothing here
// queries a database at run time, so the build output can be hosted anywhere.

export default {
  title: "Dépenses publiques — Québec",
  pages: [
    {name: "Vue d'ensemble", path: "/"},
    {name: "Fournisseurs", path: "/suppliers"},
    {name: "Concurrence", path: "/competition"},
    {name: "Dépassements de coûts", path: "/overruns"},
    {name: "Sources et mises en garde", path: "/sources"},
  ],
  theme: ["air", "near-midnight"],
  header: "",
  footer: `Données : <a href="https://www.donneesquebec.ca/recherche/dataset/d23b2e02-085d-43e5-9e6e-e1d558ebfdd5">SEAO via Données Québec</a>,
    sous <a href="https://www.donneesquebec.ca/licence/">Licence Ouverte du Gouvernement du Québec</a>.
    Montants en dollars canadiens courants, non ajustés pour l'inflation.`,
  toc: true,
  search: true,
  root: "src",
  // No `base` is set on purpose. Framework emits relative asset and page links
  // ("./suppliers", "./_observablehq/..."), so the built site works unchanged at
  // a domain root or under a repository subpath like /gov-budget-audit/.
  // Verified by serving dist/ under that prefix.
};
