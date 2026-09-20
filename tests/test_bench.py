"""The benchmark itself: small runs, so it keeps working and keeps telling the truth."""

import asyncio

from pricetime.bench import (
    Measurement,
    environment,
    main,
    measure_echo,
    measure_engine,
    measure_fix,
    render,
    synthetic_commands,
)


def test_percentiles_come_from_the_samples_themselves() -> None:
    measurement = Measurement(name="test", service=list(range(1, 1001)), response=[])

    percentiles = measurement.percentiles(measurement.service)

    assert percentiles["p50"] == 0.5  # the 500th of 1000 nanosecond samples, in microseconds
    assert percentiles["p99"] == 0.99
    assert percentiles["max"] == 1.0


def test_an_empty_measurement_reports_zeros() -> None:
    assert Measurement(name="nothing").percentiles([]) == {
        "p50": 0.0,
        "p99": 0.0,
        "p99.9": 0.0,
        "max": 0.0,
    }


def test_the_load_is_the_same_every_time_for_a_seed() -> None:
    assert synthetic_commands(7, 50) == synthetic_commands(7, 50)
    assert synthetic_commands(7, 50) != synthetic_commands(8, 50)


def test_the_engine_run_measures_every_order_after_the_warm_up() -> None:
    measurement = measure_engine(rate=2_000, seconds=0.05, warmup=0.05)

    assert len(measurement.service) == 100
    assert len(measurement.response) == 100
    assert all(sample > 0 for sample in measurement.service)


def test_pausing_the_collector_still_measures_the_same_number_of_orders() -> None:
    measurement = measure_engine(rate=2_000, seconds=0.05, warmup=0.05, collect_garbage=False)

    assert len(measurement.service) == 100


def test_the_baseline_echo_runs_over_a_real_socket() -> None:
    measurement = asyncio.run(measure_echo(rate=200, seconds=0.1, warmup=0.05))

    assert len(measurement.service) == 20
    assert all(sample > 0 for sample in measurement.response)


def test_a_round_trip_run_measures_orders_end_to_end() -> None:
    measurement = asyncio.run(measure_fix(rate=100, seconds=0.2, warmup=0.1, force_to_disk=False))

    assert len(measurement.service) == 20
    assert "no disk sync" in measurement.name


def test_the_report_names_the_machine_and_the_commit() -> None:
    where = environment()

    assert where["python"]
    assert where["commit"]
    assert where["garbage_collector"] in {"enabled", "disabled"}


def test_the_table_shows_both_kinds_of_latency() -> None:
    measurement = Measurement(name="engine", service=[1_000], response=[2_000])

    table = render([measurement.report()], environment())

    assert "service time" in table
    assert "response time" in table
    assert "microseconds" in table


def test_the_command_line_runs_a_short_engine_only_measurement(capsys) -> None:  # type: ignore[no-untyped-def]
    exit_code = main(["--engine-only", "--rate", "2000", "--seconds", "0.05", "--warmup", "0.05"])

    assert exit_code == 0
    assert "engine at 2000/s" in capsys.readouterr().out
