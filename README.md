# Who is strong at Innsbruck World Mountain and Trail Running Championships?

Check out the csv files in data for a scoring by UTMB index for all the athletes.

# To generate your own csv/json files

## Enrich race participation lists with UTMB data

First you need a "runners.json" file that can be gathered for a race start list:

E.g. for this race:
https://my.raceresult.com/237839/

runners.json is found from:
https://my4.raceresult.com/237839/RRPublish/data/list?key=1f610e2c77761929195c700a51a5de40&listname=00-Participants%7CStart%20list&page=participants&contest=0&r=all&l=0

## Install the environment

Assumes poetry and python-3.11 is already installed.

```shell
poetry install
poetry run utmb-enrich
```
