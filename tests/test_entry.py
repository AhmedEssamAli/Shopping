"""The item entry definition: fields <-> search phrase."""
from shopper.entry import Entry, compose, read_entry
from shopper.matching import best_match
from shopper.models import ItemRequest, Offer


def test_fields_make_the_phrase():
    e = Entry("milk", "Juhayna", "skimmed", "1L", 6, 2)
    assert e.phrase() == "Juhayna skimmed milk 6x1L" and e.request() == ItemRequest("Juhayna skimmed milk 6x1L", 2)
    assert e.fields() == [("Item", "milk"), ("Brand", "Juhayna"), ("Type", "skimmed"), ("Size", "1L"),
                          ("Pack", "6 pieces"), ("Buy", "2")]
    assert Entry("eggs", pieces=30).phrase() == "eggs 30 pcs"
    assert Entry("skimmed milk", "Juhayna", "skimmed").phrase() == "Juhayna skimmed milk"  # no doubled words


def test_phrase_reads_back_into_fields():
    for e in [Entry("milk", "Juhayna", "skimmed", "1L", 6, 2), Entry("rice", "Al Doha", "basmati", "5kg"),
              Entry("instant coffee", "Nescafe", "classic", "200g"), Entry("eggs", pieces=30)]:
        assert read_entry(e.phrase(), e.qty) == e
    assert read_entry("juhayna full cream milk 1 litre, pack of 6") == Entry("milk", "Juhayna", "full cream", "1L", 6)
    assert read_entry("Kiri cheese") == Entry("cheese", "Kiri")  # a lone type word stays the item
    assert read_entry("Nescafe 200g") == Entry("Nescafe", "Nescafe", "", "200g")
    assert read_entry("tahina 300g", brands=["El Rashidi"]).brand == ""
    assert read_entry("rashidi tahina", brands=["Rashidi"]).brand == "Rashidi"  # a brand you chose before


def test_form_row():
    assert compose("skimmed milk", "Juhayna", "", "1 L", "6", "2").phrase() == "Juhayna skimmed milk 6x1L"
    assert compose("milk", "", "Skimmed", "1L").type == "skimmed"
    assert compose("milk", size="1") .size == ""  # a bare number is no size
    assert compose("", "Juhayna") is None and compose("milk", qty="x").qty == 1


def test_named_fields_are_required_and_open_fields_are_open():
    offers = [Offer("a", "Juhayna Skimmed Milk 1L", 56, "u1"), Offer("a", "Almarai Skimmed Milk 1L", 52, "u2"),
              Offer("a", "Juhayna Full Cream Milk 1L", 55, "u3")]
    assert best_match(Entry("milk", "Juhayna", "skimmed", "1L").request(), offers).offer.url == "u1"
    assert best_match(Entry("milk", "", "skimmed", "1L").request(), offers).offer.url == "u2"  # any brand: cheapest


def test_suggestions_from_listings():
    from shopper.entry import suggest
    names = ["Juhayna Skimmed Milk 1L", "Juhayna Full Cream Milk 1L", "Almarai Skimmed Milk 1L", "Juhayna Skimmed Milk 200ml",
             "Lamar Milk 1.5 Litres", "Nescafe Classic Coffee 200g"]
    s = suggest(names, "milk")
    assert s["brand"] == ["Juhayna", "Almarai", "Lamar"] and s["type"][:2] == ["skimmed", "full cream"]
    assert s["size"] == ["1L", "200ml", "1.5L"]
    assert suggest(names, "")["brand"] == []
