import pytest

from overlaybuilder import fips


def test_state_fp():
    assert fips.state_fp("MN") == "27"
    assert fips.state_fp("minnesota") == "27"
    assert fips.state_fp("27") == "27"


def test_abbr_for_fp():
    assert fips.abbr_for_fp("27") == "MN"


def test_resolve_by_fips_no_network():
    # passing county name avoids the gazetteer download
    sfp, cfp, name, abbr = fips.resolve("", county="Chisago", fips="27025")
    assert (sfp, cfp, abbr, name) == ("27", "025", "MN", "Chisago")


def test_bad_fips():
    with pytest.raises(ValueError):
        fips.resolve("", fips="2702")
