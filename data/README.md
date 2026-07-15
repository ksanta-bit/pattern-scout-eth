# Data format

Put 5-minute OHLCV CSV files here.

Required columns:

```text
timestamp,open,high,low,close,volume
```

Accepted timestamp examples:

```text
2025-01-03 09:30:00
2025-01-03T09:30:00-05:00
```

If timestamps have no timezone, the bot interprets them using `timezone` from
`config.example.json`.

