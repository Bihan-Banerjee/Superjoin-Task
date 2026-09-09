"""Builders for test documents.

PDFs are generated with PyMuPDF rather than committed as fixtures, so a test can state the
text it depends on in the same file as the assertions about it. That matters most for
grounding: the whole point is that a quote must be findable in the page, and a test whose
source text lives in a binary blob cannot show that.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf


def build_pdf(path: Path, pages: list[str], *, title: str = "Test Document") -> Path:
    """Write a simple text PDF, one entry per page."""
    document = pymupdf.open()
    try:
        for content in pages:
            page = document.new_page(width=595, height=842)
            page.insert_textbox(
                pymupdf.Rect(56, 56, 539, 786),
                content,
                fontsize=10.5,
                fontname="helv",
                align=0,
            )
        document.set_metadata({"title": title})
        document.save(path)
    finally:
        document.close()
    return path


FILING_PAGES = [
    """Delhivery Limited
Annual Report for the year ended March 31, 2024

Corporate Information
Delhivery Limited is incorporated and domiciled in India. The registered office of the
Company is located at Plot 5, Sector 44, Gurugram, Haryana. The Company is listed on
NSE Limited and BSE Limited.

(All amounts in Indian Rupees in million, unless otherwise stated)

Revenue from services for the year was 81,419.7 compared with 72,246.5 in the prior year.
Express Parcel revenue for the year was 50,772.3.
The Group employed 61,527 people as at March 31, 2024.""",
    """Delhivery Limited
Management Discussion and Analysis

(All amounts in Indian Rupees in million, unless otherwise stated)

Express Parcel shipments for the year were 740 million, an increase of 11.6 per cent.
The Company operated 91 gateways as at March 31, 2024.
Adjusted EBITDA for the year was 1,459.8 compared with a loss in the prior year.""",
]

DECK_PAGES = [
    """Delhivery Q4 FY24 Earnings Presentation

FY24 performance
Revenue from services (Rs Cr)
FY24: 8,142    FY23: 7,224    FY22: 7,054

Express Parcel revenue (Rs Cr)
FY24: 5,077    FY23: 4,552

Express Parcel shipments (Mn)
FY24: 740    FY23: 663

Note: FY22 numbers are on pro forma basis.""",
]

REVISED_DECK_PAGES = [
    """Delhivery Limited Investor Update

Revenue from services for FY24 was Rs 8,500 Cr on a restated basis.
Express Parcel shipments for FY24 were 812 Mn.""",
]


# A document deliberately unlike anything in the starter corpus: a different country, a
# different fiscal convention (October to September), a different currency and scale, a
# different reporting vocabulary, and a domain that is not corporate finance at all.
#
# Nothing about it should need new code. That is the claim the brief makes the project
# defend, "it will be tested on unseen PDFs", and this is where it is checked.
UTILITY_PAGES = [
    """Cascadia Water Authority
Statistical Bulletin for the fiscal year ended September 30, 2024

All monetary amounts are stated in US Dollars in billions unless otherwise indicated.

The Authority operates under a fiscal year beginning October 1 and ending September 30.
""",
    """Operating summary

Capital expenditure for FY2024 was US$ 4.6 billion, against US$ 4.1 billion in FY2023.
Non-revenue water was 18.4% for the fiscal year ended September 30, 2024.
The Authority served 3.2 million connections as at September 30, 2024.
Treated volume reached 812 megalitres per day for FY2024.
""",
    """Restated comparatives

Capital expenditure for FY2024 is restated as US$ 4.9 billion following reclassification
of contributed assets. Non-revenue water for FY2024 is estimated at 18.9%.
""",
]

UTILITY_PROFILE = {
    "title": "Cascadia Water Authority Statistical Bulletin FY2024",
    "publisher": "Cascadia Water Authority",
    "doc_type": "statistical_bulletin",
    "subject_entity": "Cascadia Water Authority",
    "period_label": "FY2024",
    "as_of_date": "2024-09-30",
    "default_currency": "USD",
    "default_scale": "billion",
    "fiscal_convention": "us_federal",
    "reporting_basis": "consolidated",
}
