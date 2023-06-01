import asyncio
import json
from pathlib import Path

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


def parse_participant_data(participants: list, race: str) -> list:
    parsed_results = []
    for participant in participants:
        # nationality from flag image, e.g. "[img:https://timit.pro/events/graphics/flags/png/fr_black.png]"
        if participant[4][-11:] == "_black.png]":
            nationality = participant[4][-13:-11]
        elif participant[5] == "TPE":
            nationality = "tw"
        else:
            logger.warning("Unknown country code for {participant[4:6]}")
            nationality = ""
        parsed_results.append(
            {
                "name": participant[2],
                "bib": participant[0],
                "sex": participant[3],
                "nationality": nationality,
                "flag": flag.flag(nationality) if nationality else "🏳‍",
                "race": race,
            }
        )
    return parsed_results


def write_to_file(participants: list, filename: str, drop_columns: list[str]) -> None:
    participants.sort(key=lambda p: (-p.get("utmb_index", 0), p.get("name")))
    enhanced_df = pd.DataFrame(participants).drop(columns=drop_columns)
    enhanced_df.to_csv(DATADIR / f"{filename}.csv", index=False)
    with (DATADIR / "json" / f"{filename}.json").open("w", encoding="utf-8") as fout:
        json.dump(participants, fout, ensure_ascii=False, indent=4)


def main() -> None:
    logger.info("Enriching Runners via UTMB website")
    with (DATADIR / "runners.json").open() as fin:
        dat = json.load(fin)

    races = dat["data"]
    all_participants = []
    pbar = tqdm(total=len(races) * 2)
    for race_name, orig_participants in races.items():
        participants = parse_participant_data(orig_participants, race=race_name)
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
