import pytest

from ai_dnd.rules.rng import RngExhaustedError, ScriptedRng, SeededRng, SystemRng


def test_seeded_rng_repeats_the_same_sequence() -> None:
    first = SeededRng(42)
    second = SeededRng(42)

    assert [first.randint(1, 20) for _ in range(50)] == [second.randint(1, 20) for _ in range(50)]


def test_seeded_rng_differs_between_seeds() -> None:
    first = SeededRng(1)
    second = SeededRng(2)

    assert [first.randint(1, 20) for _ in range(50)] != [second.randint(1, 20) for _ in range(50)]


@pytest.mark.parametrize("rng", [SystemRng(), SeededRng(7)], ids=["system", "seeded"])
def test_values_stay_within_bounds_and_cover_them(rng: SystemRng | SeededRng) -> None:
    values = {rng.randint(1, 6) for _ in range(600)}

    assert values == {1, 2, 3, 4, 5, 6}


def test_scripted_rng_returns_values_in_order() -> None:
    rng = ScriptedRng([17, 4, 1])

    assert [rng.randint(1, 20) for _ in range(3)] == [17, 4, 1]
    assert rng.remaining == 0


def test_scripted_rng_rejects_value_out_of_range() -> None:
    rng = ScriptedRng([7])

    with pytest.raises(ValueError, match="не входит"):
        rng.randint(1, 6)
    # Значение не «съедено»: его всё ещё можно получить подходящим запросом.
    assert rng.randint(1, 8) == 7


def test_scripted_rng_fails_when_exhausted() -> None:
    rng = ScriptedRng([3])
    rng.randint(1, 6)

    with pytest.raises(RngExhaustedError):
        rng.randint(1, 6)
