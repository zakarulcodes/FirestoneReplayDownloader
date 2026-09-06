#!/usr/bin/env python3
"""Convert HDT .hdtreplay / .bgreplay files to HSReplay XML for the /play viewer.

HDT stores replays as a zip containing the raw Hearthstone Power.log
(.hdtreplay -> output_log.txt; .bgreplay -> power.log + meta.json). The /play
viewer needs HSReplay XML, so this converts using HearthSim's log parser. For
Battlegrounds logs, which register players by id but not by name, it seeds the
human player so hslog can serialize. Drop the resulting .xml into /play.

Install once:  pip install hsreplay
Usage:
  python hdt_to_xml.py FILE.hdtreplay [more ...]   # -> FILE.xml beside each input
  python hdt_to_xml.py DIR                         # convert every replay in DIR
  python hdt_to_xml.py                             # default: HDT Replays + BGReplays
  python hdt_to_xml.py DIR -o OUTDIR               # write .xml files into OUTDIR
"""
import argparse
import io
import os
import re
import zipfile

from hslog import LogParser
from hsreplay.document import HSReplayDocument
from hslog.exceptions import MissingPlayerData


def convert_log(log_text: str) -> str:
    parser = LogParser()
    parser.read(io.StringIO(log_text))
    parser.flush()
    friendly_name = None
    try:
        doc = HSReplayDocument.from_parser(parser, build=None)
    except MissingPlayerData as e:
        # HDT Battlegrounds logs register players by id but never by name, so hslog
        # can't link them. Seed the human player (first with a non-zero GameAccountId)
        # using the name from the error, then retry.
        eid = pid = None
        for m in re.finditer(r"Player EntityID=(\d+) PlayerID=(\d+) GameAccountId=\[hi=(\d+) lo=(\d+)\]", log_text):
            e_id, p_id, hi, lo = map(int, m.groups())
            if hi or lo:
                eid, pid = e_id, p_id
                break
        friendly_name = str(e).split("player '")[-1].rstrip("'")
        parser.player_manager.create_or_update_player(name=friendly_name, entity_id=eid, player_id=pid)
        doc = HSReplayDocument.from_parser(parser, build=None)
    return _match_firestone_shape(doc.to_xml(), friendly_name)


def _match_firestone_shape(xml: str, friendly_name: str | None) -> str:
    # The coliseum viewer parses card ids with brittle, order-sensitive regexes, reads
    # gameType/formatType off <Game>, and expects <Player name=...>. hsreplay's output
    # differs for BG logs, so nudge it into Firestone's shape:
    # 1) ShowEntity: hsreplay emits entity="" cardID="", coliseum's regex wants
    #    cardID="" entity="" (else its .match returns null and the parser crashes).
    xml = re.sub(r'<ShowEntity entity="([^"]*)" cardID="([^"]*)"', r'<ShowEntity cardID="\2" entity="\1"', xml)
    # 2) hsreplay omits gameType/formatType; without them BG renders as a normal game.
    game_type = "23" if "TB_BaconShop" in xml else "7"  # 23 = GT_BATTLEGROUNDS
    fmt = "1" if game_type == "23" else "2"
    xml = xml.replace("<Game ", f'<Game gameType="{game_type}" formatType="{fmt}" ', 1)
    # 3) BG logs have no player names, so hsreplay's <Player> lacks name/isMainPlayer,
    #    which coliseum's initializePlayer dereferences. Inject them (friendly = the
    #    player with a non-zero account id).
    xml = _add_player_names(xml, friendly_name or "Player")
    # 4) coliseum's MAIN_READY parser does `entities.filter(CURRENT_PLAYER==1).first().playerId`
    #    and crashes when no entity is current. In BG recruit phases hsreplay leaves both
    #    players CURRENT_PLAYER=0, so keep the human player current throughout (Firestone
    #    never has an empty MAIN_READY). ponytail: heuristic, pins current player to the
    #    human; fine for a solo BG view.
    xml = _force_current_player(xml)
    # Drop the DOCTYPE (external DTD) so the shape matches Firestone's replay XML.
    return "\n".join(l for l in xml.splitlines() if not l.lstrip().startswith("<!DOCTYPE"))


