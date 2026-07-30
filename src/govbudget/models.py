"""Canonical records produced by the parsers.

One dataclass per fact type. Parsers for different source formats converge on
these, so downstream code never sees the difference between SEAO's legacy XML
and its OCDS JSON.

Contract awards are kept deliberately separate from budget and expenditure
facts: a contract award is a commitment to a named supplier, while a budget
line is an appropriation against a program. They share almost no dimensions
and merging them would force a lowest-common-denominator schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class ContractAward:
    """A contract awarded by a public body to a supplier.

    Populated from SEAO (Quebec) and federal proactive disclosure. Fields absent
    in a given source stay None rather than being guessed at.
    """

    # -- provenance, always populated -------------------------------------
    source_id: str
    source_content_hash: str
    source_format: str

    # -- identity ---------------------------------------------------------
    #: Open Contracting ID. Present only in the post-March-2021 OCDS era.
    ocid: str | None = None
    #: SEAO notice number. The stable identifier across both SEAO eras.
    notice_number: str | None = None
    release_id: str | None = None
    award_id: str | None = None

    # -- buyer ------------------------------------------------------------
    buyer_name: str | None = None
    buyer_id: str | None = None
    #: ministere / reseau-education / sante-services-sociaux / municipal
    buyer_category: str | None = None

    # -- what ------------------------------------------------------------
    title: str | None = None
    description: str | None = None
    procurement_method: str | None = None
    procurement_category: str | None = None
    unspsc_code: str | None = None

    # -- when ------------------------------------------------------------
    publication_date: date | None = None
    award_date: date | None = None
    contract_start: date | None = None
    contract_end: date | None = None

    # -- how much ---------------------------------------------------------
    amount: float | None = None
    currency: str | None = "CAD"

    # -- supplier ---------------------------------------------------------
    supplier_name: str | None = None
    #: Numéro d'entreprise du Québec. Absent from federal disclosure entirely.
    supplier_neq: str | None = None
    supplier_city: str | None = None
    supplier_region: str | None = None

    # -- competition ------------------------------------------------------
    number_of_bidders: int | None = None

    #: Fields the parser saw but could not map. Inspected to improve mappings.
    unmapped: dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        """Best available deduplication key.

        Notice number plus supplier is the most reliable across both SEAO eras;
        OCID alone is not, because one OCID can carry several awards.
        """
        parts = [
            self.ocid or self.notice_number or self.release_id or "",
            self.award_id or "",
            self.supplier_name or "",
        ]
        return "|".join(parts)


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
