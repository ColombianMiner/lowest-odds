# Lowest odds

Finds player props that PrizePicks and Pick6 offer where the major sportsbooks
(DraftKings, FanDuel, Fanatics, BetMGM, Caesars, theScore Bet, BetRivers, Hard Rock)
average -500 or better.

## Run it from your phone
1. GitHub app > this repo > Actions > **Lowest odds** > **Run workflow**.
2. Change the sports if you want (default is NBA), then tap **Run workflow**.
3. About 2 minutes later, open your reports page (bookmark it):
   `https://colombianminer.github.io/lowest-odds/`

## Run it on the Mac
```
python3 lowest_odds.py
```
First run asks for your Odds API key and saves it. `python3 lowest_odds.py newkey` swaps it.

## One-time setup
- Settings > Secrets and variables > Actions > New repository secret:
  name `ODDS_API_KEY`, value = your Odds API key.
- Settings > Pages > Source: **GitHub Actions**.
