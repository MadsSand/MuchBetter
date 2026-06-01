# MuchBetter

Lille Flask app til herreklub golf med:

- rundeindtastning
- stableford
- DNP hvis tom score
- dagsresultat
- sæson leaderboard
- money rank

## Udvikling (uv)

Projektet bruger [uv](https://docs.astral.sh/uv/) til afhængigheder. `pyproject.toml` + `uv.lock` er kilden til sandheden.

```bash
uv sync
uv run python main.py
```

Opdater afhængigheder:

```bash
uv lock --upgrade          # opdater lockfil
uv sync
```

## Deploy / pip

`requirements.txt` genereres fra lockfilen (til Render m.m.):

```bash
uv export --format requirements-txt --no-hashes -o requirements.txt
```

På Render kan build-kommandoen også være `uv sync --frozen` i stedet for `pip install -r requirements.txt`.
