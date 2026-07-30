"""Canonical records produced by the parsers.

One dataclass per fact type. Parsers for different source formats converge on
these, so downstream code never sees the difference between SEAO's legacy XML
and its OCDS JSON.

The SEAO XML publishes three separate files per period, and they are three
genuinely different facts rather than three views of one:

    Avis_*.xml      a notice, its buyer, and every bidder  -> ContractAward
    Contrats_*.xml  the final settled amount per contract  -> ContractFinal
    Depenses_*.xml  spending beyond the original contract  -> ContractExpense

They join on `notice_number` (numeroseao). Keeping them apart matters: the
final amount and the supplementary spending both differ from the awarded
amount, and collapsing them would hide exactly the overruns an audit view
exists to show.

Contract facts are kept separate from budget facts. A contract award is a
commitment to a named supplier; a budget line is an appropriation against a
program. They share almost no dimensions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class ContractAward:
    """One supplier's participation in one procurement notice.

    Emitted for *every* bidder, not only the winner, because the losing bids
    are what make competition measurable. `is_winner` separates them — it comes
    from SEAO's `adjudicataire` flag, and any spending total must filter on it
    or it will count every bid as money spent.
    """

    # -- provenance, always populated -------------------------------------
    source_id: str
    source_content_hash: str
    source_format: str

    # -- identity ---------------------------------------------------------
    #: SEAO notice number. The join key across all three SEAO file types.
    notice_number: str | None = None
    #: The buying organization's own reference for the solicitation.
    buyer_reference: str | None = None
    #: Open Contracting ID. OCDS-era files only.
    ocid: str | None = None
    release_id: str | None = None
    award_id: str | None = None

    # -- buyer ------------------------------------------------------------
    buyer_name: str | None = None
    buyer_id: str | None = None
    buyer_city: str | None = None
    buyer_region: str | None = None
    #: From <municipal>. True for municipal bodies, False for provincial ones.
    is_municipal: bool | None = None

    # -- what -------------------------------------------------------------
    title: str | None = None
    description: str | None = None
    #: <type> code — how the contract was awarded. See seao_codes.NOTICE_TYPE.
    notice_type_code: str | None = None
    notice_type_label: str | None = None
    #: <nature> code — what was procured. See seao_codes.NATURE.
    nature_code: str | None = None
    nature_label: str | None = None
    procurement_method: str | None = None
    procurement_category: str | None = None
    #: SEAO's own category taxonomy, e.g. 'C03 - Autres travaux de construction'.
    #: Present in the data but not in the published specification.
    seao_category: str | None = None
    unspsc_code: str | None = None
    #: <regionlivraison> — Quebec administrative region of delivery.
    delivery_region_code: str | None = None
    delivery_region_label: str | None = None
    #: <disposition> — the legal provision allowing a non-competitive award.
    disposition_code: str | None = None

    # -- when -------------------------------------------------------------
    publication_date: date | None = None
    closing_date: date | None = None
    award_date: date | None = None
    contract_start: date | None = None
    contract_end: date | None = None

    # -- how much ---------------------------------------------------------
    #: The awarded amount for a winner, the bid amount otherwise.
    amount: float | None = None
    #: <montantssoumisunite>. Only code 1 is plain CAD — see
    #: seao_codes.SUMMABLE_UNITS. Summing across units is meaningless.
    amount_unit_code: str | None = None
    amount_unit_label: str | None = None
    currency: str | None = "CAD"

    # -- supplier ---------------------------------------------------------
    supplier_name: str | None = None
    #: Numéro d'entreprise du Québec.
    supplier_neq: str | None = None
    supplier_city: str | None = None
    supplier_region: str | None = None
    supplier_country: str | None = None
    supplier_postal_code: str | None = None

    # -- competition ------------------------------------------------------
    #: From <adjudicataire>. Only True rows represent money awarded.
    is_winner: bool | None = None
    #: From <conforme>. None means not disclosed, which is not the same as False.
    is_compliant: bool | None = None
    is_eligible: bool | None = None
    #: Count of <fournisseur> entries on the notice.
    number_of_bidders: int | None = None

    seao_url: str | None = None

    #: Fields the parser saw but could not map. Inspected to improve mappings.
    unmapped: dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        """Deduplication key: one row per notice per supplier."""
        parts = [
            self.notice_number or self.ocid or self.release_id or "",
            self.award_id or "",
            self.supplier_neq or self.supplier_name or "",
        ]
        return "|".join(parts)

    @property
    def is_summable(self) -> bool:
        """True when this row's amount is a plain CAD figure that may be summed."""
        from .seao_codes import SUMMABLE_UNITS

        if self.amount is None:
            return False
        # OCDS rows carry no unit code; their values are absolute amounts.
        if self.amount_unit_code is None:
            return True
        return self.amount_unit_code in SUMMABLE_UNITS


@dataclass
class ContractFinal:
    """The final settled amount for a contract, from Contrats_*.xml.

    Differs from the awarded amount, and is published later — sometimes years
    later. Joining this to ContractAward on notice_number gives awarded versus
    final, which is the headline variance for an audit view.
    """

    source_id: str
    source_content_hash: str
    source_format: str

    notice_number: str | None = None
    buyer_reference: str | None = None
    #: Date the contract was completed.
    final_date: date | None = None
    #: Date SEAO published the final information.
    final_publication_date: date | None = None
    final_amount: float | None = None
    supplier_name: str | None = None
    supplier_neq: str | None = None

    unmapped: dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        return "|".join([self.notice_number or "", self.supplier_neq or self.supplier_name or ""])


@dataclass
class ContractExpense:
    """Supplementary spending beyond the original contract, from Depenses_*.xml.

    SEAO requires publication of expenses exceeding 10% of the initial amount.
    These are cost overruns carrying a stated reason, which makes this the most
    directly audit-relevant record SEAO publishes.
    """

    source_id: str
    source_content_hash: str
    source_format: str

    notice_number: str | None = None
    buyer_reference: str | None = None
    expense_date: date | None = None
    expense_publication_date: date | None = None
    amount: float | None = None
    #: Free text stating why the additional spending occurred.
    description: str | None = None
    supplier_name: str | None = None
    supplier_neq: str | None = None

    unmapped: dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        return "|".join(
            [
                self.notice_number or "",
                str(self.expense_date or ""),
                self.supplier_neq or self.supplier_name or "",
                f"{self.amount or 0:.2f}",
            ]
        )


@dataclass
class BudgetLine:
    """An appropriation or expenditure against a program.

    Not yet populated by any parser — defined here so the taxonomy-mismatch
    decision recorded in the registry has a concrete place to land. The
    jurisdiction-specific dimensions are held in `dimensions` rather than as
    columns, because the federal vote/program taxonomy and Quebec's
    portefeuille/programme taxonomy do not share a shape.
    """

    source_id: str
    source_content_hash: str
    jurisdiction: str
    fiscal_year: str
    organization: str
    #: 'authorities' (approved) or 'expenditures' (spent). Never mix in one chart.
    measure: str
    amount: float
    currency: str = "CAD"
    program: str | None = None
    dimensions: dict[str, str] = field(default_factory=dict)
