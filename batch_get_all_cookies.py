from waivek import Timer   # Single Use
timer = Timer()
from waivek import Code    # Multi-Use
from waivek import handler # Single Use
from waivek import ic, ib     # Multi-Use, import time: 70ms - 110ms
from waivek import rel2abs
import requests
from bs4 import BeautifulSoup
from collections import Counter
Code; ic; ib; rel2abs

# CREATE TABLE cookies (
#     internal_id TEXT PRIMARY KEY, [individual]
#     name TEXT,
#     rarity TEXT,
#     type TEXT,
#     position TEXT,
#     cooldown INTEGER, [individual]
#     url TEXT, [individual]
#     image_url TEXT [individual]
# ) STRICT;

def gen1():
    url = "https://cookierunkingdom.fandom.com/wiki/List_of_Cookies?action=edit"
    rarity_map = { "c": "common", "r": "rare", "e": "epic", "l": "legendary", "s": "special", "u": "unique", "se": "super epic", "d": "dragon", "a": "ancient" }
    position_map = { "f": "front", "m": "middle", "r": "rear" }
    type_map = { "h": "healing", "s": "support", "d": "defence", "c": "charge", "a": "ambush", "m": "magic", "b": "bomber", "r": "ranged" }

    response = requests.get(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    textarea = soup.find("textarea")
    assert textarea
    table = textarea.text
    cookies = table.split("\n")
    cookie_lines = [ cookie for cookie in cookies if "cookie=" in cookie ]
    table = []
    START = "{{Loc card|"
    END = "}}"
    key_counter = Counter()
    skip_keys = [ "image", "size" ]
    for line in cookie_lines:
        assert line.startswith(START) and line.endswith(END)
        line = line[len(START):-len(END)]
        items = line.split("|")
        cookie = {}
        if '=?' in line or '=bts' in line:
            continue
        for item in items:
            key, value = item.split("=")
            if key in skip_keys:
                continue
            if key == "rarity":
                cookie[key] = rarity_map[value]
            elif key == "position":
                cookie[key] = position_map[value]
            elif key == "type":
                cookie[key] = type_map[value]
            else:
                cookie[key] = value

        table.append(cookie)
    return table

def batch_get_all_cookies():
    url = "https://cookierunkingdom.fandom.com/wiki/List_of_Cookies"
    response = requests.get(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    divs = soup.select("div.loccard")
    assert divs
    cookies = []
    # remove all style attribute assignments, recursively
    for div in divs:
        div.attrs.pop("style", None)
        for tag in div.find_all(True):
            tag.attrs.pop("style", None)
    # print(divs[1].prettify())

    table = []
    D = {}
    for div in divs:
        classes = div.attrs["class"]
        name_without_spaces = classes[-1].replace("pi-theme-", "")
        a = div.select_one("a")
        assert a
        url = a.attrs["href"]
        title = a.attrs["title"]
        if name_without_spaces in [ "villain", "mermaid", "sonic" ]:
            # villain -> poison mushroom cookie, pomegranate cookie
            # mermaid -> crimson coral cookie
            # sonic -> tails cookie, sonic cookie
            name_without_spaces = title.replace("Cookie", "").replace(" ", "").lower().strip()
        table.append({ "name": name_without_spaces, "url": url, "title": title })

        D[name_without_spaces] = { "url": url, "title": title }
    t1 = gen1()
    ic(table)
    ic(t1)
    
    for row in t1:
        name = row["cookie"]
        row["url"] = D[name]["url"]
        row["title"] = D[name]["title"]
    return t1


if __name__ == "__main__":
    with handler():
        batch_get_all_cookies()

