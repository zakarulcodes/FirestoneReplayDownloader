#!/usr/bin/env python3
"""Convert HDT .hdtreplay files to HSReplay XML for the /play viewer.

HDT stores replays as a zip containing the raw Hearthstone Power.log
(output_log.txt). The /play viewer needs HSReplay XML, so this converts one to
the other using HearthSim's log parser. Drop the resulting .xml into the /play
file picker.

Install once:  pip install hsreplay
Usage:
  python hdt_to_xml.py FILE.hdtreplay [more ...]   # -> FILE.xml beside each input
  python hdt_to_xml.py DIR                         # convert every .hdtreplay in DIR
  python hdt_to_xml.py                             # default: HDT's Replays folder
  python hdt_to_xml.py DIR -o OUTDIR               # write .xml files into OUTDIR
"""
import argparse
import io
import os
import zipfile

from hslog import LogParser
from hsreplay.document import HSReplayDocument


def convert_log(log_text: str) -> str:
    parser = LogParser()
    parser.read(io.StringIO(log_text))
    parser.flush()
    xml = HSReplayDocument.from_parser(parser, build=None).to_xml()
    # Drop the DOCTYPE (external DTD reference) so the output matches the shape of
    # Firestone's replay XML; the coliseum viewer doesn't want the DTD.
    return "\n".join(l for l in xml.splitlines() if not l.lstrip().startswith("<!DOCTYPE"))


def read_power_log(hdtreplay_path: str) -> str:
    with zipfile.ZipFile(hdtreplay_path) as z:
        name = next((n for n in z.namelist() if n.endswith(".txt")), z.namelist()[0])
        return z.read(name).decode("utf-8", "replace")


def convert_file(path: str, outdir: str | None = None) -> str:
    xml = convert_log(read_power_log(path))
    if "<Game " not in xml:
        raise SystemExit(f"no game parsed from {path!r} (empty or unsupported log)")
    out_name = os.path.splitext(os.path.basename(path))[0] + ".xml"
    out_path = os.path.join(outdir or os.path.dirname(path) or ".", out_name)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(xml)
    print(f"wrote {out_path} ({len(xml):,} bytes)")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="file(s) or dir; default = HDT Replays folder")
    ap.add_argument("-o", "--outdir", help="output directory (default: beside each input)")
    args = ap.parse_args()

    roots = args.paths or [os.path.expandvars(r"%APPDATA%\HearthstoneDeckTracker\Replays")]
    files: list[str] = []
    for p in roots:
        if os.path.isdir(p):
            files += [os.path.join(p, n) for n in sorted(os.listdir(p)) if n.lower().endswith(".hdtreplay")]
        else:
            files.append(p)
    if not files:
        raise SystemExit("no .hdtreplay files found")

    if args.outdir:
        os.makedirs(args.outdir, exist_ok=True)
    for f in files:
        convert_file(f, args.outdir)


if __name__ == "__main__":
    main()
