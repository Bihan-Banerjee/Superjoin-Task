from app.core.text import collapse_whitespace, contains_normalized, locate, normalize


def test_normalize_folds_ligatures_and_smart_punctuation():
    result = normalize("The “ofﬁce”: 12 months")
    assert '"office"' in result.text
    assert "12 months" in result.text


def test_normalize_repairs_line_break_hyphenation():
    result = normalize("well-\ncapitalised banks")
    assert "wellcapitalised banks" in result.text


def test_index_map_points_back_at_original_characters():
    source = "Revenue  from   services"
    result = normalize(source)
    start = result.text.index("services")
    source_start, source_end = result.to_source_span(start, start + len("services"))
    assert source[source_start:source_end] == "services"


def test_locate_finds_an_exact_quote_and_reports_it_as_exact():
    page = "Revenue from services grew 13% to Rs. 8,142 Cr in FY24."
    match = locate(page, "grew 13% to Rs. 8,142 Cr")
    assert match.exact
    assert page[match.start : match.end] == "grew 13% to Rs. 8,142 Cr"


def test_locate_survives_the_whitespace_pdf_extraction_introduces():
    page = "Revenue  from\nservices grew 13%  to  Rs. 8,142 Cr"
    match = locate(page, "Revenue from services grew 13% to Rs. 8,142 Cr")
    assert match.found
    assert "8,142" in page[match.start : match.end]


def test_locate_recovers_a_quote_the_model_altered_slightly():
    page = "The Company is listed on NSE Limited and BSE Limited as of March 2024."
    match = locate(page, "The Company is listed on the NSE Limited and BSE Limited")
    assert match.found
    assert not match.exact
    assert "BSE Limited" in page[match.start : match.end]


def test_locate_rejects_a_quote_that_is_not_on_the_page():
    page = "Revenue from services grew 13% in FY24."
    assert not locate(page, "The registered office is located in Gurugram, Haryana").found


def test_locate_handles_hyphenated_wrap_between_quote_and_page():
    page = "improved asset quality and well-\ncapitalised banks supported activity"
    assert locate(page, "well-capitalised banks supported activity").found


def test_contains_normalized_ignores_case_and_spacing():
    assert contains_normalized("Revenue  from\nServices", "revenue from services")
    assert not contains_normalized("Revenue from services", "operating profit")


def test_collapse_whitespace():
    assert collapse_whitespace("  a \n b\tc  ") == "a b c"
