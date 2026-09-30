from sqlalchemy import DateTime

from engine.db.models import ApiToken, Base

EXPECTED_TABLES = {
    "stocks",
    "candles_daily",
    "investor_trading_daily",
    "robots",
    "robot_settings",
    "watchlists",
    "signals",
    "signal_events",
    "alerts_sent",
    "paper_positions",
    "backtest_runs",
    "backtest_trades",
    "api_tokens",
    "asset_snapshots",
    "cash_flows",
    "holding_alert_state",
}


def test_all_tables_are_defined() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_every_datetime_column_is_timezone_aware() -> None:
    naive = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, DateTime) and not column.type.timezone
    ]
    assert naive == []


def test_api_token_repr_masks_token() -> None:
    token = ApiToken(provider="toss", access_token="secret-value")

    assert "secret-value" not in repr(token)
