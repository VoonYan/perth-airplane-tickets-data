"""Streamlit dashboard for Perth airfare tracking.

Built to run live on Streamlit Community Cloud. It always reads the freshest
data straight from the GitHub repository, so every daily commit made by the
pipeline shows up on the live dashboard within the cache window. If GitHub is
unreachable it falls back to the local checkout copy.

Run locally with:  streamlit run dashboard/app.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

LOCAL_CSV = Path(__file__).resolve().parents[1] / "data" / "prices.csv"
# The live data source. Update if the repository owner or name changes.
GITHUB_CSV = (
    "https://raw.githubusercontent.com/VoonYan/perth-airplane-tickets-data/main/data/prices.csv"
)

st.set_page_config(page_title="Perth Airfare Tracker", page_icon="✈️", layout="wide")


@st.cache_data(ttl=1800)
def load_data() -> pd.DataFrame:
    """Remote first so the live app always shows the latest committed data."""
    try:
        df = pd.read_csv(GITHUB_CSV)
    except Exception:  # noqa: BLE001
        if not LOCAL_CSV.exists():
            raise
        df = pd.read_csv(LOCAL_CSV)
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    df["price_total"] = pd.to_numeric(df["price_total"], errors="coerce")
    return df


def main() -> None:
    st.title("✈️ Perth Airfare Tracker")
    st.caption(
        "Return fares from Perth, collected daily for departures about 30 days "
        "ahead with a 7 day stay. Prices in AUD from cached Aviasales searches "
        "via the Travelpayouts Data API."
    )

    try:
        df = load_data()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not load data yet: {exc}")
        st.info("The dataset appears after the first successful daily run.")
        return

    ok = df[df["status"].isin(["ok", "ok_flex"])].copy()
    if ok.empty:
        st.warning("No successful price rows yet. Check back after the next run.")
        return

    # Sidebar filters
    regions = sorted(ok["region"].unique())
    chosen_regions = st.sidebar.multiselect("Regions", regions, default=regions)
    ok = ok[ok["region"].isin(chosen_regions)]
    if ok.empty:
        st.warning("No data for the selected regions.")
        return

    latest_date = ok["snapshot_date"].max()
    latest = ok[ok["snapshot_date"] == latest_date]

    # Headline metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Latest snapshot", latest_date.strftime("%d %b %Y"))
    col2.metric("Routes priced", f"{latest['destination'].nunique()}")
    cheapest = latest.loc[latest["price_total"].idxmin()]
    col3.metric("Cheapest return", f"${cheapest['price_total']:,.0f}", cheapest["city"])
    priciest = latest.loc[latest["price_total"].idxmax()]
    col4.metric("Priciest return", f"${priciest['price_total']:,.0f}", priciest["city"])

    st.divider()

    # Latest prices by destination
    st.subheader("Latest prices by destination")
    bar = px.bar(
        latest.sort_values("price_total"),
        x="price_total",
        y="city",
        color="region",
        orientation="h",
        labels={"price_total": "Return fare (AUD)", "city": ""},
        height=max(420, 22 * len(latest)),
    )
    bar.update_layout(legend_title_text="")
    st.plotly_chart(bar, width="stretch")

    # Price trends
    st.subheader("Price trend over time")
    default_cities = (
        latest.sort_values("price_total")["city"].head(5).tolist()
    )
    cities = st.multiselect(
        "Destinations to plot",
        sorted(ok["city"].unique()),
        default=default_cities,
    )
    if cities:
        trend = ok[ok["city"].isin(cities)]
        line = px.line(
            trend.sort_values("snapshot_date"),
            x="snapshot_date",
            y="price_total",
            color="city",
            markers=True,
            labels={"snapshot_date": "Snapshot date", "price_total": "Return fare (AUD)"},
        )
        line.update_layout(legend_title_text="")
        st.plotly_chart(line, width="stretch")

    # Route detail table
    st.subheader("Route details, latest snapshot")
    detail = latest[
        [
            "city",
            "country",
            "region",
            "price_total",
            "carrier",
            "outbound_stops",
            "return_stops",
            "outbound_duration_min",
            "return_duration_min",
        ]
    ].sort_values("price_total")
    detail = detail.rename(
        columns={
            "price_total": "fare_aud",
            "outbound_duration_min": "out_minutes",
            "return_duration_min": "back_minutes",
        }
    )
    st.dataframe(detail, width="stretch", hide_index=True)

    # Data health
    with st.expander("Data health"):
        health = (
            df.groupby("status")["destination"].count().rename("rows").reset_index()
        )
        st.dataframe(health, hide_index=True)
        st.caption(
            f"{len(df):,} rows total across "
            f"{df['snapshot_date'].nunique()} snapshot days."
        )


if __name__ == "__main__":
    main()
