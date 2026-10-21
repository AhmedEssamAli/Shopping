import pytest

from shopper.units import Size, parse_size, strip_sizes


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Juhayna Skimmed Milk - 1 Liter", Size(1000, "ml")),
        ("Juhayna - Milk Full Cream - 200ml", Size(200, "ml")),
        ("Juhayna - Milk Skimmed - 1.5l", Size(1500, "ml")),
        ("Juhayna - Milk Half Cream Save 10LE - 6*1l", Size(1000, "ml", 6)),
        ("Juhayna Half Cream Milk, 1 Liter, Pack of 6 - Discount%", Size(1000, "ml", 6)),
        ("Juhayna Mix Chocolate Milk, 200 ml - Pack of 27", Size(200, "ml", 27)),
        ("Bounty - 3 Milk Chocolate With Coconut - 3*50g", Size(50, "g", 3)),
        ("Basmati Rice 5kg", Size(5000, "g")),
        ("juhayna-0-fat-milk-1l", Size(1000, "ml")),
        ("Eggs 30 pcs", Size(30, "pcs")),
        ("Juhayna Skimmed Milk", None),
    ],
)
def test_parse_size(text, expected):
    assert parse_size(text) == expected


def test_save_10le_is_not_a_size():
    assert parse_size("Save 10LE") is None


def test_strip_sizes():
    out = strip_sizes("Juhayna Skimmed Milk - 1 Liter, Pack of 6").lower()
    assert "liter" not in out and "pack" not in out and "juhayna skimmed milk" in out


def test_label():
    assert Size(1000, "ml", 6).label() == "6x1L"
    assert Size(500, "g").label() == "500g"


def test_shelf_k_means_kilo():
    assert parse_size("Daria Basmati Rice - 5K") == Size(5000, "g")
    assert parse_size("Dettol 1,000 ml") == Size(1000, "ml")
