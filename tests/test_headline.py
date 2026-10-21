from creditdash.headline import bottom_line, status_for


def test_bottom_line_is_written_from_the_statuses_alone():
    keys = ("ebitda_to_next12m_interest", "cfo_plus_interest_to_interest", "fcf_plus_interest_to_interest",
            "liquidity_to_next12m_debt_service")
    s = lambda *v: dict(zip(keys, v))
    assert bottom_line(s("not_covered", "covered", "not_covered", "covered")) == (
        "Burns cash on capex", "Earnings don't cover interest; operating cash flow does. "
        "Cash flow doesn't cover capex and interest; liquidity covers next year's interest and principal.")
    assert bottom_line(s("covered", "covered", "thin", "not_covered")) == (
        "Pays its way, but must refinance", "Cash flow only just covers capex and interest; "
        "liquidity falls short of next year's interest and principal.")
    assert bottom_line(s("covered", "covered", "covered", "thin"))[0] == "Self-funding"
    assert bottom_line(s("n/a", "n/a", "n/a", "n/a")) == (None, "")


def test_thin_status_between_one_and_two():
    assert (status_for(2.0), status_for(1.5), status_for(0.99), status_for(None)) == \
        ("covered", "thin", "not_covered", "n/a")
