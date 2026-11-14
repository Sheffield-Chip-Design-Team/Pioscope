"""pytest plugin: the bench fixtures and options for any project's scenario tests.

Installed with PIOScope and loaded automatically (the `pytest11` entry point), so a
project with its own ISA pack gets them without a conftest:

- options: --backend python|rtl, --sim, --dut auto|stub|tt_template|engine|module:callable,
  --rtl-engine, --rtl-src, --waves, --update-golden;
- fixtures: `bench` (run a scenario factory on the selected backend), `golden` (compare
  with, or with --update-golden rewrite, a golden file), `root` (the pytest rootdir).
"""

import pathlib

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--update-golden", action="store_true",
                     help="rewrite golden files instead of comparing")
    parser.addoption("--backend", default="python", choices=["python", "rtl"],
                     help="DUT backend for scenarios")
    parser.addoption("--sim", default="icarus", choices=["icarus", "verilator"],
                     help="HDL simulator for --backend=rtl")
    parser.addoption("--dut", default="auto",
                     help="Python DUT: 'auto' (default: the stub, or the program's ISA-pack "
                     "engine model for engine scenarios), 'stub', 'tt_template', 'engine' "
                     "or 'module:callable'")
    parser.addoption("--rtl-engine", action="store_true",
                     help="the RTL in the DUT slot is the real engine (run engine scenarios)")
    parser.addoption("--rtl-src", action="append", default=[],
                     help="RTL source file(s) for --backend=rtl (default: the packaged Tiny "
                     "Tapeout template stand-in)")
    parser.addoption("--waves", action="store_true",
                     help="RTL runs dump an FST: sim_build/pioscope_<sim>/runs/<scenario>/tb.fst")


class Golden:
    def __init__(self, update: bool) -> None:
        self.update = update

    def check(self, path: pathlib.Path, actual: str) -> None:
        if self.update or not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(actual)
            if not self.update:
                pytest.fail(f"golden file {path.name} created; re-run to compare")
            return
        assert actual == path.read_text(), (
            f"{path} differs; run pytest --update-golden if the change is intended")


@pytest.fixture
def golden(request: pytest.FixtureRequest) -> Golden:
    return Golden(bool(request.config.getoption("--update-golden")))


@pytest.fixture
def root(request: pytest.FixtureRequest) -> pathlib.Path:
    return pathlib.Path(request.config.rootpath)


# ---- scenario runner ------------------------------------------------------------------

from collections.abc import Callable  # noqa: E402

from pioscope.bench.scenario import Result, ScenarioFactory  # noqa: E402

RunScenario = Callable[[ScenarioFactory], Result]


@pytest.fixture
def bench(request: pytest.FixtureRequest) -> RunScenario:
    """Run a scenario factory on the selected backend.

    With `--dut auto` (the default), a scenario with requires_engine=True runs on the
    engine model of its program's ISA pack and any other on the stub. With an explicit
    stand-in (`--dut stub`, or RTL not flagged with --rtl-engine) engine scenarios skip.
    """
    backend = request.config.getoption("--backend")
    sim = request.config.getoption("--sim")
    dut_spec = request.config.getoption("--dut")
    rtl_engine = request.config.getoption("--rtl-engine")
    rtl_src = request.config.getoption("--rtl-src")
    waves = request.config.getoption("--waves")

    results: list[Result] = []
    request.node.bench_results = results  # read by pytest_runtest_makereport

    def run(factory: ScenarioFactory) -> Result:
        r = _run(factory)
        results.append(r)
        return r

    def _run(factory: ScenarioFactory) -> Result:
        sc = factory()
        if backend == "python":
            from pioscope.bench.backends.python import run_python
            from pioscope.dut import dut_factory

            spec = dut_spec
            if spec == "auto":
                spec = "engine" if sc.requires_engine else "stub"
            dut = dut_factory(spec, sc.image)()
            if sc.requires_engine and not dut.is_engine:
                pytest.skip(f"no engine: DUT '{dut_spec}' is a stand-in ({sc.name})")
            return run_python(sc, dut)
        if sc.requires_engine and not rtl_engine:
            pytest.skip(f"no engine: RTL slot not flagged --rtl-engine ({sc.name})")
        from pioscope.bench.backends.cocotb_rtl import run_rtl

        return run_rtl(factory, sim=sim, sources=rtl_src or None, waves=waves)

    return run


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):  # type: ignore[no-untyped-def]
    """On failure, append each bench run's timestamped event timeline to the report."""
    report = yield
    if report.when == "call" and report.failed:
        from pioscope.bench.report import render_log

        for r in getattr(item, "bench_results", []):
            report.sections.append(
                (f"bench timeline: {r.scenario} ({r.backend}, {r.cycles} cycles)",
                 render_log(r, limit=200)))
    return report
