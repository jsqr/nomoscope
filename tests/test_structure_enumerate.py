from nomoscope.structure.enumerate import Enumerator, increases, read, same_number, succeeds


def test_read():
    assert read("12", "arabic") == Enumerator("arabic", 12)
    assert read("52.1", "arabic") == Enumerator("arabic", 52, 1)
    assert read("C", "upper_alpha") == Enumerator("upper_alpha", 3)
    assert read("aa", "lower_alpha") == Enumerator("lower_alpha", 27)
    assert read("ab", "lower_alpha") is None
    assert read("iv", "lower_roman") == Enumerator("lower_roman", 4)
    assert read("XIV", "upper_roman") == Enumerator("upper_roman", 14)
    assert read("IIII", "upper_roman") is None
    assert read("a", "arabic") is None


def test_i_reads_both_ways():
    assert read("i", "lower_alpha") == Enumerator("lower_alpha", 9)
    assert read("i", "lower_roman") == Enumerator("lower_roman", 1)


def test_succeeds():
    a = Enumerator("arabic", 52)
    assert succeeds(a, Enumerator("arabic", 53))
    assert succeeds(a, Enumerator("arabic", 52, 1))
    assert succeeds(Enumerator("arabic", 52, 1), Enumerator("arabic", 52, 2))
    assert succeeds(Enumerator("arabic", 52, 2), Enumerator("arabic", 53))
    assert not succeeds(a, Enumerator("arabic", 54))
    assert not succeeds(a, Enumerator("lower_alpha", 53))


def test_increases():
    assert increases(Enumerator("arabic", 3), Enumerator("arabic", 9))
    assert not increases(Enumerator("arabic", 9), Enumerator("arabic", 9))


def test_same_number():
    assert same_number("01", "1")
    assert same_number("A216", "A216")
    assert not same_number("10", "1")
    assert not same_number("AG", "8")
    assert not same_number(None, "1")
