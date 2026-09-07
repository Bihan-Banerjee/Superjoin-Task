# Sample corpora

Two independent corpora, copied unchanged from the assignment's starter datasets. They are committed so
the pipeline can be reproduced end to end without chasing links.

Both are public documents. Original sources are listed below; the excerpts were curated by the assignment
authors, not by this project.

## `delhivery/` — one company, three disclosure formats

| File | Pages | Original source |
|---|---|---|
| `01-delhivery-prospectus-2022-excerpt.pdf` | 100 | [Prospectus, May 2022](https://www.delhivery.com/wp-content/uploads/2022/05/Delhivery-Limited-Prospectus-1-min.pdf) |
| `02-delhivery-annual-report-fy24-excerpt.pdf` | 100 | [Annual Report FY24](https://www.delhivery.com/uploads/2024/08/Annual_Report_FY24.pdf) |
| `03-delhivery-q4-fy24-earnings-presentation.pdf` | 27 | [Q4 FY24 earnings deck (BSE)](https://www.bseindia.com/xml-data/corpfiling/AttachHis/d70668ee-4f13-485e-ba19-62bec5116a59.pdf) |

The interesting property of this corpus is that the same metrics are restated across three formats with
different reporting scales, periods and bases — the annual report denominates in Indian Rupees *million*,
the earnings deck in *crore*.

## `india-macroeconomy/` — one subject, three publishers

| File | Pages | Original source |
|---|---|---|
| `01-india-economic-survey-2024-25-excerpt.pdf` | 89 | [Economic Survey 2024-25](https://www.indiabudget.gov.in/budget2025-26/economicsurvey/doc/echapter.pdf) |
| `02-rbi-annual-report-2024-25-excerpt.pdf` | 100 | [RBI Annual Report 2024-25](https://rbidocs.rbi.org.in/rdocs/AnnualReport/PDFs/0ANNUALREPORT202425DA4AE08189C848C8846718B080F2A0A9.PDF) |
| `03-imf-india-2025-article-iv-excerpt.pdf` | 95 | [IMF India 2025 Article IV](https://www.imf.org/en/publications/cr/issues/2025/11/25/india-2025-article-iv-consultation-press-release-staff-report-and-statement-by-the-572056) |

Here the same indicators are published by three institutions at different data vintages, with fiscal years
written as `2024-25`, `2024/25` and `FY25`, and with estimate / projection / actual columns side by side.

## Note on page numbers

Page numbers printed inside the excerpts follow the original documents and jump between retained sections.
The pipeline records both the PDF page index and the printed label, and the UI shows the PDF index because
that is what resolves to a rendered page.
