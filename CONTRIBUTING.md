# Contributing

Thanks for helping. The Daily Times is small on purpose: one Python package, no build step,
no JavaScript framework. Please keep it that way.

## Set up

```bash
git clone https://github.com/prasanthsasikumar/dailytimes && cd dailytimes
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest -q
DAILYTIMES_DEV=1 .venv/bin/dailytimes serve       # the website on http://localhost:8787
./scripts/demo.sh                                  # end to end, outputs in ./out
```

## Good first contributions

- **Your printer works (or doesn't).** Open a *Printer report* issue with the model, how it's
  connected and the profile you used. A photo of the test page helps a lot.
- **A printer profile** (`src/dailytimes/profiles/*.yaml`) for paper we don't ship yet.
- **An adapter** for a new kind of printer: subclass `dailytimes.adapters.Adapter`, set `name`
  and `formats`, implement `send(job)`, register it, and add a dry-run test in
  `tests/test_adapters.py`. Nothing else needs to change.
- **A source for the auto edition** in `src/dailytimes/autobrief/sources.py`: keyless, public,
  quoted exactly (no summarising), failing quietly. Add an offline parser test.
- **A presence backend** (`src/dailytimes/agent/presence.py`).

## Ground rules

- **Never invent content.** Anything that ends up on the page must come from the user's AI or
  be quoted from a source with its link.
- **Privacy first.** Don't store anything new about users without a reason, a retention limit
  and a line in `PRIVACY.md`. No analytics, no third-party scripts.
- **Tests with every change**; `pytest -q` and `ruff check --select F,E9 src tests` must pass.
- Images in the README come from `scripts/make_images.py` and `scripts/screenshots.py`;
  regenerate them rather than editing by hand. Sample content stays fictional.
