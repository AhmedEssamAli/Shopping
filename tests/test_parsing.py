from shopper.parsing import parse_list, parse_local


def as_pairs(items):
    return [(i.query, i.qty) for i in items]


def test_lines_and_quantities():
    text = "2 Juhayna full cream milk 1L\nrice 5kg x3\n- eggs 30 pcs\n\nTiger chips (4)"
    assert as_pairs(parse_local(text)) == [
        ("Juhayna full cream milk 1L", 2),
        ("rice 5kg", 3),
        ("eggs 30 pcs", 1),
        ("Tiger chips", 4),
    ]


def test_leading_size_is_not_quantity():
    assert as_pairs(parse_local("5kg basmati rice")) == [("5kg basmati rice", 1)]
    assert as_pairs(parse_local("1 L Juhayna milk")) == [("1 L Juhayna milk", 1)]


def test_commas_and_arabic_comma_split():
    assert len(parse_local("milk, bread، eggs; tea")) == 4


def test_no_key_uses_local_parser():
    assert as_pairs(parse_list("3x Pepsi 1L", api_key="")) == [("Pepsi 1L", 3)]


def test_brand_digits_and_decimal_commas():
    from shopper.parsing import parse_local
    items = parse_local("7up 1L, 2 milk 1,5L")
    assert [(i.qty, i.query) for i in items] == [(1, "7up 1L"), (2, "milk 1,5L")]




def test_claude_fields_become_phrases(monkeypatch):
    import anthropic
    from types import SimpleNamespace
    from shopper import parsing
    out = parsing._List(items=[parsing._Item(item="milk", brand="Juhayna", type="skimmed", size="1L", pieces=6, qty=2),
                               parsing._Item(item="eggs", pieces=30), parsing._Item(item=" ")])
    seen = {}

    class Fake:
        def __init__(self, **kw):
            parse = lambda **kw: seen.update(kw) or SimpleNamespace(stop_reason="end_turn", parsed_output=out)  # noqa: E731
            self.beta = SimpleNamespace(messages=SimpleNamespace(parse=parse))

    monkeypatch.setattr(anthropic, "Anthropic", Fake)
    items = parsing.parse_with_claude("2 علبة لبن جهينة خالي الدسم ٦×١ لتر، بيض ٣٠", "key")
    assert [(i.qty, i.query) for i in items] == [(2, "Juhayna skimmed milk 6x1L"), (1, "eggs 30 pcs")]
    assert seen["output_format"] is parsing._List and "never invent" in seen["system"] and seen["fallbacks"] == "default"
