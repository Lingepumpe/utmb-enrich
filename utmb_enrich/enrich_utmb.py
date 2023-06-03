import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

import country_converter  # type: ignore[import]
import flag
import httpx
import pandas as pd
import typer
from loguru import logger
from tenacity import retry, wait_random
from tqdm import tqdm
from unidecode import unidecode

DATADIR = Path(__file__).parent.parent / "data"


# @retry(retry=retry_if_exception_type(httpx.ConnectTimeout), wait=wait_random(min=0.1, max=1.5))
@retry(wait=wait_random(min=0.1, max=1.5))
async def get_from_utmb(client: httpx.AsyncClient, url: str) -> httpx.Response:
    return await client.get(url=url)


async def enrich_utmb(participants: list) -> list:
    tasks = []
    async with httpx.AsyncClient() as client, asyncio.TaskGroup() as tg:
        for participant in participants:
            sex = "F" if participant["sex"] == "F" else "H"  # french api O_o
            nationality = participant["nationality"]
            names = participant["name"].replace(" ", "+")
            url = rf"https://api.utmb.world/search/runners?category=general&sex={sex}&ageGroup=&nationality={nationality}&limit=1&offset=0&search={names}"
            tasks.append(tg.create_task(get_from_utmb(client=client, url=url)))
    for participant, task in zip(participants, tasks, strict=True):
        result = task.result().json()
        runners = result["runners"]
        participant["utmb_index"] = 0  # to be overwritten later
        if len(runners) > 0:
            selected_runner = runners[0]  # pick first result
            startlist_name = unidecode(participant["name"]).replace("-", " ")
            utmb_name = unidecode(selected_runner["fullname"]).replace("-", " ")
            capital_startlist_names = [n for n in startlist_name.split() if n.upper() == n]
            capital_utmb_names = [n for n in utmb_name.split() if n.upper() == n]

            if (
                all(csn in capital_utmb_names for csn in capital_startlist_names)
                or all(cun in capital_startlist_names for cun in capital_utmb_names)
                or startlist_name.lower() == utmb_name.lower()
                or startlist_name.lower() == " ".join(reversed(utmb_name.lower().split()))
            ):
                participant["utmb_index"] = selected_runner["ip"]
                participant["utmb_agegroup"] = selected_runner["ageGroup"]
                participant["utmb_uri"] = f"https://utmb.world/en/runner/{selected_runner['uri']}"
                participant["utmb_name"] = selected_runner["fullname"]
            else:
                logger.warning(f"Name mismatch {startlist_name=} {utmb_name=}")

    return participants


def parse_participant_data(
    fields: list[dict[str, Any]],
    unstandard_countries: dict[str, str],
    participants: list,
    race: str,
) -> list:
    parsed_results = []
    name_fields = [
        idx
        for idx, field in enumerate(fields)
        if "name" in field["Expression"].lower() and "nation" not in field["Expression"].lower()
    ]
    [gender_field] = [
        idx for idx, field in enumerate(fields) if "gender" in field["Expression"].lower()
    ]
    [nationality_field] = [
        idx for idx, field in enumerate(fields) if "nation" in field["Expression"].lower()
    ]
    bib_field = 0
    country_conv = country_converter.CountryConverter()
    for participant in participants:
        # Try to fix broken encoding (latin-1 encoding in utf-8 files)
        for idx in range(1, len(participant)):
            with contextlib.suppress(UnicodeDecodeError, UnicodeEncodeError):
                participant[idx] = participant[idx].encode("latin-1").decode("utf-8")

        input_country = unstandard_countries.get(
            participant[nationality_field + 1], participant[nationality_field + 1]
        )
        nationality = country_conv.convert(input_country, to="iso2")
        country_name = country_conv.convert(input_country, to="name_short")
        if nationality == "not found":
            logger.warning(f"Unknown nationality {input_country=}")
            logger.error(participant)
            pretty_nationality = "🏳‍ (unknown)"
        else:
            pretty_nationality = f"{flag.flag(nationality)} ({country_name})"
        res = {"name": " ".join([participant[idx + 1] for idx in name_fields])}
        if bib_field is not None:
            res["bib"] = participant[bib_field]
        res |= {
            "sex": participant[gender_field + 1].upper().replace("W", "F").replace("H", "F"),
            "nationality": nationality,
            "flag": pretty_nationality,
            "race": race,
        }
        parsed_results.append(res)
    return parsed_results


def write_to_file(participants: list, filename: str, drop_columns: list[str]) -> None:
    if not participants:
        return
    participants.sort(key=lambda p: (-p.get("utmb_index", 0), p.get("name")))
    enhanced_df = pd.DataFrame(participants).drop(columns=drop_columns)
    enhanced_df.to_csv(DATADIR / f"{filename}.csv", index=False)
    with (DATADIR / "json" / f"{filename}.json").open("w", encoding="utf-8") as fout:
        json.dump(participants, fout, ensure_ascii=False, indent=4)


def main() -> None:
    logger.info("Enriching Runners via UTMB website")
    with (DATADIR / "runners.json").open(encoding="utf-8") as fin:
        dat = json.load(fin)

    with (DATADIR / "unstandard_countries.json").open(encoding="utf-8") as fin:
        unstandard_countries = json.load(fin)
    with (DATADIR / "unstandard_fifa_country_codes.json").open(encoding="utf-8") as fin:
        fifa_codes = json.load(fin)
        for elem in fifa_codes:
            unstandard_countries[elem["fifa"]] = elem["id"]

    races = dat["data"]
    all_participants = []
    pbar = tqdm(total=len(races) * 2)
    for race_name, orig_participants in races.items():
        participants = parse_participant_data(
            fields=dat["list"]["Fields"],
            unstandard_countries=unstandard_countries,
            participants=orig_participants,
            race=race_name,
        )
        for sex in ("M", "F"):
            participants_gender = [p for p in participants if p["sex"] == sex]
            participants_gender = asyncio.new_event_loop().run_until_complete(
                enrich_utmb(participants_gender)
            )
            filename = race_name.replace("#", "").replace(" ", "_") + f"_{sex}"
            write_to_file(
                participants_gender, filename=filename, drop_columns=["sex", "nationality", "race"]
            )
            all_participants += participants_gender
            pbar.update()
    for sex in ("M", "F"):
        write_to_file(
            [p for p in all_participants if p["sex"] == sex],
            filename=f"all_participants_{sex}",
            drop_columns=["nationality"],
        )
    logger.info("Enriching completed")


def run() -> None:  # entry point via pyproject.toml
    typer.run(main)


if __name__ == "__main__":
    typer.run(main)
