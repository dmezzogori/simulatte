# Troubleshooting

## “My simulation behaves strangely / jobs don’t move”

- Make sure every component shares the same `Environment` instance (`env=...` everywhere).
- Ensure each `Server` is registered on the same `ShopFloor` (`shopfloor=...` when you create the server).

## “My due dates look wrong”

- `due_date` is **absolute simulation time**. If you want “due in 50 time units”, set `due_date=env.now + 50`.

## “Utilization looks too low”

- `utilization_rate` is `worked_time / env.now`. If you run far past the last job completion, utilization drops.

## “Parallel Runner crashes on Windows / macOS”

- Put multiprocessing code under `if __name__ == "__main__":` (see the example in [Multi-run experiments](multi-run-experiments.md)).