def _force_current_player(xml: str) -> str:
    m = re.search(r'<Player id="(\d+)"[^>]*accountHi="(\d+)" accountLo="(\d+)"', xml)
    # fall back to first player if the human can't be identified
    main_id = None
    for pm in re.finditer(r'<Player id="(\d+)"[^>]*accountHi="(\d+)" accountLo="(\d+)"', xml):
        if pm.group(2) != "0" or pm.group(3) != "0":
            main_id = pm.group(1)
            break
    if main_id is None:
        return xml
    # start current
    xml = re.sub(rf'(<Player id="{main_id}"[^>]*>)', r'\1<Tag tag="23" value="1"/>', xml, count=1)
    # never drop to 0
    xml = re.sub(rf'(<TagChange entity="{main_id}" tag="23" value=)"0"', r'\g<1>"1"', xml)
    return xml


def _add_player_names(xml: str, friendly_name: str) -> str:
    def repl(m: "re.Match[str]") -> str:
        tag = m.group(0)
        if "name=" in tag:  # constructed logs already carry names
            return tag
        acct = re.search(r'accountHi="(\d+)" accountLo="(\d+)"', tag)
        friendly = bool(acct) and (acct.group(1) != "0" or acct.group(2) != "0")
        name = friendly_name if friendly else "Opponent"
        return tag[:-1] + f' name="{name}" isMainPlayer="{"true" if friendly else "false"}">'

    return re.sub(r"<Player [^>]*>", repl, xml)


def read_power_log(replay_path: str) -> str:
    # .hdtreplay = {output_log.txt}; .bgreplay = {meta.json, power.log}. Pick the log, not meta.json.
    with zipfile.ZipFile(replay_path) as z:
        names = z.namelist()
        name = (
            next((n for n in names if n.lower().endswith((".log", ".txt", ".xml"))), None)
            or next((n for n in names if not n.lower().endswith(".json")), None)
            or names[0]
        )
        return z.read(name).decode("utf-8", "replace")


REPLAY_EXTS = (".hdtreplay", ".bgreplay")


def convert_file(path: str, outdir: str | None = None) -> str | None:
    try:
        xml = convert_log(read_power_log(path))
    except Exception as e:  # skip corrupt/empty captures (BadZipFile etc.), keep going
        print(f"skip {os.path.basename(path)}: {type(e).__name__}: {str(e)[:60]}")
        return None
    if "<Game " not in xml:
        print(f"skip {os.path.basename(path)}: no game parsed")
        return None
    out_name = os.path.splitext(os.path.basename(path))[0] + ".xml"
    out_path = os.path.join(outdir or os.path.dirname(path) or ".", out_name)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(xml)
    print(f"wrote {out_path} ({len(xml):,} bytes)")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="file(s) or dir; default = HDT Replays + BGReplays folders")
    ap.add_argument("-o", "--outdir", help="output directory (default: beside each input)")
    args = ap.parse_args()

    base = os.path.expandvars(r"%APPDATA%\HearthstoneDeckTracker")
    roots = args.paths or [os.path.join(base, "Replays"), os.path.join(base, "BGReplays")]
    files: list[str] = []
    for p in roots:
        if os.path.isdir(p):
            files += [os.path.join(p, n) for n in sorted(os.listdir(p)) if n.lower().endswith(REPLAY_EXTS)]
        elif os.path.isfile(p):
            files.append(p)
    if not files:
        raise SystemExit("no .hdtreplay/.bgreplay files found")

    if args.outdir:
        os.makedirs(args.outdir, exist_ok=True)
    for f in files:
        convert_file(f, args.outdir)


if __name__ == "__main__":
    main()
