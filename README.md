# Perth Airfare Tracker

This repository is a small end to end data pipeline that watches the cost of flying out of Perth. Every day it asks the Aviasales Data API, provided by Travelpayouts, what a return ticket costs from Perth to 43 major cities around the world, saves the answers, and slowly builds up a history of airfares that you can actually analyse.

## The question it answers

If I book a trip from Perth about a month in advance, what does it cost, and how does that price move over time? Each daily snapshot looks for a departure 30 days ahead with a 7 day stay, one adult, prices in Australian dollars. Because the booking window is always the same, prices from different days are directly comparable, which is what makes the time series useful.

## How the pipeline works

A scheduled GitHub Actions workflow wakes up once a day, early morning Perth time. It runs `src/fetch_prices.py`, which queries the Aviasales price cache one route at a time until all 43 destinations are done. For every route it keeps the cheapest ticket it finds and records the price, the airline, how many stops each direction has, and how long the flights take.

The data source is a cache of real searches made by Aviasales users in the previous two days, so a route only has a price when somebody recently searched for it. The script handles this in two steps. It first asks for the exact target dates, and when the cache has nothing for them, it widens the question to the whole departure month and marks the row `ok_flex` instead of `ok`. That way thin routes still produce a usable price most days, and you can always separate strict observations from flexible ones in your analysis.

Aviasales also keeps a separate cache for every market it operates in, roughly one per country website. A route that nobody searched on the Australian site may well have been searched on the American, British or Russian one. So the month fallback walks through the au, us, gb and ru caches in that order and keeps the first price it finds, recording which market supplied it in the `market` column. Quiet routes will still have gaps on some days, which is normal for cache based data, and the popular routes fill in densely from day one.

The results land in two places. A tidy row per route per day is appended to `data/prices.csv`, which is the dataset you will use for analysis. The full raw API responses are archived under `data/raw/` as compressed JSON, one folder per day, so if you ever want a field that the tidy table does not carry, nothing has been thrown away. The workflow then commits both back to this repository, so git history doubles as a free audit log of every snapshot.

If a single route fails or returns nothing, the run carries on and simply marks that row so gaps are visible instead of silent. The run only fails when nothing at all could be fetched, which usually means a credentials or outage problem worth knowing about.

## What is in the repository

`config/routes.csv` holds the list of destinations with their city, country and region labels. Edit this file to track more places or fewer.

`src/fetch_prices.py` is the whole ingestion job, deliberately kept to one readable file with no framework around it.

`.github/workflows/daily-ingest.yml` is the schedule. It installs Python, runs the fetch script with credentials taken from repository secrets, and pushes any new data.

`data/prices.csv` grows by one snapshot per day. Each row is one destination on one day: snapshot date, route, travel dates, the cheapest total fare, currency, airline, stop counts, durations, how many offers came back, and a status flag. The status is `ok` for exact date matches, `ok_flex` when the price comes from the wider month, `no_data` when the cache was empty, and `error` when the request itself failed.

`dashboard/app.py` is a Streamlit app that reads the dataset and shows the latest prices for every destination, a price trend chart for cities you pick, a sortable route table, and a small data health panel.

## The destinations

Six cities in Australia and New Zealand, eight in Southeast Asia, eight in East Asia, three in South Asia, three in the Middle East, eight in Europe, two in Africa, and five in North America. The full list lives in `config/routes.csv`.

## Things worth knowing

The Aviasales Data API is free for Travelpayouts partners and needs only a token. Prices come from cached user searches rather than a live fare search, so they reflect what real people recently saw, and quiet routes can have gaps. Popular routes from Perth such as Bali, Singapore and London are searched constantly and give dense data.

Prices are the cheapest cached ticket for the route, which is usually but not always the absolute cheapest fare on the market for that day. What matters for analysis is that the measurement is consistent from day to day, and the status flag tells you exactly how each number was obtained.

## Ideas once data accumulates

After a few weeks of snapshots you can start asking real questions. Which destinations are getting cheaper or dearer? How volatile is each route? Do certain regions move together? Is there a weekly pattern in fares? The tidy CSV loads straight into pandas, a notebook, or the dashboard.
