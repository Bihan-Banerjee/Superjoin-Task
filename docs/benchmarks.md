# Benchmarks

Measured by `scripts/benchmark.py`, offline and with no model calls.

```
Local pipeline cost (no model calls)
------------------------------------
  parse workers: 8

  document                                        pages     parse    layout   ms/page
  01-delhivery-prospectus-2022-excerpt.pdf          100     13.5s      1.2s    146.8
  02-delhivery-annual-report-fy24-excerpt.pdf       100     16.4s      2.8s    191.5
  03-delhivery-q4-fy24-earnings-presentation.pdf     27      5.7s      0.1s    215.0
  01-india-economic-survey-2024-25-excerpt.pdf       89      8.1s      0.7s     99.3
  02-rbi-annual-report-2024-25-excerpt.pdf          100     11.2s      0.9s    120.8
  03-imf-india-2025-article-iv-excerpt.pdf           95      9.1s      0.9s    104.8

  6 documents, 511 pages in 70.5s (138.0 ms/page)
  slowest page rate: 215.0 ms/page

Many documents in one layer
---------------------------
  documents                    6
  pages                        511
  facts                        3600
  relations                    583
  of those, across documents   427 (73%)

The schema growing as documents arrive
--------------------------------------
  document                                        new measures  running total
  PROSPECTUS                                               312            312
  Delhivery Limited Annual Report 2023-24                  495            807
  Earnings Presentation Q4 & FY24                           58            865
  Economic Survey 2024-25                                  166           1031
  Annual Report 2024-25                                    197           1228
  India: 2025 Article IV Consultation-Press Rele           179           1407

  measures seen in more than one document: 100 of 1407
  qualifier dimensions discovered:         3

What ingest cost
----------------
  model calls                  587
  served from cache            451 (77%)
  median latency               3417 ms
  facts per model call         6.1

Incremental ingest
------------------
  Adding a document pairs its facts against the corpus and leaves existing
  facts untouched. Cost is proportional to the new document, not the layer:
    PROSPECTUS                                        777 facts
    Delhivery Limited Annual Report 2023-24          1077 facts
    Earnings Presentation Q4 & FY24                   378 facts
    Economic Survey 2024-25                           307 facts
    Annual Report 2024-25                             665 facts
    India: 2025 Article IV Consultation-Press Rele    396 facts
```
